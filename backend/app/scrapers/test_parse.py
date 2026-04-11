from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

P = "/app/camerussia.html"
BASE = "https://camerussia.com"

with open(P, "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")

anchors = soup.select("a.product, a[class*='product'], [class*='product'] a")
print("anchors found:", len(anchors))


def extract_from_anchor(a):
    href = a.get("href")
    url = urljoin(BASE, href.split("#")[0].strip()) if href else None
    name_tag = a.select_one(
        ".product__descr, .product__title, .descr, .title, h3, h4, a[title]"
    )
    name = name_tag.get_text(" ", strip=True) if name_tag else (a.get("title") or None)
    price_tag = a.select_one("[class*='price']")
    price_txt = price_tag.get_text(" ", strip=True) if price_tag else None
    price = None
    if price_txt:
        m = re.search(r"([\d\s\u00A0]+(?:[.,]\d+)?)", price_txt.replace("\xa0", " "))
        if m:
            price = m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    img_tag = a.find("img")
    img = None
    if img_tag:
        src = img_tag.get("data-src") or img_tag.get("src") or img_tag.get("data-lazy")
        if src and src.startswith("//"):
            src = "https:" + src
        img = urljoin(BASE, src) if src else None
    return {"url": url, "name": name, "price": price, "image": img}


for i, a in enumerate(anchors[:30], 1):
    item = extract_from_anchor(a)
    print(
        f"{i:02d}. url={item['url']!r} name={item['name']!r} price={item['price']!r} img={item['image']!r}"
    )
