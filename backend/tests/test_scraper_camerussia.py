"""Тесты скрапера Camerussia: extract_products_from_api_response и хелперы."""

import pytest

from app.scrapers.camerussia_smart_house_scraper import (
    _category_from_filename,
    extract_products_from_api_response,
)


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("abonent_ip_devices.json", "Видеомонитор"),
        ("/path/to/abonent_ip_v2.json", "Видеомонитор"),
        ("panels_catalog.json", "Вызывная панель"),
        ("vyzyvnye_paneli.json", "Вызывная панель"),
        ("", "Вызывная панель"),
    ],
)
def test_category_from_filename(filename, expected):
    assert _category_from_filename(filename) == expected


def test_extract_empty_input():
    assert extract_products_from_api_response(None) == []
    assert extract_products_from_api_response([]) == []
    assert extract_products_from_api_response({}) == []


def test_extract_top_level_list():
    data = [
        {"name": "Панель A", "price": "5000", "code": "PA-01"},
        {"name": "Панель B", "price": "7500", "code": "PB-01"},
    ]
    products = extract_products_from_api_response(data)
    assert len(products) == 2
    assert products[0]["name"] == "Панель A"
    assert products[1]["code"] == "PB-01"


def test_extract_wrapped_in_items_key():
    data = {
        "items": [
            {"name": "Монитор X", "price": 12000, "code": "MX-01"},
        ]
    }
    products = extract_products_from_api_response(data)
    assert len(products) == 1
    assert products[0]["name"] == "Монитор X"


def test_extract_wrapped_in_products_key():
    data = {
        "products": [
            {"name": "Панель Y", "price": 8000, "code": "PY-01"},
        ]
    }
    products = extract_products_from_api_response(data)
    assert len(products) == 1


def test_extract_wrapped_in_data_key():
    data = {
        "data": [
            {"name": "Panel Z", "price": 9000},
        ]
    }
    products = extract_products_from_api_response(data)
    assert len(products) == 1


_ITEM = {
    "name": "Akuvox E12W",
    "code": "E12W",
    "price": "25000",
    "main_image_link": "//cdn.example.com/img.jpg",
    "parameters": [
        {"name": "Питание", "value": "12В/PoE", "value_float": None},
        {"name": "Класс защиты", "value": "IP65", "value_float": None},
    ],
    "url": "akuvox-e12w",
    "brand": "Akuvox",
    "section": "Вызывная панель",
}


def test_extract_product_fields():
    products = extract_products_from_api_response([_ITEM])
    assert len(products) == 1
    p = products[0]
    assert p["name"] == "Akuvox E12W"
    assert p["code"] == "E12W"
    assert p["price"] == pytest.approx(25000.0)
    assert p["brand"] == "Akuvox"
    assert p["category"] == "Вызывная панель"


def test_extract_product_url_built_from_slug():
    products = extract_products_from_api_response([_ITEM])
    assert products[0]["url"] == "https://camerussia.com/product/akuvox-e12w"


def test_extract_product_absolute_url_kept():
    item = {**_ITEM, "url": "https://camerussia.com/akuvox-e12w"}
    products = extract_products_from_api_response([item])
    assert products[0]["url"] == "https://camerussia.com/akuvox-e12w"


def test_extract_product_image_protocol_added():
    products = extract_products_from_api_response([_ITEM])
    assert products[0]["image"] == "https://cdn.example.com/img.jpg"


def test_extract_product_params_extracted():
    products = extract_products_from_api_response([_ITEM])
    params = products[0]["params"]
    assert params["Питание"] == "12В/PoE"
    assert params["Класс защиты"] == "IP65"


def test_extract_product_value_float_preferred():
    item = {
        **_ITEM,
        "parameters": [{"name": "Вес", "value": "0.5 кг", "value_float": 0.5}],
    }
    products = extract_products_from_api_response([item])
    assert products[0]["params"]["Вес"] == pytest.approx(0.5)


def test_extract_product_price_invalid_string():
    item = {**_ITEM, "price": "по запросу"}
    products = extract_products_from_api_response([item])
    assert products[0]["price"] is None


def test_extract_product_default_brand_fallback():
    item = {k: v for k, v in _ITEM.items() if k != "brand"}
    products = extract_products_from_api_response([item])
    assert products[0]["brand"] == "Camerussia"


def test_extract_category_from_filename_fallback():
    item = {k: v for k, v in _ITEM.items() if k != "section"}
    products = extract_products_from_api_response(
        [item], filename="abonent_ip_list.json"
    )
    assert products[0]["category"] == "Видеомонитор"


def test_extract_non_dict_items_skipped():
    data = ["строка", 42, None, {"name": "Товар", "code": "T1"}]
    products = extract_products_from_api_response(data)
    assert len(products) == 1
    assert products[0]["name"] == "Товар"


def test_extract_category_from_list_of_dicts():
    item = {
        **_ITEM,
        "section": [{"name": "Вызывная панель"}],
    }
    del item["section"]
    item["categories"] = [{"name": "Вызывная панель"}]
    products = extract_products_from_api_response([item])
    assert products[0]["category"] == "Вызывная панель"
