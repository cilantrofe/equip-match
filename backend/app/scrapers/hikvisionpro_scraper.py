import asyncio
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.scrapers.base import BaseHttpScraper

BASE = "https://hikvisionpro.ru"

CATALOG_CATEGORIES: dict[str, str] = {
    "/catalog/videodomofony-hikvision/ip-videodomofony-hikvision/": "Видеомонитор",
    "/catalog/videodomofony-hikvision/ip-vyzyvnye-paneli-hikvision/": "Вызывная панель",
    "/catalog/produktsiya-hiwatch/videodomofony-hiwatch/": "Вызывная панель",
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE,
}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return text.replace("\u00a0", " ").strip()


def _is_product_anchor(a_tag) -> bool:
    href = a_tag.get("href") or ""
    if not href or href.startswith(("javascript:", "mailto:")):
        return False
    # hikvisionpro.ru product pages are always under /catalog/element/
    return "/catalog/element/" in href.lower()


def _extract_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) >= 2:
                k = _clean(cells[0].get_text(" ", strip=True))
                v = _clean(cells[1].get_text(" ", strip=True))
                if k and v:
                    pairs.append((k, v))

    for sc in soup.select(
        "[class*='spec'], [class*='характер'], .product-specs, .specifications, .params, .props"
    ):
        for item in sc.find_all(["li", "div"]):
            txt = _clean(item.get_text(" ", strip=True))
            if ":" in txt:
                k, v = [x.strip() for x in txt.split(":", 1)]
                if k and v:
                    pairs.append((k, v))

    if not pairs:
        for dl in soup.find_all("dl"):
            for dt in dl.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                if not dd:
                    continue
                k = _clean(dt.get_text(" ", strip=True))
                v = _clean(dd.get_text(" ", strip=True))
                if k and v:
                    pairs.append((k, v))

    return pairs


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class HikvisionProScraper(BaseHttpScraper):
    source_name = "HikvisionPro"
    source_url = BASE
    request_delay = 0.4
    concurrency = 6
    retries = 2
    default_headers = DEFAULT_HEADERS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._url_category: dict[str, str] = {}

    async def _collect_from_catalog(
        self, session: aiohttp.ClientSession, catalog_path: str, category: str, seen: set[str]
    ) -> None:
        catalog_url = urljoin(BASE, catalog_path)
        page = 1
        while True:
            url = catalog_url if page == 1 else f"{catalog_url}?PAGEN_1={page}"
            self._log.info("Fetching [%s] page %d: %s", category, page, url)
            status, html = await self.fetch(session, url)
            if status != 200 or not html:
                self._log.error("Failed to fetch %s page %d (HTTP %s)", category, page, status)
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
        seen: set[str] = set()
        for path, category in CATALOG_CATEGORIES.items():
            await self._collect_from_catalog(session, path, category, seen)
        self._log.info("Found %d product links total across all catalogs", len(seen))
        return seen

    def parse_page(
        self, soup: BeautifulSoup, _html: str, url: str
    ) -> Optional[tuple[dict, list[tuple[str, str]]]]:
        h1 = soup.find("h1")
        title = _clean(h1.get_text(" ", strip=True)) if h1 else None

        sku = None
        # Search only inside the main product block to avoid quick-view popups for other products
        main = (
            soup.select_one(".catalog-element-offer, .product-detail, .detail-block, #catalog_element, .catalog-element")
            or soup.find("article")
            or soup
        )
        sku_sel = main.select_one(".sku, .product-code, .artikul, .catalog-article, .catalog-element-article")
        if sku_sel:
            raw = _clean(sku_sel.get_text(" ", strip=True))
            # strip label prefix like "Артикул: DS-KAD20"
            raw = re.sub(r"^(Артикул|Арт|SKU|Код)[.:\s]+", "", raw, flags=re.I).strip()
            if raw and len(raw) < 80:
                sku = raw
        if not sku:
            # look for "Артикул: XXXXX" in page text
            txt = soup.find(string=re.compile(r"Артикул|SKU|EAN", re.I))
            if txt:
                m = re.search(r"[:\s]\s*([A-Za-z0-9][\w\-\/\.]{2,})", txt)
                if m:
                    sku = m.group(1).strip()
        if not sku:
            # derive from URL slug as last resort: /catalog/element/ds-kad20.html -> DS-KAD20
            slug = url.rstrip("/").split("/")[-1]
            slug = re.sub(r"\.html?$", "", slug, flags=re.I).upper()
            if slug:
                sku = slug

        price = None
        price_sel = soup.select_one(".price, .product-price, .price-current, .value_price, .price__value")
        if price_sel:
            txt = price_sel.get_text(" ", strip=True).replace("\xa0", " ")
            m = re.search(r"([\d\s]+(?:[.,]\d+)?)\s*₽?", txt)
            if m:
                try:
                    price = float(m.group(1).replace(" ", "").replace(",", "."))
                except Exception:
                    pass

        category = self._url_category.get(url)

        brand = None
        if title:
            tl = title.lower()
            if tl.startswith("hiwatch") or "hiwatch" in tl:
                brand = "HiWatch"
            else:
                brand = "Hikvision"

        product_data = {
            "source_sku": sku or title or url,
            "brand": brand,
            "model": title,
            "category": category,
            "price": price,
            "currency": "RUB" if price else None,
            "url": url,
        }

        pairs = _extract_specs(soup)

        # Fallback: description lines as key: value
        if not pairs:
            desc_sel = soup.select_one(".product-description, .description, .desc, .short-description, .text")
            if desc_sel:
                for ln in desc_sel.get_text(" ", strip=True).splitlines():
                    ln = _clean(ln)
                    if ":" in ln:
                        k, v = [x.strip() for x in ln.split(":", 1)]
                        if k and v:
                            pairs.append((k, v))

        return product_data, pairs


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(HikvisionProScraper().run())
