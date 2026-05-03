"""Скрапер сайта hikvisionpro.ru.

Обходит разделы видеодомофонов и вызывных панелей Hikvision/HiWatch,
извлекает характеристики из таблиц, CSS-блоков со спецификациями и
`<dl>`. Определяет бренд (Hikvision или HiWatch) по заголовку страницы.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.scrapers.base import (
    CHROME_UA,
    BaseHttpScraper,
    _clean,
    _extract_dl_specs,
    _extract_table_specs,
)

BASE = "https://hikvisionpro.ru"

CATALOG_CATEGORIES: dict[str, str] = {
    "/catalog/videodomofony-hikvision/ip-videodomofony-hikvision/": "Видеомонитор",
    "/catalog/videodomofony-hikvision/ip-vyzyvnye-paneli-hikvision/": "Вызывная панель",
    "/catalog/produktsiya-hiwatch/videodomofony-hiwatch/": "Вызывная панель",
}

DEFAULT_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE,
}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _is_product_anchor(a_tag) -> bool:
    """Проверить, ведёт ли ссылка на страницу товара (`/catalog/element/`)."""
    href = a_tag.get("href") or ""
    if not href or href.startswith(("javascript:", "mailto:")):
        return False
    return "/catalog/element/" in href.lower()


def _extract_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Извлечь характеристики: таблицы → CSS-блоки спецификаций → `<dl>`."""
    pairs = _extract_table_specs(soup)

    for sc in soup.select(
        "[class*='spec'], [class*='характер'], .product-specs, "
        ".specifications, .params, .props"
    ):
        for item in sc.find_all(["li", "div"]):
            txt = _clean(item.get_text(" ", strip=True))
            if ":" in txt:
                k, v = [x.strip() for x in txt.split(":", 1)]
                if k and v:
                    pairs.append((k, v))

    if not pairs:
        pairs = _extract_dl_specs(soup)

    return pairs


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


class HikvisionProScraper(BaseHttpScraper):
    """Скрапер hikvisionpro.ru: видеомониторы и вызывные панели Hikvision/HiWatch."""

    source_name = "HikvisionPro"
    source_url = BASE
    request_delay = 0.4
    concurrency = 6
    retries = 2
    default_headers = DEFAULT_HEADERS

    def __init__(self, *args, **kwargs) -> None:
        """Инициализировать скрапер и словарь соответствий URL → категория."""
        super().__init__(*args, **kwargs)
        self._url_category: dict[str, str] = {}

    async def _collect_from_catalog(
        self,
        session: aiohttp.ClientSession,
        catalog_path: str,
        category: str,
        seen: set[str],
    ) -> None:
        """Постранично обойти каталог и собрать ссылки на товары в `seen`."""
        catalog_url = urljoin(BASE, catalog_path)
        page = 1
        while True:
            url = catalog_url if page == 1 else f"{catalog_url}?PAGEN_1={page}"
            self._log.info("Fetching [%s] page %d: %s", category, page, url)
            status, html = await self.fetch(session, url)
            if status != 200 or not html:
                self._log.error(
                    "Failed to fetch %s page %d (HTTP %s)", category, page, status
                )
                break

            soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
            before = len(seen)
            for a in soup.find_all("a", href=True):
                if not _is_product_anchor(a):
                    continue
                full = urljoin(BASE, a["href"]).split("#")[0].rstrip("/")
                if urlparse(full).netloc != urlparse(BASE).netloc:
                    continue
                if full not in seen:
                    seen.add(full)
                    self._url_category[full] = category

            if len(seen) == before:
                break
            page += 1

    async def collect_links(self, session: aiohttp.ClientSession) -> set[str]:
        """Собрать ссылки на товары из всех разделов каталога."""
        seen: set[str] = set()
        for path, category in CATALOG_CATEGORIES.items():
            await self._collect_from_catalog(session, path, category, seen)
        self._log.info("Found %d product links total across all catalogs", len(seen))
        return seen

    def parse_page(
        self,
        soup: BeautifulSoup,
        _html: str,
        url: str,
    ) -> Optional[tuple[dict, list[tuple[str, str]]]]:
        """Разобрать страницу товара Hikvision/HiWatch и вернуть данные + характеристики."""
        h1 = soup.find("h1")
        title = _clean(h1.get_text(" ", strip=True)) if h1 else None

        sku: Optional[str] = None
        # Ищем только внутри основного блока товара, чтобы не захватить попапы.
        main = (
            soup.select_one(
                ".catalog-element-offer, .product-detail, .detail-block, "
                "#catalog_element, .catalog-element"
            )
            or soup.find("article")
            or soup
        )
        sku_sel = main.select_one(
            ".sku, .product-code, .artikul, .catalog-article, .catalog-element-article"
        )
        if sku_sel:
            raw = _clean(sku_sel.get_text(" ", strip=True))
            raw = re.sub(r"^(Артикул|Арт|SKU|Код)[.:\s]+", "", raw, flags=re.I).strip()
            if raw and len(raw) < 80:
                sku = raw
        if not sku:
            txt = soup.find(string=re.compile(r"Артикул|SKU|EAN", re.I))
            if txt:
                m = re.search(r"[:\s]\s*([A-Za-z0-9][\w\-\/\.]{2,})", txt)
                if m:
                    sku = m.group(1).strip()
        if not sku:
            slug = url.rstrip("/").split("/")[-1]
            slug = re.sub(r"\.html?$", "", slug, flags=re.I).upper()
            if slug:
                sku = slug

        price: Optional[float] = None
        price_sel = soup.select_one(
            ".price, .product-price, .price-current, .value_price, .price__value"
        )
        if price_sel:
            txt = price_sel.get_text(" ", strip=True).replace("\xa0", " ")
            m = re.search(r"([\d\s]+(?:[.,]\d+)?)\s*₽?", txt)
            if m:
                try:
                    price = float(m.group(1).replace(" ", "").replace(",", "."))
                except Exception:
                    pass

        category = self._url_category.get(url)

        brand: Optional[str] = None
        if title:
            tl = title.lower()
            brand = "HiWatch" if (tl.startswith("hiwatch") or "hiwatch" in tl) else "Hikvision"

        product_data = {
            "source_sku": sku or title or url,
            "brand": brand,
            "model": title,
            "category": category,
            "price": price,
            "currency": self.default_currency if price else None,
            "url": url,
        }

        pairs = _extract_specs(soup)

        if not pairs:
            desc_sel = soup.select_one(
                ".product-description, .description, .desc, .short-description, .text"
            )
            if desc_sel:
                for ln in desc_sel.get_text(" ", strip=True).splitlines():
                    ln = _clean(ln)
                    if ":" in ln:
                        k, v = [x.strip() for x in ln.split(":", 1)]
                        if k and v:
                            pairs.append((k, v))

        return product_data, pairs


if __name__ == "__main__":
    HikvisionProScraper.run_standalone()
