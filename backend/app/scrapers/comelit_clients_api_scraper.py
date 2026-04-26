"""Скрапер API сайта comelitrussia.clients.site.

Получает товары постранично через POST-запросы к JSON API,
фильтрует по разрешённым категориям и сохраняет в БД.
"""

from __future__ import annotations

from typing import Any, Iterable

import aiohttp

from app.db.session import async_session
from app.scrapers.base import BaseScraper

BASE_API = (
    "https://comelitrussia.clients.site/api/get-products?slug=comelitrussia&new=1"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json, text/plain, */*",
}

PERMALINK = "177598775019"

# Поля верхнего уровня объекта товара — они идут в Product, а не в характеристики.
_PRODUCT_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "title",
        "article",
        "sku",
        "id",
        "permalink",
        "price",
        "price_raw",
        "currency",
        "category",
        "categories",
        "images",
        "image",
        "description",
        "slug",
        "url",
        "link",
        "cost",
        "costdata",
        "costData",
        "isAvailable",
        "isDeleted",
        "source",
    }
)


async def fetch_page(
    session: aiohttp.ClientSession,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Запросить одну страницу товаров у API Comelit.

    Возвращает словарь с ключами `items`, `total` и `raw` (сырой ответ).
    """
    payload = {
        "permalink": PERMALINK,
        "limit": limit,
        "offset": offset,
        "slug": "comelitrussia",
    }
    async with session.post(BASE_API, json=payload, timeout=60) as resp:
        resp.raise_for_status()
        j = await resp.json(content_type=None, encoding="utf-8")
    items = (
        j.get("result") or j.get("data") or j.get("items") or j.get("products") or []
    )
    total = j.get("total") or j.get("count") or None
    return {"items": items, "total": total, "raw": j}


def _iter_kv(obj: Any) -> Iterable[tuple[str, Any]]:
    """Обойти dict или list[dict] и выдать пары (ключ, значение)."""
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


def _build_spec_pairs(product_json: dict[str, Any]) -> list[tuple[str, str]]:
    """Собрать пары (название, значение) из полей объекта товара, исключая служебные."""
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
    """Скрапер comelitrussia.clients.site: получает товары через JSON API."""

    source_name = "Comelit Clients Site API"
    source_url = "https://comelitrussia.clients.site"
    source_brand = "Comelit"

    async def _save_item(
        self,
        session_db: Any,
        source_id: int,
        item: dict[str, Any],
    ) -> None:
        """Разобрать объект товара из API и сохранить в БД."""
        name_obj = item.get("name") or {}
        name: str | None = (
            name_obj.get("origin") or name_obj.get("translated")
            if isinstance(name_obj, dict)
            else str(name_obj) if name_obj
            else None
        )

        sku = item.get("id") or item.get("article") or item.get("permalink") or None

        raw_price = item.get("costData") or item.get("cost") or item.get("price")
        price: float | None = None
        if isinstance(raw_price, str):
            try:
                price = float("".join(ch for ch in raw_price if ch.isdigit()))
            except Exception:
                pass
        elif isinstance(raw_price, (int, float)):
            price = float(raw_price)

        category: str | None = None
        cats = item.get("categories")
        if isinstance(cats, list) and cats:
            first = cats[0]
            if isinstance(first, dict):
                category = first.get("origin") or first.get("translated")

        product_data = {
            "source_id": source_id,
            "source_sku": (
                str(sku) if sku is not None else (name or f"comelit-{id(item)}")
            ),
            "brand": self.source_brand,
            "model": name.strip() if name else None,
            "category": category,
            "price": price,
            "currency": self.default_currency if price else None,
            "url": (
                f"https://comelitrussia.clients.site/?permalink={sku}" if sku else None
            ),
        }

        if not self._is_allowed_category(product_data.get("category")):
            self._log.debug(
                "Category %r not allowed — skipping: %s",
                product_data.get("category"),
                sku,
            )
            return

        product = await self.save_product(session_db, product_data)
        pairs = _build_spec_pairs(item)
        await self.save_specs(session_db, product.id, pairs)

    async def run(self, limit: int = 50) -> None:
        """Получить все страницы API и сохранить товары в БД."""
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
                        self._log.exception(
                            "Failed to save item: %s",
                            item.get("id") or item.get("name"),
                        )

                offset += limit
                if total is not None and offset >= int(total):
                    break

        self._log.info("Done — total saved: %d", total_saved)


if __name__ == "__main__":
    ComelitClientsScraper.run_standalone(limit=50)
