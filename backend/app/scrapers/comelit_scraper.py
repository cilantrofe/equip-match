# backend/app/scrapers/comelit_api_scraper.py
"""
Scraper for Comelit Russia using their public JSON API.
Saves products and specs into DB via existing app.db.crud helpers.

Usage:
    python -m app.scrapers.comelit_api_scraper
"""

import asyncio
import random
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from app.db.session import async_session
from app.db.crud import create_source_if_missing, upsert_product, add_spec
from app.normalization.normalizer import parse_number_and_unit, normalize_spec_name

BASE_API_ROOT = "https://comelitrussia.clients.site/api/"
SOURCE_NAME = "Comelit Russia (API)"
SOURCE_BASE = "https://comelitrussia.com"

# tuning
REQUEST_DELAY = 0.2
CONCURRENCY = 6
RETRIES = 3
TIMEOUT = 30

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": SOURCE_BASE,
}


# --- HTTP helpers ---
async def fetch_json(session: aiohttp.ClientSession, url: str, params: Optional[Dict] = None, tries: int = RETRIES) -> Optional[Dict[str, Any]]:
    delay = 0.5
    for attempt in range(1, tries + 1):
        try:
            async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    text = await resp.text(errors="ignore")
                    print(f"[fetch_json] {resp.status} for {url} (attempt {attempt}) -> {text[:200]!r}")
                    if 500 <= resp.status < 600:
                        # server error -> retry
                        raise aiohttp.ClientError(f"Server {resp.status}")
                    return None
                data = await resp.json(content_type=None)
                return data
        except Exception as e:
            print(f"[fetch_json] error for {url} attempt {attempt}: {e}")
            if attempt < tries:
                await asyncio.sleep(delay * attempt + random.random())
            else:
                return None
    return None


# --- JSON helpers ---
def extract_list_from_response(resp_json: Dict[str, Any], candidate_keys: List[str]) -> List[Any]:
    """
    Try several common keys to extract array of items from API response.
    """
    if not resp_json:
        return []
    # direct list
    if isinstance(resp_json, list):
        return resp_json
    for k in candidate_keys:
        if k in resp_json and isinstance(resp_json[k], (list, tuple)):
            return list(resp_json[k])
    # sometimes payload under 'data' or 'result'
    for k in ("data", "result", "items", "products"):
        if k in resp_json and isinstance(resp_json[k], (list, tuple)):
            return list(resp_json[k])
    # fallback: try to find any list value in top-level
    for v in resp_json.values():
        if isinstance(v, (list, tuple)):
            return list(v)
    return []


