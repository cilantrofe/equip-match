# backend/app/scrapers/hikvisionpro_catalog_page_scraper.py
import asyncio
import random
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.db.session import async_session
from app.db.crud import create_source_if_missing, upsert_product, add_spec
from app.normalization.normalizer import parse_number_and_unit, normalize_spec_name

# Source (one-time)
BASE = "https://hikvisionpro.ru"
CATALOG_PATH = "/catalog/videodomofony-hikvision/"
CATALOG_URL = urljoin(BASE, CATALOG_PATH)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE,
}

REQUEST_DELAY = 0.4
CONCURRENCY = 6
RETRIES = 2


async def fetch_text(session: aiohttp.ClientSession, url: str, tries=RETRIES):
    for attempt in range(1, tries + 1):
        try:
            async with session.get(url, timeout=40, allow_redirects=True) as resp:
                text = await resp.text(errors="ignore")
                return resp.status, text
        except Exception as e:
            if attempt == tries:
                return None, None
            await asyncio.sleep(0.2 * attempt + random.random() * 0.1)
    return None, None


def is_product_anchor(a_tag, catalog_path=CATALOG_PATH):
    """
    Heuristics: consider <a> a product link if it is inside an element whose
    class contains product/card/item/catalog/goods/tile OR its href contains some product indicators.
    This ensures we only take links that are displayed as product tiles on the catalog page.
    """
    href = a_tag.get("href") or ""
    if not href:
        return False

    # Don't include anchors pointing to fragments or external sites
    if href.startswith("javascript:") or href.startswith("mailto:"):
        return False

    # Check ancestors classes
    ancestor = a_tag
    for _ in range(4):
        ancestor = ancestor.parent
        if ancestor is None:
            break
        cl = " ".join(ancestor.get("class") or [])
        if cl:
            low = cl.lower()
            if any(k in low for k in ("product", "card", "item", "catalog", "goods", "tile", "prod")):
                return True

    # fallback: href contains keywords typical for product pages
    if any(k in href.lower() for k in ("/product", "/item", "/card", "/goods", "/catalog/")):
        # but ensure it was on the same domain (we will normalize later)
        return True

    return False


def normalize_link(href: str) -> str:
    # make absolute and strip params/fragments
    abs_url = urljoin(BASE, href).split("#")[0].rstrip("/")
    return abs_url


def extract_price_from_text(txt: str):
    if not txt:
        return None
    m = re.search(r"([\d\s\u00A0]+(?:[.,]\d+)?)\s*(₽|rub|RUB)?", txt.replace("\xa0", " "))
    if m:
        num = m.group(1).replace(" ", "").replace("\u00A0", "").replace(",", ".")
        try:
            return float(num)
        except Exception:
            return None
    return None


