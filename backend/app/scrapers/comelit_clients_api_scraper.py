# backend/app/scrapers/comelit_clients_api_scraper.py
import asyncio
import aiohttp
from typing import Any, Dict, Iterable

from app.db.session import async_session
from app.db.crud import create_source_if_missing, upsert_product, add_spec
from app.normalization.normalizer import parse_number_and_unit, normalize_spec_name

BASE_API = "https://comelitrussia.clients.site/api/get-products?slug=comelitrussia&new=1"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json, text/plain, */*",
}
PERMALINK = "177598775019"

MAIN_KEYS = {
    "name", "title", "article", "sku", "id", "permalink", "price",
    "price_raw", "currency", "category", "images", "image", "description",
    "slug", "url", "link", "cost", "costdata", "costData", "isAvailable",
    "isDeleted", "source", "categories"
}

async def fetch_page(session: aiohttp.ClientSession, limit: int, offset: int) -> Dict[str, Any]:
    payload = {"permalink": PERMALINK, "limit": limit, "offset": offset, "slug": "comelitrussia"}
    async with session.post(BASE_API, json=payload, timeout=60) as resp:
        resp.raise_for_status()
        j = await resp.json()
        # robust extraction: try several keys
        items = j.get("result") or j.get("data") or j.get("items") or j.get("products") or []
        total = j.get("total") or j.get("count") or None
        return {"items": items, "total": total, "raw": j}

def iter_key_values(obj) -> Iterable[tuple]:
    if obj is None:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
    elif isinstance(obj, list):
        for el in obj:
            if isinstance(el, dict):
                # common patterns
                if "name" in el and ("value" in el or "val" in el):
                    yield el.get("name"), el.get("value") or el.get("val")
                elif "key" in el and ("value" in el or "val" in el):
                    yield el.get("key"), el.get("value") or el.get("val")
                else:
                    for kk, vv in el.items():
                        yield kk, vv
            else:
                # plain element: skip
                continue
    else:
        return

async def save_product_and_specs(session_db, source_id: int, product_json: Dict[str, Any]):
    # map main fields
    name_obj = product_json.get("name") or {}
    if isinstance(name_obj, dict):
        name = name_obj.get("origin") or name_obj.get("translated") or None
    else:
        name = str(name_obj) if name_obj else None

    sku = product_json.get("id") or product_json.get("article") or product_json.get("permalink") or None
    price = product_json.get("costData") or product_json.get("cost") or product_json.get("price") or None
    # price might be string "13 990 ₽" or numeric costData (int)
    if isinstance(price, str):
        # try to parse numeric
        try:
            price_num = int("".join(ch for ch in price if ch.isdigit()))
            price = float(price_num)
        except Exception:
            price = None
    elif isinstance(price, (int, float)):
        price = float(price)
    else:
        price = None

    # category: take first category.origin if exists
    category = None
    cats = product_json.get("categories")
    if isinstance(cats, list) and cats:
        first = cats[0]
        if isinstance(first, dict):
            category = first.get("origin") or first.get("translated")

    description = product_json.get("description") or None
    # image field may be image.templateUrl
    image = None
    img_obj = product_json.get("image") or product_json.get("images")
    if isinstance(img_obj, dict):
        image = img_obj.get("templateUrl") or img_obj.get("url") or None
    elif isinstance(img_obj, list):
        # take first
        if img_obj:
            image = img_obj[0]
    elif isinstance(img_obj, str):
        image = img_obj

    # try to build public url (optional)
    public_url = None
    if sku:
        public_url = f"https://comelitrussia.clients.site/?permalink={sku}"

    product_data = {
        "source_id": source_id,
        "source_sku": str(sku) if sku is not None else (name or public_url),
        "brand": "Comelit (clients)",
        "model": name or None,
        "category": category,
        "price": price,
        "currency": "RUB" if price else None,
        "url": public_url,
        "raw_html": None,
    }

    p = await upsert_product(session_db, product_data)

    # save other fields as specs (skip MAIN_KEYS)
    for k, v in product_json.items():
        if not k:
            continue
        kl = k.lower()
        if kl in MAIN_KEYS:
            continue
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            for subk, subv in iter_key_values(v):
                if not subk:
                    continue
                spec_name = normalize_spec_name(str(subk))
                if isinstance(subv, (dict, list)):
                    sval = str(subv)
                    num, unit = parse_number_and_unit(sval)
                    if num is not None:
                        await add_spec(session_db, p.id, spec_name, None, num, unit)
                    else:
                        await add_spec(session_db, p.id, spec_name, sval or None, None, None)
                else:
                    sval = str(subv).strip()
                    num, unit = parse_number_and_unit(sval)
                    if num is not None:
                        await add_spec(session_db, p.id, spec_name, None, num, unit)
                    else:
                        await add_spec(session_db, p.id, spec_name, sval or None, None, None)
        else:
            spec_name = normalize_spec_name(str(k))
            sval = str(v).strip()
            num, unit = parse_number_and_unit(sval)
            if num is not None:
                await add_spec(session_db, p.id, spec_name, None, num, unit)
            else:
                await add_spec(session_db, p.id, spec_name, sval or None, None, None)

    # add images and description explicitly
    if image:
        await add_spec(session_db, p.id, normalize_spec_name("image"), image, None, None)
    if description:
        # strip/trim
        await add_spec(session_db, p.id, normalize_spec_name("description"), description.strip(), None, None)

    return p.id

async def run_scraper(limit: int = 50):
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        offset = 0
        total_saved = 0

        async with async_session() as db_sess:
            src = await create_source_if_missing(db_sess, "Comelit Clients Site API", "https://comelitrussia.clients.site")
            source_id = src.id

        while True:
            page = await fetch_page(session, limit, offset)
            items = page.get("items", [])
            raw = page.get("raw", {})
            total = page.get("total") or raw.get("total") or None

            if not items:
                print("No items returned, stopping. offset=", offset)
                break

            print(f"Fetched {len(items)} items (offset={offset}) total={total}")

            for it in items:
                try:
                    async with async_session() as db_sess:
                        pid = await save_product_and_specs(db_sess, source_id, it)
                        total_saved += 1
                        print(" saved:", pid, it.get("name") and (it["name"].get("origin") if isinstance(it["name"], dict) else it["name"]))
                except Exception as e:
                    print(" error saving item:", e)

            offset += limit
            # stop if total known and offset >= total
            if total is not None and offset >= int(total):
                break

        print("=== DONE ===")
        print("Total saved:", total_saved)

if __name__ == "__main__":
    asyncio.run(run_scraper(limit=50))
