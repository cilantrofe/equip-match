"""Тесты скрапера Comelit: _iter_kv и _build_spec_pairs."""

import pytest

from app.scrapers.comelit_clients_api_scraper import _build_spec_pairs, _iter_kv


def test_iter_kv_from_dict():
    pairs = list(_iter_kv({"питание": "12В", "класс_защиты": "IP65"}))
    assert ("питание", "12В") in pairs
    assert ("класс_защиты", "IP65") in pairs


def test_iter_kv_from_list_name_value():
    data = [
        {"name": "Питание", "value": "12В"},
        {"name": "IP", "value": "IP65"},
    ]
    pairs = list(_iter_kv(data))
    assert ("Питание", "12В") in pairs
    assert ("IP", "IP65") in pairs


def test_iter_kv_from_list_key_value():
    data = [
        {"key": "Питание", "value": "PoE"},
    ]
    pairs = list(_iter_kv(data))
    assert ("Питание", "PoE") in pairs


def test_iter_kv_from_list_val_field():
    data = [{"name": "Вес", "val": "0.5 кг"}]
    pairs = list(_iter_kv(data))
    assert ("Вес", "0.5 кг") in pairs


def test_iter_kv_generic_list_dict():
    data = [{"питание": "12В"}]
    pairs = list(_iter_kv(data))
    assert ("питание", "12В") in pairs


def test_iter_kv_non_dict_list():
    pairs = list(_iter_kv(["строка", 42, None]))
    assert pairs == []


def test_iter_kv_empty():
    assert list(_iter_kv({})) == []
    assert list(_iter_kv([])) == []


def test_build_spec_pairs_basic():
    product = {
        "name": {"origin": "Comelit VK-A"},
        "id": "12345",
        "питание": "12В DC",
        "класс_защиты": "IP65",
    }
    pairs = _build_spec_pairs(product)
    names = [k for k, _ in pairs]
    assert "питание" in names
    assert "класс_защиты" in names

    assert "name" not in names
    assert "id" not in names


def test_build_spec_pairs_excludes_product_level_keys():
    product = {
        "name": "Товар",
        "price": 5000,
        "sku": "SKU-001",
        "description": "Описание",
        "slug": "tovar",
        "voltage": "12В",
    }
    pairs = _build_spec_pairs(product)
    names = [k for k, _ in pairs]
    assert "voltage" in names
    for excluded in ("name", "price", "sku", "description", "slug"):
        assert excluded not in names


def test_build_spec_pairs_nested_dict():
    product = {
        "specs": {"Питание": "12В", "Класс защиты": "IP65"},
    }
    pairs = _build_spec_pairs(product)
    assert ("Питание", "12В") in pairs
    assert ("Класс защиты", "IP65") in pairs


def test_build_spec_pairs_nested_list_of_name_value():
    product = {
        "properties": [
            {"name": "Питание", "value": "PoE"},
            {"name": "Вес", "value": "0.5 кг"},
        ],
    }
    pairs = _build_spec_pairs(product)
    assert ("Питание", "PoE") in pairs
    assert ("Вес", "0.5 кг") in pairs


def test_build_spec_pairs_none_values_skipped():
    product = {"питание": None, "класс_защиты": "IP65"}
    pairs = _build_spec_pairs(product)
    names = [k for k, _ in pairs]
    assert "питание" not in names
    assert "класс_защиты" in names


def test_build_spec_pairs_scalar_to_string():
    product = {"serial_number": 42, "версия": 3.14}
    pairs = _build_spec_pairs(product)
    vals = dict(pairs)
    assert vals["serial_number"] == "42"
    assert vals["версия"] == "3.14"


def test_build_spec_pairs_empty():
    assert _build_spec_pairs({}) == []

    assert _build_spec_pairs({"name": "X", "price": 100}) == []
