from pint import UnitRegistry
import re

ureg = UnitRegistry()
number_re = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")

def parse_number_and_unit(text: str):
    if not text:
        return None, None
    m = number_re.search(text.replace('\xa0',' '))
    if not m:
        return None, None
    num = float(m.group(1).replace(",", "."))
    after = text[m.end():].strip()
    unit_token = None
    if after:
        unit_token = after.split()[0].strip(",.;")
    return num, unit_token

def normalize_spec_name(name: str):
    name = name.strip().lower()
    mappings = {
        "питание": "power",
        "разрешение экрана": "resolution",
        "встроенная камера": "camera",
        "питание:": "power",
        "питание /": "power"
    }
    for k, v in mappings.items():
        if k in name:
            return v
    return name

# множители для приведения к миллиметрам
_UNIT_TO_MM = {
    "mm": 1.0, "мм": 1.0, "millimeter": 1.0,
    "cm": 10.0, "см": 10.0,
    "m": 1000.0, "м": 1000.0,
    "in": 25.4, "″": 25.4, "in.": 25.4, "inch": 25.4
}

def _normalize_unit_token(token: str):
    if not token: 
        return None
    t = token.strip().lower().replace(".", "").replace(" ", "")
    # common russian forms
    t = t.replace("миллиметр", "mm").replace("миллиметров", "mm")
    t = t.replace("миллиметры", "mm").replace("миллиметр.", "mm")
    # common cm
    t = t.replace("сантиметр", "cm").replace("сантиметров", "cm").replace("см", "cm")
    # inches
    t = t.replace("дюйм", "in").replace("дюйма", "in").replace("дюймов", "in")
    # accept mm / cm / in direct
    if t in ("mm","мм","cm","см","m","м","in","inch","\"","″"):
        return t
    # fallback: strip non alpha
    m = re.search(r"[a-zа-я]+", t)
    return m.group(0) if m else None

def parse_dimensions(text: str):
    """
    Попытается извлечь 2-3 числовых размера и единицу.
    Возвращает dict:
    {
      "nums": [195.0, 127.0, 27.0],
      "unit_raw": "мм",
      "unit_norm": "mm",
      "nums_in_mm": [195.0, 127.0, 27.0]  # приведённые к мм (float)
    }
    Или None если не распознано.
    """
    if not text:
        return None
    s = text.strip()
    # нормализуем разные символы на "x"
    s = s.replace("×", "x").replace("х", "x").replace("X", "x").replace("*", "x")
    # попытка выделить единицу в конце: числа(...)\s*(unit)
    m_unit = re.search(r"([0-9\.,x\s\-–]+)\s*([a-zA-Zа-яА-Я\"″\.]+)$", s)
    unit_token = None
    nums_part = s
    if m_unit:
        nums_part = m_unit.group(1)
        unit_token = m_unit.group(2)
    # извлечь числа
    nums = re.findall(r"([0-9]+(?:[.,][0-9]+)?)", nums_part)
    if not nums:
        return None
    nums_f = [float(n.replace(",", ".")) for n in nums]
    unit_norm = _normalize_unit_token(unit_token) if unit_token else None
    # приведение к мм
    factor = _UNIT_TO_MM.get(unit_norm, 1.0)  # если None, считаем что уже в мм
    nums_mm = [float(n) * factor for n in nums_f]
    return {"nums": nums_f, "unit_raw": unit_token, "unit_norm": unit_norm or "mm", "nums_in_mm": nums_mm}

