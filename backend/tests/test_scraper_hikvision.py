"""Тесты скрапера hikvisionpro.ru: хелперы и parse_page."""

from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from app.scrapers.hikvisionpro_scraper import (
    HikvisionProScraper,
    _extract_specs,
    _is_product_anchor,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _a(href: str) -> SimpleNamespace:
    return SimpleNamespace(get=lambda k, default=None: href if k == "href" else default)


@pytest.mark.parametrize(
    "href",
    [
        "/catalog/element/ds-kh6320",
        "/Catalog/Element/some-product",
        "/catalog/element/ds-kh6320/",
    ],
)
def test_is_product_anchor_true(href):
    tag = _soup(f'<a href="{href}">link</a>').find("a")
    assert _is_product_anchor(tag) is True


@pytest.mark.parametrize(
    "href",
    [
        "/catalog/videodomofony-hikvision/",
        "javascript:void(0)",
        "mailto:info@example.com",
        "",
    ],
)
def test_is_product_anchor_false(href):
    tag = _soup(f'<a href="{href}">link</a>').find("a")
    assert _is_product_anchor(tag) is False


def test_extract_specs_from_table():
    html = """
    <html><body>
    <table>
      <tr><th>Питание</th><td>12 В</td></tr>
      <tr><th>Класс защиты</th><td>IP65</td></tr>
    </table>
    </body></html>
    """
    pairs = _extract_specs(_soup(html))
    assert ("Питание", "12 В") in pairs
    assert ("Класс защиты", "IP65") in pairs


def test_extract_specs_from_dl_fallback():
    html = """
    <html><body>
    <dl>
      <dt>Питание</dt><dd>PoE</dd>
      <dt>Вес</dt><dd>0.5 кг</dd>
    </dl>
    </body></html>
    """
    pairs = _extract_specs(_soup(html))
    assert ("Питание", "PoE") in pairs


def test_extract_specs_from_spec_class():
    html = """
    <html><body>
    <div class="specs">
      <li>Питание: 12В</li>
      <li>Класс защиты: IP65</li>
    </div>
    </body></html>
    """
    pairs = _extract_specs(_soup(html))
    assert ("Питание", "12В") in pairs


def test_extract_specs_empty():
    assert (
        _extract_specs(_soup("<html><body><p>нет характеристик</p></body></html>"))
        == []
    )


_PRODUCT_URL = "https://hikvisionpro.ru/catalog/element/ds-kh6320"

_PRODUCT_HTML = """
<html><body>
<h1>Hikvision DS-KH6320-WTE1</h1>
<div class="catalog-element">
  <span class="catalog-element-article">DS-KH6320-WTE1</span>
</div>
<span class="price">15 000 ₽</span>
<table>
  <tr><th>Питание</th><td>12В DC</td></tr>
  <tr><th>Класс защиты</th><td>IP65</td></tr>
</table>
</body></html>
"""

_PANEL_URL = "https://hikvisionpro.ru/catalog/element/ds-kd8003"

_HIWATCH_HTML = """
<html><body>
<h1>HiWatch DS-D100I</h1>
<div class="catalog-element">
  <span class="catalog-element-article">DS-D100I</span>
</div>
<table>
  <tr><th>Питание</th><td>PoE</td></tr>
</table>
</body></html>
"""


def test_parse_page_returns_product_data():
    scraper = HikvisionProScraper()
    scraper._url_category[_PRODUCT_URL] = "Видеомонитор"
    soup = _soup(_PRODUCT_HTML)
    result = scraper.parse_page(soup, _PRODUCT_HTML, _PRODUCT_URL)

    assert result is not None
    product_data, _ = result
    assert product_data["brand"] == "Hikvision"
    assert product_data["model"] == "Hikvision DS-KH6320-WTE1"
    assert product_data["category"] == "Видеомонитор"
    assert product_data["source_sku"] == "DS-KH6320-WTE1"


def test_parse_page_price_parsed():
    scraper = HikvisionProScraper()
    scraper._url_category[_PRODUCT_URL] = "Видеомонитор"
    soup = _soup(_PRODUCT_HTML)
    result = scraper.parse_page(soup, _PRODUCT_HTML, _PRODUCT_URL)

    assert result is not None
    product_data, _ = result
    assert product_data["price"] == pytest.approx(15000.0)


def test_parse_page_returns_spec_pairs():
    scraper = HikvisionProScraper()
    scraper._url_category[_PRODUCT_URL] = "Видеомонитор"
    soup = _soup(_PRODUCT_HTML)
    result = scraper.parse_page(soup, _PRODUCT_HTML, _PRODUCT_URL)

    assert result is not None
    _, pairs = result
    names = [k for k, _ in pairs]
    assert "Питание" in names
    assert "Класс защиты" in names


def test_parse_page_hiwatch_brand():
    scraper = HikvisionProScraper()
    scraper._url_category[_PANEL_URL] = "Вызывная панель"
    soup = _soup(_HIWATCH_HTML)
    result = scraper.parse_page(soup, _HIWATCH_HTML, _PANEL_URL)

    assert result is not None
    product_data, _ = result
    assert product_data["brand"] == "HiWatch"


def test_parse_page_sku_falls_back_to_url_slug():
    scraper = HikvisionProScraper()
    scraper._url_category[_PRODUCT_URL] = "Видеомонитор"
    html = """
    <html><body>
    <h1>Hikvision DS-KH6320</h1>
    <table><tr><th>Питание</th><td>12В</td></tr></table>
    </body></html>
    """
    soup = _soup(html)
    result = scraper.parse_page(soup, html, _PRODUCT_URL)

    assert result is not None
    product_data, _ = result

    assert product_data["source_sku"] == "DS-KH6320"
