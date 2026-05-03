"""Тесты нормализации значений характеристик normalizer.py."""

import pytest

from app.normalization.normalizer import (
    NormalizedValue,
    _normalize_unit,
    normalize_for_spec,
    normalize_temperature_range,
    normalize_value,
    parse_number_and_unit,
)


@pytest.mark.parametrize("raw", [None, "", "   ", "\xa0", " "])
def test_normalize_value_empty(raw):
    assert normalize_value(raw).kind == "empty"


@pytest.mark.parametrize(
    "raw", ["да", "yes", "есть", "поддерживается", "true", "1", "+"]
)
def test_normalize_value_boolean_true(raw):
    nv = normalize_value(raw)
    assert nv.kind == "boolean"
    assert nv.value_num == 1.0
    assert nv.value_text == "да"


@pytest.mark.parametrize("raw", ["нет", "no", "отсутствует", "false", "0", "-"])
def test_normalize_value_boolean_false(raw):
    nv = normalize_value(raw)
    assert nv.kind == "boolean"
    assert nv.value_num == 0.0
    assert nv.value_text == "нет"


@pytest.mark.parametrize(
    "raw, expected_num, expected_unit",
    [
        ("12.5 V", 12.5, "V"),
        ("100", 100.0, None),
        ("3,14", 3.14, None),
        ("12 Вт", 12.0, "W"),
        ("0.5 кг", 0.5, "kg"),
        ("100 %", 100.0, "%"),
        ("50 Гц", 50.0, "Hz"),
        ("3.3 В", 3.3, "V"),
    ],
)
def test_normalize_value_plain_number(raw, expected_num, expected_unit):
    nv = normalize_value(raw)
    assert nv.kind == "number"
    assert nv.value_num == pytest.approx(expected_num)
    assert nv.unit == expected_unit


def test_normalize_value_thousand_separator():
    nv = normalize_value("1 000 500")
    assert nv.kind == "number"
    assert nv.value_num == pytest.approx(1000500.0)


@pytest.mark.parametrize(
    "raw, expected_text, expected_unit",
    [
        ("0 — 100 %", "0..100", "%"),
        ("5 to 35", "5..35", None),
        ("-40..+85 °C", "-40..85", "C"),
        ("10–20", "10..20", None),
        ("-10..45", "-10..45", None),
    ],
)
def test_normalize_value_range(raw, expected_text, expected_unit):
    nv = normalize_value(raw)
    assert nv.kind == "range"
    assert nv.value_text == expected_text
    assert nv.unit == expected_unit


@pytest.mark.parametrize(
    "raw, expected_text, expected_unit",
    [
        ("100×200 mm", "100x200", "mm"),
        ("12×34×56", "12x34x56", None),
        ("100 x 200 x 50 мм", "100x200x50", "mm"),
        ("80*60", "80x60", None),
    ],
)
def test_normalize_value_dimension(raw, expected_text, expected_unit):
    nv = normalize_value(raw)
    assert nv.kind == "dimension"
    assert nv.value_text == expected_text
    assert nv.unit == expected_unit


@pytest.mark.parametrize(
    "raw, expected_text, expected_unit",
    [
        ("1/2", "1/2", "inch"),
        ('1/4"', "1/4", "inch"),
        ("1/3 inch", "1/3", "inch"),
    ],
)
def test_normalize_value_fraction(raw, expected_text, expected_unit):
    nv = normalize_value(raw)
    assert nv.kind == "fraction"
    assert nv.value_text == expected_text
    assert nv.unit == expected_unit


@pytest.mark.parametrize(
    "raw",
    [
        "IP65",
        "H.265+",
        "802.11ac",
        "PoE+",
        "Wi-Fi 6",
        "MJPEG",
        "RJ-45",
        "USB 2.0",
        "H264",
    ],
)
def test_normalize_value_standard(raw):
    nv = normalize_value(raw)
    assert nv.kind == "standard"
    assert nv.value_text == raw


@pytest.mark.parametrize(
    "raw, expected_num, expected_unit",
    [
        ("до 100 м", 100.0, "m"),
        ("не более 50 Вт", 50.0, "W"),
        ("up to 1000", 1000.0, None),
    ],
)
def test_normalize_value_up_to_prefix(raw, expected_num, expected_unit):
    nv = normalize_value(raw)
    assert nv.kind == "number"
    assert nv.value_num == pytest.approx(expected_num)
    assert nv.unit == expected_unit


