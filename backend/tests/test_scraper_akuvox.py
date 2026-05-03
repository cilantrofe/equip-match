"""Тесты скрапера akuvox-rus.ru: хелперы и parse_page."""

import json

import pytest
from bs4 import BeautifulSoup

from app.scrapers.akuvox_rus_scraper import (
    AkuvoxRusScraper,
    _allowed_path,
    _derive_category,
    _extract_jsonld_specs,
    _extract_kv_lines,
    _extract_price,
    _extract_title,
    _has_product_jsonld,
    _is_internal,
    _looks_like_product,
    _norm_url,
    _strip_title_prefix,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://akuvox-rus.ru/page#section", "https://akuvox-rus.ru/page"),
        ("https://akuvox-rus.ru/page/", "https://akuvox-rus.ru/page"),
        ("https://akuvox-rus.ru/page", "https://akuvox-rus.ru/page"),
    ],
)
def test_norm_url(url, expected):
    assert _norm_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://akuvox-rus.ru/page",
        "https://www.akuvox-rus.ru/page",
        "/relative/path",
    ],
)
def test_is_internal_true(url):
    assert _is_internal(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/page",
        "https://other-site.ru/page",
    ],
)
def test_is_internal_false(url):
    assert _is_internal(url) is False


@pytest.mark.parametrize(
    "path",
    [
        "/produkty/ip-vyzyvnye-paneli",
        "/produkty/ip-vyzyvnye-paneli/e12w",
        "/produkty/ip-domofony",
        "/produkty/ip-domofony/r29",
    ],
)
def test_allowed_path_true(path):
    assert _allowed_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/about",
        "/news/article",
        "/kontakty",
    ],
)
def test_allowed_path_false(path):
    assert _allowed_path(path) is False


def test_derive_category_panel():
    url = "https://akuvox-rus.ru/produkty/ip-vyzyvnye-paneli/e12w"
    assert _derive_category(url) == "Вызывная панель"


def test_derive_category_monitor():
    url = "https://akuvox-rus.ru/produkty/ip-domofony/r29"
    assert _derive_category(url) == "Видеомонитор"


def test_derive_category_unknown():
    assert _derive_category("https://akuvox-rus.ru/about") is None


# _strip_title_prefix


@pytest.mark.parametrize(
    "title, expected",
    [
        ("IP вызывная панель Akuvox E12W", "E12W"),
        ("IP домофон Akuvox R29", "R29"),
        ("Akuvox E16C", "E16C"),
        ("SomeOtherTitle", "SomeOtherTitle"),
    ],
)
def test_strip_title_prefix(title, expected):
    assert _strip_title_prefix(title) == expected


_JSONLD_PRODUCT_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Product",
  "name": "Akuvox E12W",
  "sku": "E12W",
  "additionalProperty": [
    {"@type": "PropertyValue", "name": "Питание", "value": "12В/PoE"},
    {"@type": "PropertyValue", "name": "Класс защиты", "value": "IP65"}
  ]
}
</script>
</head><body></body></html>
"""

_JSONLD_NO_PRODUCT_HTML = """
<html><head>
<script type="application/ld+json">{"@type": "WebPage", "name": "Каталог"}</script>
</head><body></body></html>
"""


def test_has_product_jsonld_true():
    assert _has_product_jsonld(_soup(_JSONLD_PRODUCT_HTML)) is True


def test_has_product_jsonld_false():
    assert _has_product_jsonld(_soup(_JSONLD_NO_PRODUCT_HTML)) is False


def test_has_product_jsonld_empty():
    assert _has_product_jsonld(_soup("<html><body></body></html>")) is False


def test_extract_jsonld_specs():
    pairs = _extract_jsonld_specs(_soup(_JSONLD_PRODUCT_HTML))
    assert ("Питание", "12В/PoE") in pairs
    assert ("Класс защиты", "IP65") in pairs


def test_extract_jsonld_specs_empty():
    assert _extract_jsonld_specs(_soup(_JSONLD_NO_PRODUCT_HTML)) == []


def test_extract_title_from_h1():
    html = "<html><body><h1>IP вызывная панель Akuvox E12W</h1></body></html>"
    assert _extract_title(_soup(html)) == "IP вызывная панель Akuvox E12W"


def test_extract_title_from_og_meta():
    html = '<html><head><meta property="og:title" content="Akuvox E12W"/></head><body></body></html>'
    assert _extract_title(_soup(html)) == "Akuvox E12W"


def test_extract_title_prefers_h1():
    html = '<html><head><meta property="og:title" content="OG Title"/></head><body><h1>H1 Title</h1></body></html>'
    assert _extract_title(_soup(html)) == "H1 Title"


def test_extract_title_none():
    assert _extract_title(_soup("<html><body></body></html>")) is None


def test_extract_price_from_price_class():
    html = '<html><body><div class="price">25 000 ₽</div></body></html>'
    assert _extract_price(_soup(html)) == pytest.approx(25000.0)


def test_extract_price_itemprop():
    html = '<html><body><span itemprop="price" content="15000">15 000 ₽</span></body></html>'
    price = _extract_price(_soup(html))
    assert price == pytest.approx(15000.0)


def test_extract_price_none_when_missing():
    assert _extract_price(_soup("<html><body><p>no price</p></body></html>")) is None


def test_extract_kv_lines_basic():
    html = """
    <html><body><main>
      <p>Питание: 12В DC</p>
      <p>Разрешение: 1920x1080</p>
    </main></body></html>
    """
    pairs = _extract_kv_lines(_soup(html))
    assert ("Питание", "12В DC") in pairs
    assert ("Разрешение", "1920x1080") in pairs


def test_extract_kv_lines_no_colon_skipped():
    html = "<html><body><main><p>просто текст без двоеточия</p></main></body></html>"
    pairs = _extract_kv_lines(_soup(html))
    assert pairs == []


_PRODUCT_PAGE_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "Product", "name": "Akuvox E12W", "additionalProperty": []}
</script>
</head><body>
<h1>IP вызывная панель Akuvox E12W</h1>
<div class="price">25 000 ₽</div>
<p>Основные характеристики: питание 12В, IP65</p>
</body></html>
"""

