import asyncio
import json
import random
import re
from collections import deque
from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from sqlalchemy.exc import IntegrityError

from app.db.crud import add_spec, create_source_if_missing, upsert_product
from app.db.session import async_session
from app.normalization.normalizer import normalize_spec_name, parse_number_and_unit

BASE = "https://akuvox-rus.ru"

ROOT_PAGES = [
    f"{BASE}/produkty/ip-vyzyvnye-paneli",
    f"{BASE}/produkty/ip-domofony",
    f"{BASE}/produkty/kontrol-dostupa",
    f"{BASE}/produkty/ip-sip-videotelefony",
]

REQUEST_DELAY = 1.0
CONCURRENCY = 3
RETRIES = 3

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE,
    "Connection": "keep-alive",
}

ROOT_LABELS = {
    "/produkty/ip-vyzyvnye-paneli": "IP вызывные панели",
    "/produkty/ip-domofony": "IP домофоны",
    "/produkty/kontrol-dostupa": "Контроль доступа",
    "/produkty/ip-sip-videotelefony": "IP SIP видеотелефоны",
}

HEADERS_LIKE = {"h1", "h2", "h3", "h4", "h5", "h6"}
SPEC_HEADINGS = (
    "характеристик",
    "характеристика",
    "ключевые особенности",
    "основные характеристики",
    "функции",
    "возможности",
    "сфера применения",
    "установка и обслуживание",
    "технические характеристики",
    "описание",
)

PRICE_RE = re.compile(r"(?P<num>\d[\d\s\u00A0]*)\s*(?:₽|р\.?|руб\.?)", re.IGNORECASE)