async def parse_product_page_and_save(html: str, url: str, source_id: int):
    soup = BeautifulSoup(html, "html.parser")

    # Title / H1
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else None

    # SKU detection
    sku = None
    sku_sel = soup.select_one(".sku, .product-code, .artikul, .catalog-article, .code")
    if sku_sel:
        sku = sku_sel.get_text(" ", strip=True)
    else:
        txt = soup.find(string=re.compile(r"Артикул|SKU|EAN|Код", re.I))
        if txt:
            m = re.search(r"[:\s]\s*([A-Za-z0-9\-_\/\.]+)", txt)
            if m:
                sku = m.group(1).strip()

    # Price detection
    price = None
    price_sel = soup.select_one(".price, .product-price, .price-current, .value_price, .price__value")
    if price_sel:
        price = extract_price_from_text(price_sel.get_text(" ", strip=True))
    if price is None:
        txt = soup.find(string=re.compile(r"[\d\s\u00A0]+\s*₽"))
        if txt:
            price = extract_price_from_text(txt)

    # Category (breadcrumb)
    category = None
    bc = soup.select_one(".breadcrumb, nav.breadcrumbs, .breadcrumbs")
    if bc:
        try:
            nodes = [n.get_text(" ", strip=True) for n in bc.find_all(["a", "li", "span"]) if n.get_text(strip=True)]
            if nodes:
                category = nodes[-1]
        except Exception:
            category = None

    # Description (short / long)
    desc = None
    desc_sel = soup.select_one(".product-description, .description, .desc, .short-description, .text")
    if desc_sel:
        desc = desc_sel.get_text(" ", strip=True)

    # Image
    image = None
    img = soup.select_one(".product-image img, img.product-image, .gallery img")
    if img and img.get("src"):
        image = urljoin(BASE, img.get("src"))

    product_data = {
        "source_id": source_id,
        "source_sku": sku or title or url,
        "brand": None,
        "model": title or None,
        "category": category,
        "price": price,
        "currency": "RUB" if price else None,
        "url": url,
        "raw_html": html[:1000000],
    }

    async with async_session() as db_sess:
        try:
            p = await upsert_product(db_sess, product_data)
        except Exception as e:
            print("DB upsert error:", url, e)
            return

        # parse specs: tables, lists, dl, and lines with "Key: Value"
        parsed_any = False

        # tables
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if len(cells) >= 2:
                    key = cells[0].get_text(" ", strip=True)
                    val = cells[1].get_text(" ", strip=True)
                    if key:
                        sname = normalize_spec_name(key)
                        num, unit = parse_number_and_unit(val)
                        try:
                            if num is not None:
                                await add_spec(db_sess, p.id, sname, None, num, unit)
                            else:
                                await add_spec(db_sess, p.id, sname, val or None, None, None)
                            parsed_any = True
                        except Exception as e:
                            print("add_spec table error:", p.id, key, e)

        # blocks likely with specs
        spec_blocks = soup.select("[class*='spec'], [class*='характер'], .product-specs, .specifications, .params, .props")
        for sc in spec_blocks:
            # try list items
            items = sc.find_all(["li", "div"])
            for it in items:
                txt = it.get_text(" ", strip=True)
                if ":" in txt:
                    k, v = [x.strip() for x in txt.split(":", 1)]
                    if k:
                        sname = normalize_spec_name(k)
                        num, unit = parse_number_and_unit(v)
                        try:
                            if num is not None:
                                await add_spec(db_sess, p.id, sname, None, num, unit)
                            else:
                                await add_spec(db_sess, p.id, sname, v or None, None, None)
                            parsed_any = True
                        except Exception as e:
                            print("add_spec block error:", p.id, k, e)

        # dl/dt/dd
        if not parsed_any:
            for dl in soup.find_all("dl"):
                dts = dl.find_all("dt")
                for dt in dts:
                    dd = dt.find_next_sibling("dd")
                    if not dd:
                        continue
                    k = dt.get_text(" ", strip=True)
                    v = dd.get_text(" ", strip=True)
                    if k:
                        sname = normalize_spec_name(k)
                        num, unit = parse_number_and_unit(v)
                        try:
                            if num is not None:
                                await add_spec(db_sess, p.id, sname, None, num, unit)
                            else:
                                await add_spec(db_sess, p.id, sname, v or None, None, None)
                            parsed_any = True
                        except Exception as e:
                            print("add_spec dl error:", p.id, k, e)

        # fallback: try to find "ключ: значение" pairs in description text
        if not parsed_any and desc:
            lines = [ln.strip() for ln in desc.splitlines() if ln.strip()]
            for ln in lines:
                if ":" in ln:
                    k, v = [x.strip() for x in ln.split(":", 1)]
                    if k:
                        sname = normalize_spec_name(k)
                        num, unit = parse_number_and_unit(v)
                        try:
                            if num is not None:
                                await add_spec(db_sess, p.id, sname, None, num, unit)
                            else:
                                await add_spec(db_sess, p.id, sname, v or None, None, None)
                        except Exception as e:
                            print("add_spec desc line error:", p.id, k, e)

        # save image/description
        if image:
            try:
                await add_spec(db_sess, p.id, normalize_spec_name("image"), image, None, None)
            except Exception:
                pass
        if desc:
            try:
                await add_spec(db_sess, p.id, normalize_spec_name("description"), desc, None, None)
            except Exception:
                pass

    return


async def crawl_catalog_page_only():
    conn = aiohttp.TCPConnector(limit_per_host=6)
    async with aiohttp.ClientSession(headers=HEADERS, connector=conn) as session:
        st, html = await fetch_text(session, CATALOG_URL)
        if st != 200 or not html:
            print("Failed to fetch catalog page:", st)
            return

        soup = BeautifulSoup(html, "html.parser")

        # find all candidate anchors that look like product links on this page
        found = []
        for a in soup.find_all("a", href=True):
            if is_product_anchor(a):
                href = a.get("href")
                normalized = normalize_link(href)
                # only internal links
                if urlparse(normalized).netloc != urlparse(BASE).netloc:
                    continue
                found.append(normalized)

        # dedupe and preserve order
        seen = set()
        links = []
        for u in found:
            if u not in seen:
                seen.add(u)
                links.append(u)

        print("Product links found on catalog page:", len(links))
        # print first few for debug
        for i, u in enumerate(links[:40], 1):
            print(f"{i:02d}. {u}")

        if not links:
            print("No product links detected — consider sending the catalog page HTML for selector tuning.")
            return

        # create/get source
        async with async_session() as db_sess:
            src = await create_source_if_missing(db_sess, "HikvisionPro (catalog page)", BASE + CATALOG_PATH)
            source_id = src.id

        # process links with limited concurrency
        sem = asyncio.Semaphore(CONCURRENCY)
        counters = {"processed": 0, "saved": 0, "skipped": 0, "errors": 0}

        async def worker(link):
            async with sem:
                counters["processed"] += 1
                s, h = await fetch_text(session, link)
                if s != 200 or not h:
                    print("skip (status)", s, link)
                    counters["skipped"] += 1
                    return
                try:
                    await parse_product_page_and_save(h, link, source_id)
                    print("saved:", link)
                    counters["saved"] += 1
                except Exception as e:
                    print("error for", link, e)
                    counters["errors"] += 1
                await asyncio.sleep(REQUEST_DELAY + random.random() * 0.15)

        tasks = [asyncio.create_task(worker(u)) for u in links]
        await asyncio.gather(*tasks)

        print("=== SUMMARY ===")
        print("Found links:", len(links))
        print("Processed:", counters["processed"])
        print("Saved:", counters["saved"])
        print("Skipped:", counters["skipped"])
        print("Errors:", counters["errors"])


if __name__ == "__main__":
    asyncio.run(crawl_catalog_page_only())
