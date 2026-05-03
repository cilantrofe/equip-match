"""Матчинг товаров: по цене и по характеристикам.

`match_by_tech` собирает характеристики обеих сторон в каноническом
виде, считает сходство по каждой характеристике target и возвращает
взвешенный скор кандидата вместе с разбивкой, чтобы было видно, какая
характеристика какой вклад дала.

`match_by_price` сравнивает относительную разницу цен и возвращает
top-N ближайших кандидатов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from app.normalization.spec_aliases import canonicalize_spec_name, weight_for

_PAREN_RE = re.compile(r'\([^)]*\)')
_TOKEN_RE = re.compile(r'[а-яёa-z0-9]+', re.IGNORECASE)


@dataclass
class FeatureContribution:
    """Вклад одной характеристики в итоговый скор кандидата."""

    name: str
    target: Optional[str]
    candidate: Optional[str]
    similarity: float
    weight: float
    note: Optional[str] = None

    @property
    def contribution(self) -> float:
        """Взвешенный вклад: `similarity * weight`."""
        return self.similarity * self.weight


@dataclass
class MatchResult:
    """Кандидат с его скором и разбивкой по характеристикам."""

    candidate: object
    score: float
    breakdown: list[FeatureContribution] = field(default_factory=list)


SpecTuple = tuple[Optional[float], Optional[str], float]
WeightOverrides = dict[str, float]


def _collect_specs(
    product: object,
    overrides: Optional[WeightOverrides] = None,
) -> dict[str, SpecTuple]:
    """Собрать характеристики товара в виде `{canonical: (num, text, weight)}`.

    Для строк без `spec_name_canonical` каноническое имя вычисляется
    на лету через `canonicalize_spec_name`. Если у товара несколько
    записей одной характеристики (дубли в старых данных), берётся
    запись с наибольшим весом из БД — оверрайды на выбор дубля не влияют,
    они применяются отдельным проходом после того, как значение выбрано.
    """
    out: dict[str, SpecTuple] = {}
    for s in getattr(product, "specs", []) or []:
        canonical = s.spec_name_canonical or canonicalize_spec_name(s.spec_name)
        if not canonical:
            continue
        num = float(s.spec_value_num) if s.spec_value_num is not None else None
        text = (s.spec_value_text or "").strip().lower() or None
        db_weight = _effective_weight(s, canonical)
        prev = out.get(canonical)
        if prev is None or db_weight > prev[2]:
            out[canonical] = (num, text, db_weight)
    if overrides:
        out = {
            name: (num, text, overrides[name] if name in overrides else w)
            for name, (num, text, w) in out.items()
        }
    return out


def _effective_weight(spec: object, canonical: str) -> float:
    """Определить вес характеристики: БД-значение побеждает дефолтное.

    NULL в колонке `weight` означает «не задано»; в этом случае
    возвращается канонический дефолт из `WEIGHT_DEFAULTS`.
    """
    raw = getattr(spec, "weight", None)
    if raw is None:
        return weight_for(canonical)
    return float(raw)


def _text_similarity(a: str, b: str) -> float:
    """Нечёткое сходство двух текстовых значений характеристики.

    1. Точное совпадение → 1.0.
    2. Убираем пояснения в скобках («если не используется poe» и т.п.),
       проверяем снова → 0.97.
    3. Считаем Жаккар по стемам токенов (первые 5 символов каждого слова
       длиной ≥ 2) — ловит «врезная/врезной», «накладная/накладной».
    4. Считаем символьный fuzzy-ratio через SequenceMatcher.
    5. Возвращаем максимум из шагов 3 и 4.
    """
    if a == b:
        return 1.0

    a_n = _PAREN_RE.sub("", a).strip()
    b_n = _PAREN_RE.sub("", b).strip()
    if a_n == b_n:
        return 0.97

    def stems(text: str) -> set[str]:
        return {w[:5] for w in _TOKEN_RE.findall(text) if len(w) >= 2}

    sa, sb = stems(a_n), stems(b_n)
    jaccard = len(sa & sb) / len(sa | sb) if (sa or sb) else 0.0
    fuzzy = SequenceMatcher(None, a_n, b_n).ratio()
    return round(max(jaccard, fuzzy), 3)


def _similarity(
    t_num: Optional[float],
    t_text: Optional[str],
    c_num: Optional[float],
    c_text: Optional[str],
) -> tuple[float, Optional[str]]:
    """Вернуть сходство пары значений и пометку-нотку.

    Оба числа — нормализованное линейное сходство в [0, 1]. Оба текста —
    нечёткое сравнение через `_text_similarity`. Если тип значений не совпал
    (число vs текст), возвращаем 0.0 с `note="type_mismatch"`.
    """
    if t_num is not None and c_num is not None:
        denom = max(abs(t_num), abs(c_num), 1.0)
        sim = max(0.0, 1.0 - abs(t_num - c_num) / denom)
        return sim, None
    if t_text is not None and c_text is not None:
        return _text_similarity(t_text, c_text), None
    if (t_num is not None) != (c_num is not None):
        return 0.0, "type_mismatch"
    return 0.0, None


def _display(num: Optional[float], text: Optional[str]) -> Optional[str]:
    """Вернуть строковое представление значения: число предпочтительнее текста."""
    if num is not None:
        return str(num)
    return text


def _score_pair(
    target_specs: dict[str, SpecTuple],
    cand_specs: dict[str, SpecTuple],
) -> tuple[float, list[FeatureContribution], int]:
    """Посчитать скор по характеристикам target и собрать breakdown.

    Знаменатель — сумма весов **всех** характеристик target. Если
    у кандидата нет характеристики, которая есть у target, в breakdown
    попадает запись с `similarity=0` и `note="missing_on_candidate"`
    и полный вес характеристики идёт в знаменатель — это штрафует
    кандидата за пробелы относительно требований target.

    Возвращает также число общих характеристик — оно нужно вызывающему
    коду, чтобы отсечь кандидатов без ни одного совпадения (им нельзя
    осмысленно присвоить скор — они просто «другой товар»).
    """
    breakdown: list[FeatureContribution] = []
    total_weight = 0.0
    weighted_sum = 0.0
    common_count = 0
    for name, (t_num, t_text, t_w) in target_specs.items():
        total_weight += t_w
        if name in cand_specs:
            c_num, c_text, _ = cand_specs[name]
            sim, note = _similarity(t_num, t_text, c_num, c_text)
            breakdown.append(
                FeatureContribution(
                    name=name,
                    target=_display(t_num, t_text),
                    candidate=_display(c_num, c_text),
                    similarity=round(sim, 3),
                    weight=t_w,
                    note=note,
                )
            )
            weighted_sum += sim * t_w
            common_count += 1
        else:
            breakdown.append(
                FeatureContribution(
                    name=name,
                    target=_display(t_num, t_text),
                    candidate=None,
                    similarity=0.0,
                    weight=t_w,
                    note="missing_on_candidate",
                )
            )
    score = weighted_sum / total_weight if total_weight > 0 else 0.0
    breakdown.sort(key=lambda f: f.contribution, reverse=True)
    return score, breakdown, common_count


def _same_brand(target: object, candidate: object) -> bool:
    """Вернуть `True`, если оба товара принадлежат одному бренду (без учёта регистра)."""
    t = (getattr(target, "brand", None) or "").strip().lower()
    c = (getattr(candidate, "brand", None) or "").strip().lower()
    return bool(t) and t == c


def match_by_tech(
    target: object,
    candidates: list[object],
    limit: int = 5,
    exclude_same_brand: bool = True,
    weight_overrides: Optional[WeightOverrides] = None,
) -> list[MatchResult]:
    """Вернуть top-N кандидатов по взвешенному сходству характеристик.

    Формула: `score = Σ(sim_i × weight_i) / Σ_target(weight_i)`.
    Знаменатель считается по всем характеристикам target — кандидат,
    у которого части характеристик нет, получает штраф пропорционально
    их весу (в breakdown это видно по `note="missing_on_candidate"`).

    `weight_overrides` — веса, прилетевшие из запроса по каноническим
    именам; перекрывают и `product_specs.weight`, и `WEIGHT_DEFAULTS`.
    Позволяет менеджеру динамически крутить важность характеристик под
    конкретную заявку, не трогая БД.

    При `exclude_same_brand=True` пропускаются кандидаты того же бренда.
    Кандидаты без единой общей характеристики с target в результат не
    попадают — у них все характеристики идут через `missing_on_candidate`,
    и скор был бы 0, что засоряет выдачу.
    """
    if not target or not candidates:
        return []

    target_specs = _collect_specs(target, weight_overrides)
    if not target_specs:
        return []

    results: list[MatchResult] = []
    for cand in candidates:
        if exclude_same_brand and _same_brand(target, cand):
            continue
        cand_specs = _collect_specs(cand, weight_overrides)
        score, breakdown, common = _score_pair(target_specs, cand_specs)
        if common == 0:
            continue
        results.append(
            MatchResult(
                candidate=cand,
                score=round(score, 3),
                breakdown=breakdown,
            )
        )
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]


def match_by_price(
    target: object,
    candidates: list[object],
    limit: int = 5,
    exclude_same_brand: bool = True,
) -> list[MatchResult]:
    """Вернуть top-N ближайших по цене кандидатов.

    Скор — `max(0, 1 - |Δ| / target_price)`. Кандидаты без цены и
    (опционально) одного бренда с target отбрасываются.
    """
    if not target or not candidates:
        return []
    target_price = getattr(target, "price", None)
    if target_price is None:
        return []
    try:
        t_price = float(target_price)
    except (TypeError, ValueError):
        return []
    if t_price <= 0:
        return []

    results: list[MatchResult] = []
    for cand in candidates:
        if exclude_same_brand and _same_brand(target, cand):
            continue
        cand_price = getattr(cand, "price", None)
        if cand_price is None:
            continue
        try:
            cp = float(cand_price)
        except (TypeError, ValueError):
            continue
        diff = abs(t_price - cp) / t_price
        score = round(max(0.0, 1.0 - diff), 3)
        results.append(MatchResult(candidate=cand, score=score))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
