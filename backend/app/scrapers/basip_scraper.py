import asyncio
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.scrapers.base import BaseHttpScraper

BASE = "https://bas-ip.ru"

_CATEGORY_MAP: dict[str, str] = {
    "access-control": "Контроль доступа",
    "accessories": "Аксессуары",
    "brackets": "Кронштейны",
    "intercoms": "Видеомонитор",
    "panels": "Вызывная панель",
    "racks": "Стойки",
    "software": "Программное обеспечение",
    "archive": "Архив",
}

_ALLOWED_CATALOG_SLUGS: frozenset[str] = frozenset({"intercoms", "panels"})


def _category_from_url(url: str) -> Optional[str]:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "catalog":
        return _CATEGORY_MAP.get(parts[1], parts[1].replace("-", " ").title())
    return None

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": urljoin(BASE, "/catalog"),
}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return text.replace("\u00a0", " ").strip()


def _extract_specs_from_container(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    container = soup.select_one(".specifications")
    if not container:
        return pairs
    for prop in container.select(".property"):
        k = _clean((prop.select_one(".uk-text-muted") or prop).get_text(strip=True))
        v = _clean((prop.select_one(".uk-text-bold") or prop).get_text(" ", strip=True))
        if k and v and k != v:
            pairs.append((k, v))
    return pairs


def _extract_specs_from_text_blocks(soup: BeautifulSoup) -> list[tuple[str, str]]:
    lines: list[str] = []

    heading_node = None
    for s in soup.find_all(string=True):
        try:
            if "технические характеристики" in s.strip().lower():
                heading_node = s.parent
                break
        except Exception:
            continue

    if heading_node:
        for sib in heading_node.find_next_siblings():
            if sib.name and sib.name.lower() in ("h2", "h3", "h4"):
                break
            text = sib.get_text(separator="\n", strip=True)
            if not text or any(k in text.lower() for k in ("файлы", "загрузки", "скачать")):
                break
            lines.extend(ln.strip() for ln in text.splitlines() if ln.strip())

    if not lines:
        block = soup.select_one(".product-description, .product-body, .product-info, .content")
        if block:
            lines = [ln.strip() for ln in block.get_text(separator="\n", strip=True).splitlines() if ln.strip()]

    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        k, v = lines[i], lines[i + 1] if i + 1 < len(lines) else ""
        if k and v:
            pairs.append((k, v))
        i += 2
    return pairs


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class BasIPScraper(BaseHttpScraper):
    source_name = "BAS-IP"
    source_url = BASE
    request_delay = 1.0
    concurrency = 2
    default_headers = DEFAULT_HEADERS

    async def collect_links(self, session: aiohttp.ClientSession) -> set[str]:
        self._log.info("Fetching catalog root: %s/catalog", BASE)
        status, html = await self.fetch(session, urljoin(BASE, "/catalog"))
        if status != 200 or not html:
            self._log.error("Failed to fetch catalog root (HTTP %s)", status)
            return set()

        soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
        product_links: set[str] = set()
        category_links: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("/catalog/") or href.rstrip("/").endswith("/catalog"):
                continue
            full = urljoin(BASE, href)
            parts = [p for p in urlparse(full).path.split("/") if p]
            if len(parts) >= 2 and parts[1] not in _ALLOWED_CATALOG_SLUGS:
                continue
            (product_links if len(parts) >= 3 else category_links).add(full)

        self._log.info("Root page: %d product links, %d category links to crawl",
                       len(product_links), len(category_links))

        visited: set[str] = set()
        to_visit = list(category_links)
        while to_visit:
            cat = to_visit.pop(0)
            if cat in visited:
                continue
            visited.add(cat)
            self._log.debug("Crawling category (%d left): %s", len(to_visit), cat)
            st, h = await self.fetch(session, cat)
            if st != 200 or not h:
                continue
            s = await asyncio.to_thread(BeautifulSoup, h, "html.parser")
            for a in s.find_all("a", href=True):
                href = a["href"]
                if not href.startswith("/catalog/"):
                    continue
                full = urljoin(BASE, href)
                parts = [x for x in urlparse(full).path.split("/") if x]
                if len(parts) >= 2 and parts[1] not in _ALLOWED_CATALOG_SLUGS:
                    continue
                if len(parts) >= 3:
                    product_links.add(full)
                elif full not in visited:
                    to_visit.append(full)

        self._log.info("Crawl finished — found %d product links", len(product_links))
        return product_links

    def parse_page(
        self, soup: BeautifulSoup, _html: str, url: str
    ) -> Optional[tuple[dict, list[tuple[str, str]]]]:
        has_specs = bool(soup.select_one(".specifications"))
        has_price = bool(
            soup.select_one(".price") or soup.select_one(".uk-text-bolder")
            or soup.select_one(".product__price")
        )
        has_ean = bool(soup.find(
            string=lambda s: s and any(k in s for k in ("EAN", "Артикул", "арт."))
        ))
        if not (has_specs or has_price or has_ean):
            return None

        title_tag = soup.select_one("h1")
        title = _clean(title_tag.get_text(strip=True)) if title_tag else None

        price = None
        for sel in (".price", ".product-price", ".price-block", ".product__price", ".prod-price"):
            tag = soup.select_one(sel)
            if tag:
                m = re.search(r"([\d\s\u00A0]+)\s*₽", tag.get_text(separator=" ", strip=True))
                if m:
                    price = float(m.group(1).replace("\u00a0", "").replace(" ", ""))
                break

        ean = None
        for cand in soup.find_all(string=lambda s: s and any(k in s.lower() for k in ("артикул", "ean", "арт"))):
            m = re.search(r"(?:EAN|Артикул|арт\.?)[:\s]*([0-9A-Za-z\-]+)", cand.strip(), re.I)
            if m:
                ean = m.group(1).strip()
                break

        category = None
        bc = soup.select(".breadcrumb li")
        if bc:
            try:
                category = bc[-1].get_text(strip=True)
            except Exception:
                pass
        if not category:
            category = _category_from_url(url)

        product_data = {
            "source_sku": ean or title or url,
            "brand": "BAS-IP",
            "model": title,
            "category": category,
            "price": price,
            "currency": "RUB",
            "url": url,
        }

        pairs = _extract_specs_from_container(soup) or _extract_specs_from_text_blocks(soup)
        return product_data, pairs


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(BasIPScraper().run())