def test_normalize_value_nbsp_unit():
    nv = normalize_value("12\xa0Вт")
    assert nv.kind == "number"
    assert nv.value_num == pytest.approx(12.0)
    assert nv.unit == "W"


def test_normalize_value_unicode_minus():
    nv = normalize_value("−10 В")
    assert nv.kind == "number"
    assert nv.value_num == pytest.approx(-10.0)
    assert nv.unit == "V"


def test_normalize_value_text_fallback():
    nv = normalize_value("настенный монтаж")
    assert nv.kind == "text"
    assert nv.value_text == "настенный монтаж"


def test_normalize_value_text_fallback_mixed():
    nv = normalize_value("врезная/накладная")
    assert nv.kind == "text"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("-10°C ~ +45°C", "-10...45"),
        ("-40...+85 °C", "-40...85"),
        ("от -20 до +60", "-20...60"),
        ("-10 .. 50", "-10...50"),
    ],
)
def test_normalize_temperature_range(raw, expected):
    assert normalize_temperature_range(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "нет данных"])
def test_normalize_temperature_range_returns_none(raw):
    assert normalize_temperature_range(raw) is None


def test_normalize_temperature_range_single_number():
    assert normalize_temperature_range("100") is None


def test_normalize_for_spec_weight_grams_conversion():
    nv = normalize_for_spec("weight", "0.5 кг")
    assert nv.value_num == pytest.approx(500.0)
    assert nv.unit == "g"


def test_normalize_for_spec_weight_no_conversion():
    nv = normalize_for_spec("weight", "1.2 кг")
    assert nv.value_num == pytest.approx(1.2)
    assert nv.unit == "kg"


def test_normalize_for_spec_weight_empty():
    nv = normalize_for_spec("weight", None)
    assert nv.kind == "empty"


@pytest.mark.parametrize(
    "raw, expected_text",
    [
        ("IP65", "65"),
        ("IP 54", "54"),
        ("IP67", "67"),
    ],
)
def test_normalize_for_spec_ip_rating_strips_prefix(raw, expected_text):
    nv = normalize_for_spec("ip_rating", raw)
    assert nv.value_text == expected_text


@pytest.mark.parametrize(
    "raw, expected_text",
    [
        ("IK10", "10"),
        ("IK 08", "08"),
        ("IK07", "07"),
    ],
)
def test_normalize_for_spec_ik_rating_strips_prefix(raw, expected_text):
    nv = normalize_for_spec("ik_rating", raw)
    assert nv.value_text == expected_text


def test_normalize_for_spec_temperature_range():
    nv = normalize_for_spec("temperature_range", "-10..45")
    assert nv.value_text == "-10...45"
    assert nv.value_num is None


def test_normalize_for_spec_temperature_range_full():
    nv = normalize_for_spec("temperature_range", "-40°C ~ +85°C")
    assert nv.value_text == "-40...85"


def test_normalize_for_spec_passthrough():
    nv1 = normalize_for_spec("voltage", "12 V")
    nv2 = normalize_value("12 V")
    assert nv1 == nv2


@pytest.mark.parametrize(
    "token, expected",
    [
        ("Вт", "W"),
        ("вт", "W"),
        ("w", "W"),
        ("W", "W"),
        ("кГц", "kHz"),
        ("МГц", "MHz"),
        ("мм", "mm"),
        ("°c", "C"),
    ],
)
def test_normalize_unit_aliases(token, expected):
    assert _normalize_unit(token) == expected


@pytest.mark.parametrize("token", [None, ""])
def test_normalize_unit_empty(token):
    assert _normalize_unit(token) is None


def test_normalize_unit_too_long():
    assert _normalize_unit("a" * 21) is None


def test_normalize_unit_unknown_passthrough():
    assert _normalize_unit("xyz_unknown") == "xyz_unknown"


def test_parse_number_and_unit_basic():
    num, unit = parse_number_and_unit("12.5 V")
    assert num == pytest.approx(12.5)
    assert unit == "V"


def test_parse_number_and_unit_none():
    assert parse_number_and_unit(None) == (None, None)


def test_parse_number_and_unit_text_only():
    num, unit = parse_number_and_unit("настенный")
    assert num is None
