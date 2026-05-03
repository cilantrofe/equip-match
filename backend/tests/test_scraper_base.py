"""Тесты утилит base.py: _clean, _extract_table_specs, _extract_dl_specs."""

import pytest
from bs4 import BeautifulSoup

from app.scrapers.base import _clean, _extract_dl_specs, _extract_table_specs


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  hello  ", "hello"),
        ("hello world", "hello world"),
        ("hello world", "hello world"),
        ("a  \t  b", "a b"),
        ("", ""),
        (None, ""),
        ("нет данных", "нет данных"),
    ],
)
def test_clean(raw, expected):
    assert _clean(raw) == expected


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_extract_table_specs_basic():
    html = """
    <table>
      <tr><th>Питание</th><td>12 В</td></tr>
      <tr><th>Класс защиты</th><td>IP65</td></tr>
    </table>
    """
    pairs = _extract_table_specs(_soup(html))
    assert ("Питание", "12 В") in pairs
    assert ("Класс защиты", "IP65") in pairs


def test_extract_table_specs_multiple_tables():
    html = """
    <table><tr><th>A</th><td>1</td></tr></table>
    <table><tr><th>B</th><td>2</td></tr></table>
    """
    pairs = _extract_table_specs(_soup(html))
    assert len(pairs) == 2
    assert ("A", "1") in pairs
    assert ("B", "2") in pairs


def test_extract_table_specs_empty():
    assert _extract_table_specs(_soup("<div>no table</div>")) == []


def test_extract_table_specs_single_cell_rows_skipped():
    html = "<table><tr><th>Заголовок</th></tr><tr><th>A</th><td>1</td></tr></table>"
    pairs = _extract_table_specs(_soup(html))

    assert ("A", "1") in pairs
    assert all(k != "Заголовок" for k, _ in pairs)


def test_extract_table_specs_nbsp_cleaned():
    html = "<table><tr><th>Вес (кг)</th><td>0.5 кг</td></tr></table>"
    pairs = _extract_table_specs(_soup(html))
    assert ("Вес (кг)", "0.5 кг") in pairs


def test_extract_dl_specs_basic():
    html = """
    <dl>
      <dt>Питание</dt><dd>12 В</dd>
      <dt>Разрешение</dt><dd>1920x1080</dd>
    </dl>
    """
    pairs = _extract_dl_specs(_soup(html))
    assert ("Питание", "12 В") in pairs
    assert ("Разрешение", "1920x1080") in pairs


def test_extract_dl_specs_no_dd_skipped():
    html = "<dl><dt>Одинокий заголовок</dt></dl>"
    pairs = _extract_dl_specs(_soup(html))
    assert pairs == []


def test_extract_dl_specs_empty_values_skipped():
    html = "<dl><dt>Пусто</dt><dd></dd><dt>Есть</dt><dd>Значение</dd></dl>"
    pairs = _extract_dl_specs(_soup(html))
    assert ("Есть", "Значение") in pairs
    assert all(k != "Пусто" for k, _ in pairs)


def test_extract_dl_specs_empty_html():
    assert _extract_dl_specs(_soup("<p>нет списка</p>")) == []