_CATALOG_PAGE_HTML = """
<html><body>
<h2>Каталог вызывных панелей</h2>
<ul><li><a href="/page1">Панель 1</a></li></ul>
</body></html>
"""


def test_looks_like_product_true():
    html = _PRODUCT_PAGE_HTML
    assert _looks_like_product(_soup(html), html, "https://akuvox-rus.ru/p") is True


def test_looks_like_product_false_catalog():
    html = _CATALOG_PAGE_HTML
    assert _looks_like_product(_soup(html), html, "https://akuvox-rus.ru/cat") is False


_PARSE_URL = "https://akuvox-rus.ru/produkty/ip-vyzyvnye-paneli/e12w"

_FULL_PRODUCT_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Product",
  "name": "Akuvox E12W",
  "sku": "E12W",
  "additionalProperty": [
    {"@type": "PropertyValue", "name": "Питание", "value": "12В/PoE"},
    {"@type": "PropertyValue", "name": "Класс защиты", "value": "IP65"}
  ]
}
</script>
</head><body>
<h1>IP вызывная панель Akuvox E12W</h1>
<div class="price">25 000 ₽</div>
<p>Основные характеристики</p>
<table>
  <tr><th>Питание</th><td>12В/PoE</td></tr>
  <tr><th>Класс защиты</th><td>IP65</td></tr>
</table>
</body></html>
"""


def test_parse_page_returns_product_data():
    scraper = AkuvoxRusScraper()
    soup = BeautifulSoup(_FULL_PRODUCT_HTML, "html.parser")
    result = scraper.parse_page(soup, _FULL_PRODUCT_HTML, _PARSE_URL)

    assert result is not None
    product_data, pairs = result
    assert product_data["brand"] == "Akuvox"
    assert product_data["category"] == "Вызывная панель"
    assert product_data["url"] == _PARSE_URL
    assert product_data["price"] == pytest.approx(25000.0)


def test_parse_page_returns_spec_pairs():
    scraper = AkuvoxRusScraper()
    soup = BeautifulSoup(_FULL_PRODUCT_HTML, "html.parser")
    result = scraper.parse_page(soup, _FULL_PRODUCT_HTML, _PARSE_URL)

    assert result is not None
    _, pairs = result
    names = [p[0] for p in pairs]
    assert "Питание" in names
    assert "Класс защиты" in names


def test_parse_page_returns_none_for_shallow_path():
    scraper = AkuvoxRusScraper()
    soup = BeautifulSoup(_FULL_PRODUCT_HTML, "html.parser")

    result = scraper.parse_page(
        soup, _FULL_PRODUCT_HTML, "https://akuvox-rus.ru/produkty"
    )
    assert result is None


def test_parse_page_returns_none_for_non_product():
    scraper = AkuvoxRusScraper()
    soup = BeautifulSoup(_CATALOG_PAGE_HTML, "html.parser")
    result = scraper.parse_page(soup, _CATALOG_PAGE_HTML, _PARSE_URL)
    assert result is None