def _norm_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def _is_internal(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc in {"akuvox-rus.ru", "www.akuvox-rus.ru", ""}
    except Exception:
        return False


def _allowed_path(path: str) -> bool:
    path = path.rstrip("/")
    return any(path == root or path.startswith(root + "/") for root in ROOT_LABELS.keys())


def _same_allowed_section(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return _allowed_path(path)


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_jsonld_objects(soup: BeautifulSoup) -> list:
    objs = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                objs.extend(data)
            else:
                objs.append(data)
        except Exception:
            continue
    return objs


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
            if isinstance(t, list):
                if any(str(x).lower() == "product" for x in t):
                    return True
            elif isinstance(t, str) and t.lower() == "product":
                return True
    return False


def _extract_meta(soup: BeautifulSoup, key: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    if tag and tag.get("content"):
        return _clean_text(tag["content"])
    return None


def _extract_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    selectors = [
        ".breadcrumb li",
        ".breadcrumbs li",
        "nav.breadcrumb li",
        "nav[aria-label='breadcrumb'] li",
        "ol.breadcrumb li",
        "ul.breadcrumb li",
    ]
    crumbs = []
    for sel in selectors:
        nodes = soup.select(sel)
        if nodes:
            for n in nodes:
                txt = _clean_text(n.get_text(" ", strip=True))
                if txt and txt.lower() not in {"главная", "home"}:
                    crumbs.append(txt)
            if crumbs:
                break

    out = []
    seen = set()
    for c in crumbs:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _derive_category(soup: BeautifulSoup, url: str) -> Optional[str]:
    crumbs = _extract_breadcrumbs(soup)
    if len(crumbs) >= 2:
        return " / ".join(crumbs[:-1])
    path = urlparse(url).path.rstrip("/")
    for root, label in ROOT_LABELS.items():
        if path == root or path.startswith(root + "/"):
            return label
    return None


def _extract_price(soup: BeautifulSoup) -> Optional[float]:
    for sel in [
        '[itemprop="price"]',
        '[data-price]',
        ".price",
        ".product-price",
        ".product__price",
        ".woocommerce-Price-amount",
        ".product-card__price",
        ".price-block",
        ".prod-price",
    ]:
        tag = soup.select_one(sel)
        if not tag:
            continue
        txt = _clean_text(tag.get_text(" ", strip=True))
        m = PRICE_RE.search(txt)
        if m:
            num = m.group("num").replace(" ", "").replace("\u00a0", "")
            try:
                return float(num)
            except Exception:
                pass

        for attr in ("content", "data-price", "data-product-price", "value"):
            if tag.get(attr):
                raw = _clean_text(tag.get(attr))
                if raw and re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
                    return float(raw.replace(",", "."))

    main = soup.select_one("main") or soup.body
    if main:
        text = _clean_text(main.get_text(" ", strip=True))
        m = PRICE_RE.search(text)
        if m:
            num = m.group("num").replace(" ", "").replace("\u00a0", "")
            try:
                return float(num)
            except Exception:
                pass
    return None


def _extract_sku(soup: BeautifulSoup, url: str) -> Optional[str]:
    for obj in _extract_jsonld_objects(soup):
        for d in _iter_dicts(obj):
            sku = d.get("sku") or d.get("mpn") or d.get("productID")
            if sku:
                return _clean_text(str(sku))

    text_candidates = []
    main = soup.select_one("main") or soup.body
    if main:
        text_candidates.append(_clean_text(main.get_text("\n", strip=True)))
    text_candidates.append(_clean_text(soup.get_text("\n", strip=True)))

    patterns = [
        r"(?:артикул|sku|mpn|код товара)[:\s]*([A-Za-zА-Яа-я0-9\-_.\/]+)",
        r"\b(?:art\.?|арт\.?)[:\s]*([A-Za-zА-Яа-я0-9\-_.\/]+)",
    ]
    for text in text_candidates:
        low = text.lower()
        if any(token in low for token in ("артикул", "sku", "mpn", "код товара", "арт.")):
            for pat in patterns:
                m = re.search(pat, text, flags=re.IGNORECASE)
                if m:
                    return _clean_text(m.group(1))
    path = urlparse(url).path.rstrip("/").split("/")[-1]
    return path or None


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.select_one("h1")
    if h1:
        title = _clean_text(h1.get_text(" ", strip=True))
        if title:
            return title
    og = _extract_meta(soup, "og:title")
    if og:
        return og
    title_tag = soup.title.string if soup.title and soup.title.string else None
    return _clean_text(title_tag) if title_tag else None


def _looks_like_product(soup: BeautifulSoup, html: str, url: str) -> bool:
    main = soup.select_one("main") or soup.body
    text = _clean_text(main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True))
    score = 0

    if _has_product_jsonld(soup):
        score += 3

    if soup.select_one("h1"):
        score += 1

    if PRICE_RE.search(text):
        score += 2

    if any(k in text.lower() for k in ("основные характеристики", "ключевые особенности", "технические характеристики")):
        score += 2

    if any(k in text.lower() for k in ("купить", "сообщить о наличии", "нет в наличии", "цена по запросу")):
        score += 1

    if len(soup.select("article")) <= 2 and len(soup.select(".product, .product-page, .product-card")) <= 3:
        score += 1

    return score >= 4


async def fetch_with_retries(session_http: aiohttp.ClientSession, url: str, tries: int = RETRIES):
    delay = 0.6
    for attempt in range(1, tries + 1):
        try:
            async with session_http.get(url, timeout=30, allow_redirects=True) as resp:
                text = await resp.text()
                return resp.status, text
        except Exception as e:
            if attempt < tries:
                await asyncio.sleep(delay * attempt + random.random())
            else:
                return None, None
    return None, None


async def collect_product_links(session_http: aiohttp.ClientSession) -> set[str]:
    queue = deque(_norm_url(u) for u in ROOT_PAGES)
    visited = set()
    product_links = set()

    while queue:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        status, html = await fetch_with_retries(session_http, url)
        if status != 200 or not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            continue

        if _looks_like_product(soup, html, url):
            product_links.add(url)

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue

            full = _norm_url(urljoin(BASE, href))
            if not _is_internal(full):
                continue

            path = urlparse(full).path
            if not _allowed_path(path):
                continue

            if full not in visited:
                queue.append(full)

    return product_links


def _collect_section_texts(section_node) -> list[str]:
    texts = []

    for li in section_node.find_all("li"):
        txt = _clean_text(li.get_text(" ", strip=True))
        if txt:
            texts.append(txt)

    for tag in section_node.find_all(["p", "div", "span"], recursive=True):
        txt = _clean_text(tag.get_text(" ", strip=True))
        if not txt:
            continue

        if len(txt) < 2:
            continue
        texts.append(txt)

    for tr in section_node.find_all("tr"):
        cells = [
            _clean_text(c.get_text(" ", strip=True))
            for c in tr.find_all(["th", "td"])
        ]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            texts.append(f"{cells[0]}: {cells[1]}")
        elif len(cells) == 1:
            texts.append(cells[0])

    out = []
    seen = set()
    for t in texts:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _extract_sectioned_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for heading in soup.find_all(list(HEADERS_LIKE)):
        heading_text = _clean_text(heading.get_text(" ", strip=True))
        low = heading_text.lower()
        if not any(k in low for k in SPEC_HEADINGS):
            continue

        chunks = []
        for sib in heading.find_next_siblings():
            if getattr(sib, "name", None) in HEADERS_LIKE:
                break
            txts = _collect_section_texts(sib)
            if txts:
                chunks.extend(txts)

        if chunks:
            value = "; ".join(chunks[:30])
            pairs.append((heading_text, value))

    return pairs


def _extract_global_table_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [
                _clean_text(c.get_text(" ", strip=True))
                for c in tr.find_all(["th", "td"])
            ]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                pairs.append((cells[0], cells[1]))
    return pairs


def _extract_definition_list_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            k = _clean_text(dt.get_text(" ", strip=True))
            v = _clean_text(dd.get_text(" ", strip=True))
            if k and v:
                pairs.append((k, v))
    return pairs


def _extract_jsonld_specs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for obj in _extract_jsonld_objects(soup):
        for d in _iter_dicts(obj):
            if isinstance(d.get("@type"), str) and d.get("@type", "").lower() == "product":
                sku = d.get("sku") or d.get("mpn") or d.get("productID")
                if sku:
                    pairs.append(("SKU", str(sku)))

                brand = d.get("brand")
                if isinstance(brand, dict) and brand.get("name"):
                    pairs.append(("Бренд", str(brand["name"])))

                offers = d.get("offers")
                if isinstance(offers, dict):
                    price = offers.get("price")
                    currency = offers.get("priceCurrency")
                    if price is not None:
                        pairs.append(("Цена", str(price)))
                    if currency:
                        pairs.append(("Валюта", str(currency)))

                additional = d.get("additionalProperty")
                if isinstance(additional, list):
                    for item in additional:
                        if isinstance(item, dict):
                            name = item.get("name")
                            value = item.get("value")
                            if name and value is not None:
                                pairs.append((str(name), str(value)))
    return pairs


def _extract_kv_lines(soup: BeautifulSoup) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    main = soup.select_one("main") or soup.body
    if not main:
        return pairs

    text = _clean_text(main.get_text("\n", strip=True))

    for raw_line in text.split("\n"):
        line = _clean_text(raw_line)
        if not line or len(line) < 3:
            continue
        if ":" in line:
            left, right = line.split(":", 1)
            left = _clean_text(left)
            right = _clean_text(right)
            if left and right and len(left) < 80:
                pairs.append((left, right))
    return pairs


def _merge_spec_pairs(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    merged = {}
    order = []

    for k, v in pairs:
        k = _clean_text(k)
        v = _clean_text(v)
        if not k or not v:
            continue

        key = normalize_spec_name(k)
        compound = f"{key}::{v}".lower()
        if compound in merged:
            continue

        merged[compound] = (key, v)
        order.append(compound)

    return [merged[c] for c in order]


async def _save_specs(session_db, product_id: int, specs: list[tuple[str, str]]):
    seen = set()
    for name, value in specs:
        spec_name = normalize_spec_name(name)
        value = _clean_text(value)
        if not spec_name or not value:
            continue

        dedupe_key = (spec_name.lower(), value.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        num, unit = parse_number_and_unit(value)
        try:
            if num is not None:
                await add_spec(session_db, product_id, spec_name, None, num, unit)
            else:
                await add_spec(session_db, product_id, spec_name, value, None, None)
        except Exception as e:
            print(f"add_spec error: {product_id=} {spec_name=} {e}")


async def parse_product_page_and_save(html: str, url: str, source_id: int):
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return

    title = _extract_title(soup)
    if not title:
        return

    price = _extract_price(soup)
    sku = _extract_sku(soup, url)
    category = _derive_category(soup, url)
    description = _extract_meta(soup, "description") or None

    product_data = {
        "source_id": source_id,
        "source_sku": sku or title or url,
        "brand": "Akuvox",
        "model": title,
        "category": category,
        "price": price,
        "currency": "RUB",
        "url": url,
        "raw_html": html[:1_000_000],
    }

    async with async_session() as session_db:
        try:
            product = await upsert_product(session_db, product_data)
        except IntegrityError as ie:
            return
        except Exception as e:
            return

        specs = []
        specs.extend(_extract_jsonld_specs(soup))
        specs.extend(_extract_global_table_specs(soup))
        specs.extend(_extract_definition_list_specs(soup))
        specs.extend(_extract_sectioned_specs(soup))
        specs.extend(_extract_kv_lines(soup))

        if description:
            specs.append(("Описание", description))

        specs = _merge_spec_pairs(specs)

        try:
            await _save_specs(session_db, product.id, specs)
        except Exception as e:
            print("spec save error:", url, e)


async def process_product_link(
    session_http: aiohttp.ClientSession,
    url: str,
    source_id: int,
    sem: asyncio.Semaphore,
    counters: dict,
):
    async with sem:
        counters["processed"] += 1

        status, html = await fetch_with_retries(session_http, url)
        if status is None:
            counters["errors"] += 1
            return

        if status != 200 or not html:
            counters["skipped"] += 1
            await asyncio.sleep(REQUEST_DELAY + random.random())
            return

        if "502 bad gateway" in html.lower() or "503 service temporarily unavailable" in html.lower():
            counters["skipped"] += 1
            await asyncio.sleep(REQUEST_DELAY + random.random())
            return

        soup = BeautifulSoup(html, "html.parser")
        if not _looks_like_product(soup, html, url):
            counters["skipped"] += 1
            await asyncio.sleep(REQUEST_DELAY + random.random())
            return

        try:
            await parse_product_page_and_save(html, url, source_id)
            counters["saved"] += 1
        except Exception as e:
            counters["errors"] += 1

        await asyncio.sleep(REQUEST_DELAY + random.random())


async def crawl_akuvox_catalog():
    cookie_jar = aiohttp.CookieJar()
    connector = aiohttp.TCPConnector(limit_per_host=CONCURRENCY)

    async with aiohttp.ClientSession(
        headers=DEFAULT_HEADERS,
        cookie_jar=cookie_jar,
        connector=connector,
    ) as http:
        product_links = await collect_product_links(http)

        async with async_session() as db_sess:
            src = await create_source_if_missing(db_sess, "Akuvox Rus", BASE)
            source_id = src.id

        sem = asyncio.Semaphore(CONCURRENCY)
        counters = {"processed": 0, "saved": 0, "skipped": 0, "errors": 0}

        tasks = [
            process_product_link(http, link, source_id, sem, counters)
            for link in sorted(product_links)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(crawl_akuvox_catalog())