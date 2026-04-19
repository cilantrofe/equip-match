import asyncio
import json
import re
from collections import deque
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.scrapers.base import BaseHttpScraper

BASE = "https://akuvox-rus.ru"

ROOT_PAGES = [
    f"{BASE}/produkty/ip-vyzyvnye-paneli",
    f"{BASE}/produkty/ip-domofony",
]

ROOT_LABELS = {
    "/produkty/ip-vyzyvnye-paneli": "IP вызывные панели",
    "/produkty/ip-domofony": "IP домофоны",
}

CATEGORY_MAP = {
    "/produkty/ip-vyzyvnye-paneli": "Вызывная панель",
    "/produkty/ip-domofony": "Видеомонитор",
}

HEADERS_LIKE = {"h1", "h2", "h3", "h4", "h5", "h6"}
SPEC_HEADINGS = (
    "характеристик", "ключевые особенности", "основные характеристики",
    "технические характеристики", "функции", "возможности",
    "сфера применения", "установка и обслуживание",
)
PRICE_RE = re.compile(r"(?P<num>\d[\d\s\u00A0]*)\s*(?:₽|р\.?|руб\.?)", re.IGNORECASE)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE,
}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _norm_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def _is_internal(url: str) -> bool:
    return urlparse(url).netloc.lower() in {"akuvox-rus.ru", "www.akuvox-rus.ru", ""}


def _allowed_path(path: str) -> bool:
    path = path.rstrip("/")
    return any(path == r or path.startswith(r + "/") for r in ROOT_LABELS)


def _extract_jsonld_objects(soup: BeautifulSoup) -> list:
    out = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=True)
        try:
            data = json.loads(raw)
            out.extend(data if isinstance(data, list) else [data])
        except Exception:
            pass
    return out


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _has_product_jsonld(soup: BeautifulSoup) -> bool:
    for obj in _extract_jsonld_objects(soup):
        for d in _iter_dicts(obj):
            t = d.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(str(x).lower() == "product" for x in types if x):
                return True
    return False