# --- product parsing & saving ---
async def parse_and_save_product(session_db, product_raw: Dict[str, Any], source_id: int):
    """
    product_raw: JSON object describing product (structure can vary).
    This function is robust: tries to extract common fields by checking several keys.
    """
    # heuristics for common fields
    def get_first(keys, default=None):
        for k in keys:
            if k in product_raw and product_raw[k] not in (None, "", []):
                return product_raw[k]
        return default

    title = get_first(["title", "name", "product_name", "model", "headline"])
    url = get_first(["url", "link", "permalink", "product_url"]) or None
    # normalize relative URL
    if url and url.startswith("/"):
        url = urljoin(SOURCE_BASE, url)
    sku = get_first(["sku", "source_sku", "code", "article", "art", "ean"])
    price_raw = get_first(["price", "price_formatted", "regular_price", "sale_price", "cost", "price_value"])
    # price may be dict or numeric or string
    price = None
    try:
        if isinstance(price_raw, dict):
            # try common keys
            price = float(price_raw.get("value") or price_raw.get("amount") or 0)
        elif isinstance(price_raw, (int, float)):
            price = float(price_raw)
        elif isinstance(price_raw, str):
            import re
            m = re.search(r"([\d\s\u00A0]+)", price_raw.replace("\xa0", " "))
            if m:
                price = float(m.group(1).replace(" ", "").replace("\u00A0", ""))
    except Exception:
        price = None

    category = get_first(["category", "category_name", "cat", "parent_name"])

    product_data = {
        "source_id": source_id,
        "source_sku": sku or title or url,
        "brand": get_first(["brand", "manufacturer"]) or "Comelit",
        "model": title or None,
        "category": category,
        "price": price,
        "currency": get_first(["currency", "price_currency"]) or ("RUB" if price else None),
        "url": url or None,
        "raw_html": None,  # not applicable for API-based import; we can store JSON instead if desired
    }

    try:
        p = await upsert_product(session_db, product_data)
    except Exception as e:
        print("upsert_product error:", e, "product:", title)
        return

    # --- extract specs ---
    parsed_any = False

    # 1) if product_raw contains 'specs' or 'attributes' as dict/list
    specs_candidates = []
    for k in ("specs", "specifications", "attributes", "properties", "params", "characteristics", "features"):
        if k in product_raw:
            specs_candidates.append(product_raw[k])

    for sc in specs_candidates:
        if isinstance(sc, dict):
            for key, val in sc.items():
                if val is None:
                    continue
                key_s = normalize_spec_name(str(key))
                if isinstance(val, (list, dict)):
                    val_txt = str(val)
                    num, unit = None, None
                    try:
                        await add_spec(session_db, p.id, key_s, val_txt, None, None)
                    except Exception as e:
                        print("add_spec error (dict-list):", e, key_s)
                else:
                    val_txt = str(val).strip()
                    num, unit = parse_number_and_unit(val_txt)
                    try:
                        if num is not None:
                            await add_spec(session_db, p.id, key_s, None, num, unit)
                        else:
                            await add_spec(session_db, p.id, key_s, val_txt or None, None, None)
                    except Exception as e:
                        print("add_spec error (dict):", e, key_s)
                parsed_any = True
        elif isinstance(sc, list):
            # list of {"name":..., "value":...} or ["Key: Value", ...]
            for item in sc:
                if isinstance(item, dict):
                    key = item.get("name") or item.get("key") or item.get("title")
                    val = item.get("value") or item.get("val") or item.get("text")
                    if not key:
                        continue
                    key_s = normalize_spec_name(str(key))
                    val_txt = str(val).strip() if val is not None else None
                    num, unit = parse_number_and_unit(val_txt or "")
                    try:
                        if num is not None:
                            await add_spec(session_db, p.id, key_s, None, num, unit)
                        else:
                            await add_spec(session_db, p.id, key_s, val_txt or None, None, None)
                    except Exception as e:
                        print("add_spec error (list-dict):", e, key_s)
                    parsed_any = True
                elif isinstance(item, str):
                    if ":" in item:
                        key, val = [s.strip() for s in item.split(":", 1)]
                        key_s = normalize_spec_name(key)
                        num, unit = parse_number_and_unit(val)
                        try:
                            if num is not None:
                                await add_spec(session_db, p.id, key_s, None, num, unit)
                            else:
                                await add_spec(session_db, p.id, key_s, val or None, None, None)
                        except Exception as e:
                            print("add_spec error (list-str):", e, key_s)
                        parsed_any = True
                    else:
                        # store as generic info
                        try:
                            await add_spec(session_db, p.id, "info", item, None, None)
                        except Exception:
                            pass
                        parsed_any = True

    # 2) If product JSON contains HTML description, parse and extract key/value pairs
    if not parsed_any:
        # common description fields
        desc = None
        for dkey in ("description", "desc", "short_description", "text", "content", "full_description", "prod_description"):
            if dkey in product_raw and product_raw[dkey]:
                desc = product_raw[dkey]
                break
        if desc:
            # parse HTML for patterns like <li>Key: Value</li> or property blocks
            try:
                soup = BeautifulSoup(desc, "html.parser")
                # ul/li with colon
                for li in soup.find_all("li"):
                    txt = li.get_text(" ", strip=True)
                    if ":" in txt:
                        key, val = [s.strip() for s in txt.split(":", 1)]
                        key_s = normalize_spec_name(key)
                        num, unit = parse_number_and_unit(val)
                        try:
                            if num is not None:
                                await add_spec(session_db, p.id, key_s, None, num, unit)
                            else:
                                await add_spec(session_db, p.id, key_s, val or None, None, None)
                        except Exception as e:
                            print("add_spec error (desc-li):", e, key_s)
                        parsed_any = True
                # div.property or other two-column layout
                props = soup.select(".property, .param, .spec, .product-specs, .characteristics, .technical")
                for prop in props:
                    # try find key and value children
                    key_el = prop.select_one(".key, .name, .label, .uk-text-muted") or prop.find(["b", "strong"])
                    val_el = prop.select_one(".value, .val, .uk-text-bold, .description")
                    if key_el:
                        key = key_el.get_text(" ", strip=True)
                        val = val_el.get_text(" ", strip=True) if val_el else prop.get_text(" ", strip=True)
                        key_s = normalize_spec_name(key)
                        num, unit = parse_number_and_unit(val)
                        try:
                            if num is not None:
                                await add_spec(session_db, p.id, key_s, None, num, unit)
                            else:
                                await add_spec(session_db, p.id, key_s, val or None, None, None)
                        except Exception as e:
                            print("add_spec error (desc-prop):", e, key_s)
                        parsed_any = True
            except Exception as e:
                print("error parsing description HTML:", e)

    # if still nothing parsed, optionally add a fallback spec with JSON string
    if not parsed_any:
        try:
            # save minimal info to specs so product is not empty
            await add_spec(session_db, p.id, "raw", str(product_raw)[:200], None, None)
        except Exception:
            pass

    return


