import asyncio
import random
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from app.db.session import async_session
from app.db.crud import create_source_if_missing, upsert_product, add_spec
from app.normalization.normalizer import parse_number_and_unit, normalize_spec_name
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlparse

BASE = "https://bas-ip.ru"
REQUEST_DELAY = 1.0
CONCURRENCY = 2
RETRIES = 3


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": urljoin(BASE, "/catalog"),
    "Connection": "keep-alive",
}


async def collect_product_links(session_http, base_catalog="/catalog"):
    root_url = urljoin(BASE, base_catalog)
    status, html = await fetch_with_retries(session_http, root_url)
    if status != 200 or not html:
        return set()
    soup = BeautifulSoup(html, "html.parser")

    all_links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/catalog/") and not href.rstrip("/").endswith("/catalog"):
            all_links.add(urljoin(BASE, href))

    product_links = set()
    category_links = set()

    for l in all_links:
        path = urlparse(l).path
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 3:
            product_links.add(l)
        else:
            category_links.add(l)

    to_visit = list(category_links)
    visited = set()
    while to_visit:
        cat = to_visit.pop(0)
        if cat in visited:
            continue
        visited.add(cat)
        st, h = await fetch_with_retries(session_http, cat)
        if st != 200 or not h:
            continue
        s = BeautifulSoup(h, "html.parser")

        for a in s.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("/catalog/"):
                continue
            full = urljoin(BASE, href)
            p = urlparse(full).path
            parts = [x for x in p.split("/") if x]
            if len(parts) >= 3:
                product_links.add(full)
            else:
                if full not in visited:
                    to_visit.append(full)
    return product_links


async def fetch_with_retries(
    session_http: aiohttp.ClientSession, url: str, tries: int = RETRIES
):
    delay = 0.5
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