def _extract_meta(soup: BeautifulSoup, key: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return _clean(tag["content"]) if tag and tag.get("content") else None


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.select_one("h1")
    if h1:
        title = _clean(h1.get_text(" ", strip=True))
        if title:
            return title
    return _extract_meta(soup, "og:title") or (
        _clean(soup.title.string) if soup.title and soup.title.string else None
    )


def _extract_price(soup: BeautifulSoup) -> Optional[float]:
    for sel in (
        '[itemprop="price"]', '[data-price]', ".price", ".product-price",
        ".product__price", ".woocommerce-Price-amount", ".price-block", ".prod-price",
    ):
        tag = soup.select_one(sel)
        if not tag:
            continue
        m = PRICE_RE.search(_clean(tag.get_text(" ", strip=True)))
        if m:
            try:
                v = float(m.group("num").replace(" ", "").replace("\u00a0", ""))
                return v if v > 0 else None
            except Exception:
                pass
        for attr in ("content", "data-price", "value"):
            raw = (tag.get(attr) or "").strip()
            if raw and re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
                v = float(raw.replace(",", "."))
                return v if v > 0 else None
    return None


def _extract_sku(soup: BeautifulSoup, url: str) -> Optional[str]:
    for obj in _extract_jsonld_objects(soup):
        for d in _iter_dicts(obj):
            sku = d.get("sku") or d.get("mpn") or d.get("productID")
            if sku:
                return _clean(str(sku))
    text = _clean((soup.select_one("main") or soup.body or soup).get_text("\n", strip=True))
    if any(t in text.lower() for t in ("артикул", "sku", "mpn")):
        for pat in (
            r"(?:артикул|sku|mpn)[:\s]*([A-Za-zА-Яа-я0-9\-_.\/]+)",
            r"\b(?:арт\.?)[:\s]*([A-Za-zА-Яа-я0-9\-_.\/]+)",
        ):
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return _clean(m.group(1))
    return urlparse(url).path.rstrip("/").split("/")[-1] or None


def _derive_category(soup: BeautifulSoup, url: str) -> Optional[str]:
    for sel in (".breadcrumb li", ".breadcrumbs li", "nav.breadcrumb li"):
        nodes = soup.select(sel)
        if nodes:
            crumbs = [_clean(n.get_text(" ", strip=True)) for n in nodes]
            crumbs = [c for c in crumbs if c and c.lower() not in {"главная", "home"}]
            if len(crumbs) >= 2:
                return " / ".join(crumbs[:-1])
    path = urlparse(url).path.rstrip("/")
    for root, category in CATEGORY_MAP.items():
        if path == root or path.startswith(root + "/"):
            return category
    return next((label for root, label in ROOT_LABELS.items()
                 if path == root or path.startswith(root + "/")), None)


def _looks_like_product(soup: BeautifulSoup, html: str, url: str) -> bool:
    text = _clean((soup.select_one("main") or soup.body or soup).get_text(" ", strip=True))
    score = 0
    if _has_product_jsonld(soup):
        score += 3
    if soup.select_one("h1"):
        score += 1
    if PRICE_RE.search(text):
        score += 2
    if any(k in text.lower() for k in ("основные характеристики", "технические характеристики")):
        score += 2
    if any(k in text.lower() for k in ("купить", "сообщить о наличии", "нет в наличии")):
        score += 1
    return score >= 4


def _extract_jsonld_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Only additionalProperty — product-level fields (sku, price, brand) go into Product."""
    pairs: list[tuple[str, str]] = []
    for obj in _extract_jsonld_objects(soup):
        for d in _iter_dicts(obj):
            if str(d.get("@type", "")).lower() != "product":
                continue
            for item in d.get("additionalProperty") or []:
                if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                    pairs.append((str(item["name"]), str(item["value"])))
    return pairs


def _extract_table_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [_clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                pairs.append((cells[0], cells[1]))
    return pairs


def _extract_dl_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            k, v = _clean(dt.get_text(" ", strip=True)), _clean(dd.get_text(" ", strip=True))
            if k and v:
                pairs.append((k, v))
    return pairs


def _extract_section_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for heading in soup.find_all(list(HEADERS_LIKE)):
        low = _clean(heading.get_text(" ", strip=True)).lower()
        if not any(k in low for k in SPEC_HEADINGS):
            continue
        chunks: list[str] = []
        for sib in heading.find_next_siblings():
            if getattr(sib, "name", None) in HEADERS_LIKE:
                break
            for tag in sib.find_all(["li", "p", "div", "span", "tr"], recursive=True):
                txt = _clean(tag.get_text(" ", strip=True))
                if txt and len(txt) >= 2:
                    chunks.append(txt)
        if chunks:
            pairs.append((_clean(heading.get_text(" ", strip=True)), "; ".join(chunks[:30])))
    return pairs


def _extract_kv_lines(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    main = soup.select_one("main") or soup.body
    if not main:
        return pairs
    for line in _clean(main.get_text("\n", strip=True)).split("\n"):
        line = _clean(line)
        if len(line) < 3 or ":" not in line:
            continue
        left, right = line.split(":", 1)
        left, right = _clean(left), _clean(right)
        if left and right and len(left) < 80:
            pairs.append((left, right))
    return pairs


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class AkuvoxRusScraper(BaseHttpScraper):
    source_name = "Akuvox Rus"
    source_url = BASE
    request_delay = 1.0
    concurrency = 3
    default_headers = DEFAULT_HEADERS

    async def collect_links(self, session: aiohttp.ClientSession) -> set[str]:
        self._log.info("Crawling %d root pages", len(ROOT_PAGES))
        queue = deque(_norm_url(u) for u in ROOT_PAGES)
        visited: set[str] = set()
        product_links: set[str] = set()
        while queue:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            self._log.debug("Visiting (%d visited): %s", len(visited), url)
            status, html = await self.fetch(session, url)
            if status != 200 or not html:
                self._log.debug("HTTP %s — skipping catalog page: %s", status, url)
                continue
            soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
            if _looks_like_product(soup, html, url):
                product_links.add(url)
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                    continue
                full = _norm_url(urljoin(BASE, href))
                if _is_internal(full) and _allowed_path(urlparse(full).path) and full not in visited:
                    queue.append(full)
        self._log.info("Crawl finished — visited %d pages, found %d product links",
                       len(visited), len(product_links))
        return product_links

    def parse_page(
        self, soup: BeautifulSoup, html: str, url: str
    ) -> Optional[tuple[dict, list[tuple[str, str]]]]:
        if not _looks_like_product(soup, html, url):
            self._log.debug("Score < 4, not a product page: %s", url)
            return None
        title = _extract_title(soup)
        if not title:
            self._log.debug("No title found: %s", url)
            return None

        product_data = {
            "source_sku": _extract_sku(soup, url) or title or url,
            "brand": "Akuvox",
            "model": title,
            "category": _derive_category(soup, url),
            "price": _extract_price(soup),
            "currency": "RUB",
            "url": url,
        }

        pairs: list[tuple[str, str]] = []
        pairs.extend(_extract_jsonld_specs(soup))
        pairs.extend(_extract_table_specs(soup))
        pairs.extend(_extract_dl_specs(soup))
        pairs.extend(_extract_section_specs(soup))
        pairs.extend(_extract_kv_lines(soup))

        return product_data, pairs


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(AkuvoxRusScraper().run())