# --- collectors ---
async def collect_categories(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    url = urljoin(BASE_API_ROOT, "get-categories")
    # example param found earlier: permalink=177598775019&slug=comelitrussia&new=1
    params = {"slug": "comelitrussia", "new": 1}
    data = await fetch_json(session, url, params=params)
    if not data:
        return []
    cats = extract_list_from_response(data, ["categories", "data", "items"])
    return cats


async def collect_products_from_api(session: aiohttp.ClientSession, extra_params: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """
    Try general 'get-products' with simple params. If the API supports pagination,
    this function tries to collect all pages if data includes pagination info.
    """
    url = urljoin(BASE_API_ROOT, "get-products")
    params = {"slug": "comelitrussia", "new": 1}
    if extra_params:
        params.update(extra_params)

    collected = []
    page = 1
    while True:
        params["page"] = page
        data = await fetch_json(session, url, params=params)
        if not data:
            break
        items = extract_list_from_response(data, ["products", "items", "data"])
        if not items:
            # maybe 'data' contains 'items' sub-object
            if isinstance(data.get("data"), dict) and isinstance(data["data"].get("items"), list):
                items = data["data"]["items"]
        if not items:
            break
        collected.extend(items)
        # detect pagination end
        # common patterns: data.total_pages or data.last_page or data.pagination
        if isinstance(data, dict):
            total_pages = None
            for key in ("total_pages", "last_page", "pages"):
                if key in data and isinstance(data[key], int):
                    total_pages = data[key]
                    break
            if total_pages and page >= total_pages:
                break
        # safety: stop if page yields fewer items (maybe last)
        if len(items) < 1:
            break
        page += 1
        await asyncio.sleep(REQUEST_DELAY + random.random() * 0.2)
        # optional max pages protection
        if page > 200:
            break
    return collected


# --- main crawl ---
async def crawl_comelit_api():
    conn = aiohttp.TCPConnector(limit_per_host=10)
    async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, connector=conn) as session:
        print("Collecting categories via API...")
        categories = await collect_categories(session)
        print("Categories found:", len(categories))

        # collect products in two ways:
        # 1) try general /get-products (site-wide)
        print("Collecting products via get-products...")
        products = await collect_products_from_api(session)
        print("Products fetched from get-products:", len(products))

        # 2) optionally, iterate categories and call products by category permalink (if categories carry such param)
        for cat in categories:
            # try to find permalink/id in category object (perm, permalink, id)
            permalink = None
            for k in ("permalink", "id", "permalink_id", "permalink_id"):
                if k in cat:
                    permalink = cat[k]
                    break
            if not permalink:
                # try other keys
                permalink = cat.get("permalink") or cat.get("permalink_id") or cat.get("link") or cat.get("slug")
            if permalink:
                params = {"permalink": permalink, "slug": "comelitrussia", "new": 1}
                data = await fetch_json(session, urljoin(BASE_API_ROOT, "get-categories"), params=params)
                # sometimes get-categories returns items/products
                extra_items = extract_list_from_response(data or {}, ["products", "items", "data"])
                if extra_items:
                    products.extend(extra_items)
                # also try /get-products with permalink param
                p_extra = await collect_products_from_api(session, extra_params={"permalink": permalink})
                if p_extra:
                    products.extend(p_extra)
                await asyncio.sleep(REQUEST_DELAY + random.random() * 0.2)

        # dedupe products by id or url or slug
        seen = set()
        unique_products = []
        for pr in products:
            uid = None
            for k in ("id", "product_id", "slug", "permalink", "link", "url"):
                if k in pr and pr[k]:
                    uid = str(pr[k])
                    break
            if not uid:
                # fallback: try name
                uid = (pr.get("title") or pr.get("name") or "")[:120]
            if uid in seen:
                continue
            seen.add(uid)
            unique_products.append(pr)

        print("Unique products to process:", len(unique_products))

        # ensure source exists
        async with async_session() as db_sess:
            src = await create_source_if_missing(db_sess, SOURCE_NAME, SOURCE_BASE)
            source_id = src.id

        sem = asyncio.Semaphore(CONCURRENCY)
        counters = {"processed": 0, "saved": 0, "errors": 0}

        async def worker(pr_raw):
            async with sem:
                counters["processed"] += 1
                # perhaps pr_raw already is detailed; if not, try to fetch detailed endpoint (some APIs provide product details)
                # attempt: if pr_raw has 'id' or 'product_id', call get-product-details endpoint (not guaranteed)
                try:
                    async with async_session() as db_sess:
                        await parse_and_save_product(db_sess, pr_raw, source_id)
                        counters["saved"] += 1
                except Exception as e:
                    counters["errors"] += 1
                    print("worker error:", e)
                await asyncio.sleep(REQUEST_DELAY + random.random() * 0.2)

        tasks = [asyncio.create_task(worker(pr)) for pr in unique_products]
        # run in chunks to avoid too many tasks
        chunk = 100
        for i in range(0, len(tasks), chunk):
            batch = tasks[i : i + chunk]
            await asyncio.gather(*batch)
        print("=== SCRAPE SUMMARY ===")
        print("Collected categories:", len(categories))
        print("Collected (raw) products:", len(products))
        print("Unique products:", len(unique_products))
        print("Processed:", counters["processed"])
        print("Saved:", counters["saved"])
        print("Errors:", counters["errors"])


if __name__ == "__main__":
    asyncio.run(crawl_comelit_api())
