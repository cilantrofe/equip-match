import asyncio
from typing import Any, Dict, Iterable

import aiohttp

from app.db.session import async_session
from app.scrapers.base import ALLOWED_CATEGORIES, BaseScraper

BASE_API = "https://comelitrussia.clients.site/api/get-products?slug=comelitrussia&new=1"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json, text/plain, */*",
}
PERMALINK = "177598775019"

# Top-level keys that map to Product fields — not specs
_PRODUCT_LEVEL_KEYS = {
    "name", "title", "article", "sku", "id", "permalink",
    "price", "price_raw", "currency", "category", "categories",
    "images", "image", "description", "slug", "url", "link",
    "cost", "costdata", "costData", "isAvailable", "isDeleted", "source",
}


async def fetch_page(session: aiohttp.ClientSession, limit: int, offset: int) -> Dict[str, Any]:
    payload = {"permalink": PERMALINK, "limit": limit, "offset": offset, "slug": "comelitrussia"}
    async with session.post(BASE_API, json=payload, timeout=60) as resp:
        resp.raise_for_status()
        j = await resp.json(content_type=None, encoding="utf-8")
    items = j.get("result") or j.get("data") or j.get("items") or j.get("products") or []
    total = j.get("total") or j.get("count") or None
    return {"items": items, "total": total, "raw": j}


def _iter_kv(obj) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        yield from obj.items()
    elif isinstance(obj, list):
        for el in obj:
            if isinstance(el, dict):
                if "name" in el and ("value" in el or "val" in el):
                    yield el.get("name"), el.get("value") or el.get("val")
                elif "key" in el and ("value" in el or "val" in el):
                    yield el.get("key"), el.get("value") or el.get("val")
                else:
                    yield from el.items()


def _build_spec_pairs(product_json: Dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for k, v in product_json.items():
        if not k or k in _PRODUCT_LEVEL_KEYS or v is None:
            continue
        if isinstance(v, (dict, list)):
            for subk, subv in _iter_kv(v):
                if subk:
                    pairs.append((str(subk), str(subv) if subv is not None else ""))
        else:
            pairs.append((str(k), str(v).strip()))
    return pairs


class ComelitClientsScraper(BaseScraper):
    source_name = "Comelit Clients Site API"
    source_url = "https://comelitrussia.clients.site"

    async def _save_item(self, session_db, source_id: int, item: Dict[str, Any]) -> None:
        name_obj = item.get("name") or {}
        name = (name_obj.get("origin") or name_obj.get("translated")
                if isinstance(name_obj, dict) else str(name_obj) if name_obj else None)

        sku = item.get("id") or item.get("article") or item.get("permalink") or None

        raw_price = item.get("costData") or item.get("cost") or item.get("price")
        price = None
        if isinstance(raw_price, str):
            try:
                price = float("".join(ch for ch in raw_price if ch.isdigit()))
            except Exception:
                pass
        elif isinstance(raw_price, (int, float)):
            price = float(raw_price)

        category = None
        cats = item.get("categories")
        if isinstance(cats, list) and cats:
            first = cats[0]
            if isinstance(first, dict):
                category = first.get("origin") or first.get("translated")

        product_data = {
            "source_id": source_id,
            "source_sku": str(sku) if sku is not None else (name or f"comelit-{id(item)}"),
            "brand": "Comelit",
            "model": name or None,
            "category": category,
            "price": price,
            "currency": "RUB" if price else None,
            "url": f"https://comelitrussia.clients.site/?permalink={sku}" if sku else None,
        }

        if product_data.get("category") not in ALLOWED_CATEGORIES:
            self._log.debug("Category %r not allowed — skipping: %s", product_data.get("category"), sku)
            return
        product = await self.save_product(session_db, product_data)
        pairs = _build_spec_pairs(item)
        await self.save_specs(session_db, product.id, pairs)

    async def run(self, limit: int = 50) -> None:
        self._log.info("Starting — %s", BASE_API)
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with async_session() as db_sess:
                source_id = await self.get_or_create_source(db_sess)

            offset = 0
            total_saved = 0
            while True:
                self._log.info("Fetching page offset=%d limit=%d", offset, limit)
                page = await fetch_page(session, limit, offset)
                items = page.get("items", [])
                total = page.get("total") or page.get("raw", {}).get("total") or None

                if not items:
                    self._log.info("No items on page — stopping")
                    break

                self._log.info("Got %d items (total=%s)", len(items), total)
                for item in items:
                    try:
                        async with async_session() as db_sess:
                            await self._save_item(db_sess, source_id, item)
                        total_saved += 1
                    except Exception:
                        self._log.exception("Failed to save item: %s", item.get("id") or item.get("name"))

                offset += limit
                if total is not None and offset >= int(total):
                    break

        self._log.info("Done — total saved: %d", total_saved)


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(ComelitClientsScraper().run(limit=50))
