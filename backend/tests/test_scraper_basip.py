"""Тесты скрапера bas-ip.ru: хелперы и parse_page."""

import pytest
from bs4 import BeautifulSoup

from app.scrapers.basip_scraper import (
    BasIPScraper,
    _category_from_url,
    _extract_specs_from_container,
    _extract_specs_from_text_blocks,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://bas-ip.ru/catalog/panels/av-07b", "Вызывная панель"),
        ("https://bas-ip.ru/catalog/intercoms/at-07", "Видеомонитор"),
        ("https://bas-ip.ru/catalog/accessories/x1", "Аксессуары"),
        ("https://bas-ip.ru/about", None),
    ],
)
def test_category_from_url(url, expected):
    assert _category_from_url(url) == expected


_SPECS_CONTAINER_HTML = """
<html><body>
<div class="specifications">
  <div class="property">
    <span class="uk-text-muted">Питание</span>
    <span class="uk-text-bold">12В DC</span>
  </div>
  <div class="property">
    <span class="uk-text-muted">Класс защиты</span>
    <span class="uk-text-bold">IP65</span>
  </div>
  <div class="property">
    <span class="uk-text-muted">Разрешение</span>
    <span class="uk-text-bold">1920x1080</span>
  </div>
</div>
</body></html>
"""


def test_extract_specs_from_container_basic():
    pairs = _extract_specs_from_container(_soup(_SPECS_CONTAINER_HTML))
    assert ("Питание", "12В DC") in pairs
    assert ("Класс защиты", "IP65") in pairs
    assert ("Разрешение", "1920x1080") in pairs


def test_extract_specs_from_container_empty():
    assert _extract_specs_from_container(_soup("<html><body></body></html>")) == []


def test_extract_specs_from_container_no_duplicate_kv():
    html = """
    <div class="specifications">
      <div class="property">
        <span class="uk-text-muted">Питание</span>
        <span class="uk-text-bold">Питание</span>
      </div>
    </div>
    """
    pairs = _extract_specs_from_container(_soup(html))
    assert pairs == []


_TEXT_SPECS_HTML = """
<html><body>
<h3>Технические характеристики</h3>
<div>
  Питание
  12В DC
  Класс защиты
  IP65
</div>
</body></html>
"""


def test_extract_specs_from_text_blocks_heading():
    pairs = _extract_specs_from_text_blocks(_soup(_TEXT_SPECS_HTML))
    assert len(pairs) > 0
    names = [k for k, _ in pairs]
    assert "Питание" in names


def test_extract_specs_from_text_blocks_empty():
    html = "<html><body><p>нет характеристик</p></body></html>"

    pairs = _extract_specs_from_text_blocks(_soup(html))
    assert isinstance(pairs, list)


_PRODUCT_URL = "https://bas-ip.ru/catalog/panels/av-07b"

_PRODUCT_HTML_EAN = """
<html><body>
<h1>BAS-IP AV-07B</h1>
<div class="specifications">
  <div class="property">
    <span class="uk-text-muted">Питание</span>
    <span class="uk-text-bold">12В DC</span>
  </div>
  <div class="property">
    <span class="uk-text-muted">Класс защиты</span>
    <span class="uk-text-bold">IP65</span>
  </div>
</div>
<span>EAN: 4607015595127</span>
</body></html>
"""

_PRODUCT_HTML_SKU = """
<html><body>
<h1>BAS-IP AV-07B</h1>
<div class="specifications">
  <div class="property">
    <span class="uk-text-muted">Питание</span>
    <span class="uk-text-bold">PoE</span>
  </div>
</div>
<span>Артикул: AV-07</span>
</body></html>
"""

_NON_PRODUCT_HTML = """
<html><body>
<h1>Каталог</h1>
<ul><li>Товар 1</li></ul>
</body></html>
"""


def test_parse_page_with_ean():
    scraper = BasIPScraper()
    soup = _soup(_PRODUCT_HTML_EAN)
    result = scraper.parse_page(soup, _PRODUCT_HTML_EAN, _PRODUCT_URL)

    assert result is not None
    product_data, pairs = result
    assert product_data["brand"] == "BAS-IP"
    assert product_data["source_sku"] == "4607015595127"
    assert product_data["model"] == "BAS-IP AV-07B"


def test_parse_page_with_sku_article():
    scraper = BasIPScraper()
    soup = _soup(_PRODUCT_HTML_SKU)
    result = scraper.parse_page(soup, _PRODUCT_HTML_SKU, _PRODUCT_URL)

    assert result is not None
    product_data, _ = result
    assert product_data["source_sku"] == "AV-07"


def test_parse_page_returns_specs():
    scraper = BasIPScraper()
    soup = _soup(_PRODUCT_HTML_EAN)
    result = scraper.parse_page(soup, _PRODUCT_HTML_EAN, _PRODUCT_URL)

    assert result is not None
    _, pairs = result
    assert len(pairs) > 0
    names = [k for k, _ in pairs]
    assert "Питание" in names


def test_parse_page_returns_none_without_ean_or_sku():
    scraper = BasIPScraper()
    soup = _soup(_NON_PRODUCT_HTML)
    result = scraper.parse_page(soup, _NON_PRODUCT_HTML, _PRODUCT_URL)
    assert result is None


def test_parse_page_category_from_url():
    scraper = BasIPScraper()
    soup = _soup(_PRODUCT_HTML_EAN)
    result = scraper.parse_page(soup, _PRODUCT_HTML_EAN, _PRODUCT_URL)
    assert result is not None
    product_data, _ = result
    assert product_data["category"] == "Вызывная панель"