async def parse_product_page_and_save(html: str, url: str, source_id: int):
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return

    title_tag = soup.select_one("h1")
    title = title_tag.get_text(strip=True) if title_tag else None

    price = None
    for sel in [
        ".price",
        ".product-price",
        ".price-block",
        ".product__price",
        ".prod-price",
    ]:
        tag = soup.select_one(sel)
        if tag:
            txt = tag.get_text(separator=" ", strip=True)
            import re

            m = re.search(r"([\d\s\u00A0]+)\s*₽", txt)
            if m:
                price = float(m.group(1).replace("\u00a0", "").replace(" ", ""))
            break

    ean = None

    ean_candidates = soup.find_all(
        string=lambda s: s
        and ("артикул" in s.lower() or "ean" in s.lower() or "арт" in s.lower())
    )
    if ean_candidates:
        import re

        for cand in ean_candidates:
            m = re.search(
                r"(?:EAN|Артикул|арт\.?)[:\s]*([0-9A-Za-z\-]+)",
                cand.strip(),
                flags=re.I,
            )
            if m:
                ean = m.group(1).strip()
                break

    cat = None
    bc = soup.select(".breadcrumb li")
    if bc:
        try:
            cat = bc[-1].get_text(strip=True)
        except Exception:
            cat = None

    product_data = {
        "source_id": source_id,
        "source_sku": ean or title or url,
        "brand": "BAS-IP",
        "model": title or None,
        "category": cat,
        "price": price,
        "currency": "RUB",
        "url": url,
        "raw_html": html[:1000000],
    }

    async with async_session() as session_db:
        try:
            p = await upsert_product(session_db, product_data)
        except IntegrityError as ie:
            return
        except Exception as e:
            return

    spec_parsed = False

    spec_container = soup.select_one(".specifications")
    if spec_container:
        props = spec_container.select(".property")
        if props:
            for prop in props:
                key_el = prop.select_one(".uk-text-muted")
                val_el = prop.select_one(".uk-text-bold")
                key = key_el.get_text(strip=True) if key_el else None
                val = val_el.get_text(" ", strip=True) if val_el else ""
                if not key:
                    continue
                spec_name = normalize_spec_name(key)
                num, unit = parse_number_and_unit(val)
                try:
                    if num is not None:
                        await add_spec(session_db, p.id, spec_name, None, num, unit)
                    else:
                        await add_spec(
                            session_db, p.id, spec_name, val or None, None, None
                        )
                except Exception as e:
                    print("add_spec error (property):", p.id, spec_name, e)
            spec_parsed = True

    if not spec_parsed:
        spec_heading = None
        for s in soup.find_all(string=True):
            try:
                if "техничесные характеристики" in s.strip().lower():
                    spec_heading = s
                    break
            except Exception:
                continue

        lines = []
        if spec_heading:
            heading_el = spec_heading.parent
            for sib in heading_el.find_next_siblings():
                if sib.name and sib.name.lower() in ("h2", "h3", "h4"):
                    break
                text = sib.get_text(separator="\n", strip=True)
                if not text:
                    continue
                lower = text.lower()
                if "файлы" in lower or "загрузки" in lower or "скачать" in lower:
                    break
                for ln in text.splitlines():
                    ln = ln.strip()
                    if ln:
                        lines.append(ln)

        if not lines:
            desc_block = soup.select_one(
                ".product-description, .product-body, .product-info, .content"
            )
            if desc_block:
                txt = desc_block.get_text(separator="\n", strip=True)
                for ln in txt.splitlines():
                    ln = ln.strip()
                    if ln:
                        lines.append(ln)

        i = 0
        while i < len(lines):
            key = lines[i].strip()
            val = lines[i + 1].strip() if (i + 1) < len(lines) else ""
            i += 2
            if not key:
                continue
            spec_name = normalize_spec_name(key)
            num, unit = parse_number_and_unit(val)
            try:
                if num is not None:
                    await add_spec(session_db, p.id, spec_name, None, num, unit)
                else:
                    await add_spec(session_db, p.id, spec_name, val or None, None, None)
            except Exception as e:
                print("add_spec error (lines):", p.id, spec_name, e)


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
        if status != 200:
            counters["skipped"] += 1
            await asyncio.sleep(REQUEST_DELAY + random.random())
            return
        if "503 Service Temporarily Unavailable" in (html or ""):
            counters["skipped"] += 1
            await asyncio.sleep(REQUEST_DELAY + random.random())
            return

        soup = BeautifulSoup(html, "html.parser")
        has_specs = bool(soup.select_one(".specifications"))
        has_price = bool(
            soup.select_one(".price")
            or soup.select_one(".uk-text-bolder")
            or soup.select_one(".product__price")
        )
        has_ean = bool(
            soup.find(
                string=lambda s: s
                and ("EAN" in s or "Артикул" in s or "арт." in s.lower())
            )
        )
        is_product = has_specs or has_price or has_ean

        if not is_product:
            counters["skipped"] += 1
            await asyncio.sleep(REQUEST_DELAY + random.random())
            return

        try:
            await parse_product_page_and_save(html, url, source_id)
            counters["saved"] += 1
        except Exception as e:
            counters["errors"] += 1

        await asyncio.sleep(REQUEST_DELAY + random.random())


async def crawl_catalog_and_products():
    cookie_jar = aiohttp.CookieJar()
    conn = aiohttp.TCPConnector(limit_per_host=2)
    async with aiohttp.ClientSession(
        headers=DEFAULT_HEADERS, cookie_jar=cookie_jar, connector=conn
    ) as http:
        product_links = await collect_product_links(http, "/catalog")
        async with async_session() as db_sess:
            src = await create_source_if_missing(db_sess, "BAS-IP", BASE)
            source_id = src.id

        sem = asyncio.Semaphore(CONCURRENCY)
        counters = {"processed": 0, "saved": 0, "skipped": 0, "errors": 0}
        tasks = [
            process_product_link(http, link, source_id, sem, counters)
            for link in sorted(product_links)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(crawl_catalog_and_products())
