"""Нормализация значений характеристик товаров.

Скраперы передают сырые строки в `normalize_value`, который возвращает
структурированный `NormalizedValue`. Функция `parse_number_and_unit`
оставлена как тонкая обёртка, чтобы старые скраперы продолжали работать
без изменений.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
import re


ValueKind = Literal[
    "number",
    "range",
    "dimension",
    "fraction",
    "standard",
    "boolean",
    "text",
    "empty",
]


@dataclass(frozen=True)
class NormalizedValue:
    """Структурированный результат разбора сырого значения характеристики."""

    value_num: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    kind: ValueKind = "empty"


UNIT_ALIASES: dict[str, str] = {
    "в": "V", "v": "V", "вольт": "V", "volt": "V", "volts": "V",
    "а": "A", "a": "A", "ма": "mA", "ma": "mA",
    "вт": "W", "w": "W", "watt": "W", "watts": "W",
    "мм": "mm", "mm": "mm",
    "см": "cm", "cm": "cm",
    "м": "m", "meter": "m", "meters": "m", "метр": "m",
    "дюйм": "inch", "дюйма": "inch", "дюймов": "inch",
    "in": "inch", "inch": "inch", '"': "inch", "″": "inch",
    "гц": "Hz", "hz": "Hz",
    "кгц": "kHz", "khz": "kHz",
    "мгц": "MHz", "mhz": "MHz",
    "ггц": "GHz", "ghz": "GHz",
    "кб": "KB", "kb": "KB",
    "мб": "MB", "mb": "MB",
    "гб": "GB", "gb": "GB",
    "тб": "TB", "tb": "TB",
    "кг": "kg", "kg": "kg",
    "г": "g", "g": "g",
    "мс": "ms", "ms": "ms",
    "с": "s", "sec": "s",
    "мин": "min", "min": "min",
    "°c": "C", "c°": "C", "°с": "C", "с°": "C",
    "°": "deg", "deg": "deg", "градус": "deg",
    "fps": "fps", "к/с": "fps", "кадр/с": "fps",
    "мп": "MP", "mp": "MP",
    "лк": "lux", "lux": "lux", "люкс": "lux",
    "дб": "dB", "db": "dB",
    "%": "%",
}


_STANDARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"IP[0-9]{2}[KX]?", re.IGNORECASE),
    re.compile(r"H\.?26[0-9]\+?", re.IGNORECASE),
    re.compile(r"802\.[0-9]+[a-z0-9/\-]*", re.IGNORECASE),
    re.compile(r"RJ-?[0-9]{1,3}", re.IGNORECASE),
    re.compile(r"USB[\s\-]?[0-9](?:\.[0-9])?", re.IGNORECASE),
    re.compile(r"PoE(?:\+\+|\+)?", re.IGNORECASE),
    re.compile(r"Wi-?Fi(?:\s?[0-9]+)?", re.IGNORECASE),
    re.compile(r"MJPEG|MPEG-?[124]|HEVC", re.IGNORECASE),
)

_THOUSAND_SEP_RE = re.compile(r"(?<=\d)[ \u00A0\u202F](?=\d{3}\b)")

_RANGE_RE = re.compile(
    r"^([+-]?\d+(?:[.,]\d+)?)"
    r"\s*(?:\.\.|—|–|−|÷|\sto\s|-)\s*"
    r"([+-]?\d+(?:[.,]\d+)?)"
    r"\s*(.*)$"
)

_FRACTION_RE = re.compile(r"^(\d+)\s*/\s*(\d+)\s*(.*)$")

_DIMENSION_SEP_RE = re.compile(r"[×xхX*]")

_LEADING_NUM_RE = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)")

_PLAIN_NUM_RE = re.compile(r"^([+-]?\d+(?:[.,]\d+)?)\s*(.*)$")

_UNIT_CHARS_RE = re.compile(r"^[a-zA-Zа-яА-Я°/²³%\s\.\-]+$")

_TRAILING_UNIT_RE = re.compile(r"([a-zA-Zа-яА-Я°%/²³]+)\s*$")

_BOOL_TRUE = frozenset({"да", "yes", "есть", "поддерживается", "поддержка", "true", "1", "+"})
_BOOL_FALSE = frozenset({"нет", "no", "отсутствует", "не поддерживается", "false", "0", "-"})


def _clean(text: str) -> str:
    """Убрать пробелы по краям и привести юникод-пробелы к обычному."""
    return (
        text
        .replace("\xa0", " ")
        .replace("\u202f", " ")
        .replace("\u2212", "-")  # Unicode minus sign → hyphen-minus
        .strip()
    )


def _normalize_unit(token: Optional[str]) -> Optional[str]:
    """Вернуть каноническую форму единицы из `UNIT_ALIASES`.

    Неизвестные единицы возвращаются как есть (в нижнем регистре),
    пустая строка и `None` — как `None`.
    """
    if not token:
        return None
    key = token.strip().lower().rstrip(".,;")
    if not key:
        return None
    return UNIT_ALIASES.get(key, key)


def _to_float(s: str) -> float:
    """Преобразовать строку в число, принимая и точку, и запятую."""
    return float(s.replace(",", "."))


def _format_num(n: float) -> str:
    """Записать число без хвостового `.0` для целых значений."""
    return str(int(n)) if n == int(n) else str(n)


def _try_boolean(text: str) -> Optional[NormalizedValue]:
    key = text.strip().lower().rstrip(".,;")
    if key in _BOOL_TRUE:
        return NormalizedValue(value_num=1.0, value_text="да", kind="boolean")
    if key in _BOOL_FALSE:
        return NormalizedValue(value_num=0.0, value_text="нет", kind="boolean")
    return None


def _has_standard(text: str) -> bool:
    return any(p.search(text) for p in _STANDARD_PATTERNS)


def _try_fraction(text: str) -> Optional[NormalizedValue]:
    m = _FRACTION_RE.match(text)
    if not m:
        return None
    canonical = f"{m.group(1)}/{m.group(2)}"
    rest = m.group(3).strip()
    unit: Optional[str]
    if '"' in rest or "″" in rest:
        unit = "inch"
    elif rest:
        unit = _normalize_unit(rest)
    else:
        unit = "inch"
    return NormalizedValue(value_text=canonical, unit=unit, kind="fraction")


def _try_dimension(text: str) -> Optional[NormalizedValue]:
    if not _DIMENSION_SEP_RE.search(text):
        return None
    parts = _DIMENSION_SEP_RE.split(text)
    if len(parts) < 2:
        return None
    nums: list[str] = []
    for part in parts:
        match = _LEADING_NUM_RE.match(part.strip())
        if not match:
            return None
        nums.append(_format_num(_to_float(match.group(1))))
    trailing = _TRAILING_UNIT_RE.search(parts[-1])
    unit = _normalize_unit(trailing.group(1)) if trailing else None
    return NormalizedValue(
        value_text="x".join(nums), unit=unit, kind="dimension"
    )


def _try_range(text: str) -> Optional[NormalizedValue]:
    m = _RANGE_RE.match(text)
    if not m:
        return None
    low = _to_float(m.group(1))
    high = _to_float(m.group(2))
    tail = m.group(3).strip()
    unit = _normalize_unit(tail) if tail else None
    canonical = f"{_format_num(low)}..{_format_num(high)}"
    return NormalizedValue(value_text=canonical, unit=unit, kind="range")


def _try_plain_number(text: str) -> Optional[NormalizedValue]:
    collapsed = _THOUSAND_SEP_RE.sub("", text)
    m = _PLAIN_NUM_RE.match(collapsed)
    if not m:
        return None
    num = _to_float(m.group(1))
    rest = m.group(2).strip()
    if rest and not _UNIT_CHARS_RE.fullmatch(rest):
        return None
    unit = _normalize_unit(rest) if rest else None
    return NormalizedValue(value_num=num, unit=unit, kind="number")


def normalize_value(raw: Optional[str]) -> NormalizedValue:
    """Разобрать сырую строку характеристики в `NormalizedValue`.

    Применяет форматы по порядку: стандарт, дробь, размерность,
    диапазон, простое число. Если ни один не подошёл — значение
    сохраняется как свободный текст. Для `None` и пустых строк
    возвращает `kind="empty"`.
    """
    if raw is None:
        return NormalizedValue(kind="empty")

    text = _clean(str(raw))
    if not text:
        return NormalizedValue(kind="empty")

    if _has_standard(text):
        return NormalizedValue(value_text=text, kind="standard")

    parsers = (_try_boolean, _try_fraction, _try_dimension, _try_range, _try_plain_number)
    for parser in parsers:
        result = parser(text)
        if result is not None:
            return result

    return NormalizedValue(value_text=text, kind="text")


def parse_number_and_unit(
    text: Optional[str],
) -> tuple[Optional[float], Optional[str]]:
    """Обёртка над `normalize_value`, возвращающая `(num, unit)`.

    Старые скраперы распаковывают результат как кортеж — интерфейс
    оставлен ради обратной совместимости. В новом коде используйте
    `normalize_value` напрямую, чтобы не терять `value_text` и `kind`.
    """
    if not text:
        return None, None
    nv = normalize_value(text)
    return nv.value_num, nv.unit


def normalize_spec_name(name: str) -> str:
    """Убрать пробелы и привести имя характеристики к нижнему регистру.

    Сопоставление синонимов с канонической формой живёт в
    `spec_aliases.canonicalize_spec_name`; здесь — только базовая
    чистка, общая для всех вызывающих.
    """
    if not name:
        return ""
    return name.strip().lower()
