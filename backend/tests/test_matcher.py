"""Тесты матчера matcher.py"""

from types import SimpleNamespace

import pytest

from app.matching.matcher import (
    MatchResult,
    _similarity,
    _text_similarity,
    match_by_price,
    match_by_tech,
)


def make_spec(canonical, num=None, text=None, weight=None, name=None):
    return SimpleNamespace(
        spec_name=name or canonical,
        spec_name_canonical=canonical,
        spec_value_num=num,
        spec_value_text=text,
        weight=weight,
    )


def make_product(
    brand="BrandA", price=1000.0, specs=None, sku="SKU001", category="cam"
):
    return SimpleNamespace(
        id=1,
        source_sku=sku,
        brand=brand,
        model="M1",
        category=category,
        price=price,
        url=None,
        specs=specs or [],
    )


def test_match_by_price_ordering():
    target = make_product(brand="X", price=1000.0)
    c950 = make_product(brand="A", price=950.0, sku="c950")
    c1100 = make_product(brand="B", price=1100.0, sku="c1100")
    c1500 = make_product(brand="C", price=1500.0, sku="c1500")
    c800 = make_product(brand="D", price=800.0, sku="c800")

    results = match_by_price(target, [c950, c1100, c1500, c800])

    skus = [r.candidate.source_sku for r in results]
    assert skus == ["c950", "c1100", "c800", "c1500"]


def test_match_by_price_exact_match():
    target = make_product(brand="X", price=1000.0)
    cand = make_product(brand="Y", price=1000.0)
    results = match_by_price(target, [cand])
    assert results[0].score == pytest.approx(1.0)


def test_match_by_price_score_clamped_to_zero():
    target = make_product(brand="X", price=100.0)
    cand = make_product(brand="Y", price=10000.0)
    results = match_by_price(target, [cand])
    assert results[0].score == pytest.approx(0.0)


def test_match_by_price_excludes_same_brand_by_default():
    target = make_product(brand="Hikvision", price=1000.0)
    same = make_product(brand="Hikvision", price=999.0)
    other = make_product(brand="Dahua", price=999.0)
    results = match_by_price(target, [same, other])
    assert len(results) == 1
    assert results[0].candidate.brand == "Dahua"


def test_match_by_price_same_brand_included_when_disabled():
    target = make_product(brand="Hikvision", price=1000.0)
    same = make_product(brand="Hikvision", price=999.0)
    results = match_by_price(target, [same], exclude_same_brand=False)
    assert len(results) == 1


def test_match_by_price_no_price_on_target():
    target = make_product(price=None)
    cand = make_product(brand="Y", price=500.0)
    assert match_by_price(target, [cand]) == []


def test_match_by_price_zero_price_on_target():
    target = make_product(price=0)
    cand = make_product(brand="Y", price=500.0)
    assert match_by_price(target, [cand]) == []


def test_match_by_price_candidate_missing_price():
    target = make_product(brand="X", price=1000.0)
    no_price = make_product(brand="Y", price=None, sku="no_price")
    with_price = make_product(brand="Z", price=1000.0, sku="with_price")
    results = match_by_price(target, [no_price, with_price])
    assert len(results) == 1
    assert results[0].candidate.source_sku == "with_price"


def test_match_by_price_respects_limit():
    target = make_product(brand="X", price=1000.0)
    candidates = [
        make_product(brand=f"B{i}", price=1000.0 + i, sku=f"c{i}") for i in range(10)
    ]
    results = match_by_price(target, candidates, limit=3)
    assert len(results) == 3


def test_match_by_price_empty_candidates():
    target = make_product()
    assert match_by_price(target, []) == []


def test_match_by_price_none_target():
    assert match_by_price(None, [make_product()]) == []


def test_match_by_tech_exact_numeric():
    target = make_product(brand="X", specs=[make_spec("voltage", num=12.0)])
    cand_exact = make_product(
        brand="Y", price=1000.0, specs=[make_spec("voltage", num=12.0)], sku="exact"
    )
    cand_half = make_product(
        brand="Z", price=1000.0, specs=[make_spec("voltage", num=24.0)], sku="half"
    )

    results = match_by_tech(target, [cand_exact, cand_half])

    assert results[0].candidate.source_sku == "exact"
    assert results[0].score == pytest.approx(1.0)
    assert results[1].score < results[0].score


def test_match_by_tech_exact_text():
    target = make_product(brand="X", specs=[make_spec("mount_type", text="настенный")])
    cand = make_product(brand="Y", specs=[make_spec("mount_type", text="настенный")])
    results = match_by_tech(target, [cand])
    assert results[0].score == pytest.approx(1.0)


def test_match_by_tech_partial_text():
    target = make_product(
        brand="X", specs=[make_spec("mount_type", text="настенная установка")]
    )
    cand = make_product(
        brand="Y", specs=[make_spec("mount_type", text="настенный монтаж")]
    )
    results = match_by_tech(target, [cand])
    assert len(results) == 1
    assert 0.0 < results[0].score < 1.0


def test_match_by_tech_missing_spec_penalizes():
    specs_both = [
        make_spec("voltage", num=12.0, weight=3.0),
        make_spec("ip_rating", text="65", weight=2.5),
    ]
    target = make_product(brand="X", specs=specs_both)
    cand_both = make_product(brand="Y", specs=specs_both, sku="both")
    cand_one = make_product(
        brand="Z", specs=[make_spec("voltage", num=12.0, weight=3.0)], sku="one"
    )

    results = match_by_tech(target, [cand_both, cand_one])

    scores = {r.candidate.source_sku: r.score for r in results}
    assert scores["both"] > scores["one"]


def test_match_by_tech_type_mismatch():
    target = make_product(brand="X", specs=[make_spec("voltage", num=12.0)])
    cand = make_product(brand="Y", specs=[make_spec("voltage", text="some text")])
    results = match_by_tech(target, [cand])
    assert len(results) == 1
    note = results[0].breakdown[0].note
    assert note == "type_mismatch"


def test_match_by_tech_excludes_same_brand():
    spec = make_spec("voltage", num=12.0)
    target = make_product(brand="Hikvision", specs=[spec])
    same = make_product(brand="Hikvision", specs=[spec], sku="same")
    other = make_product(brand="Dahua", specs=[spec], sku="other")
    results = match_by_tech(target, [same, other])
    assert len(results) == 1
    assert results[0].candidate.source_sku == "other"


def test_match_by_tech_include_same_brand():
    spec = make_spec("voltage", num=12.0)
    target = make_product(brand="Hikvision", specs=[spec])
    same = make_product(brand="Hikvision", specs=[spec], sku="same")
    results = match_by_tech(target, [same], exclude_same_brand=False)
    assert len(results) == 1


def test_match_by_tech_weight_overrides_change_ranking():
    target = make_product(
        brand="X",
        specs=[
            make_spec("voltage", num=12.0, weight=3.0),
            make_spec("color", text="red", weight=1.0),
        ],
    )

    cand_a = make_product(
        brand="A",
        specs=[
            make_spec("voltage", num=99.0, weight=3.0),
            make_spec("color", text="red", weight=1.0),
        ],
        sku="a",
    )

    cand_b = make_product(
        brand="B",
        specs=[
            make_spec("voltage", num=12.0, weight=3.0),
            make_spec("color", text="blue", weight=1.0),
        ],
        sku="b",
    )

    results_default = match_by_tech(target, [cand_a, cand_b])
    assert results_default[0].candidate.source_sku == "b"

    results_override = match_by_tech(
        target,
        [cand_a, cand_b],
        weight_overrides={"color": 10.0, "voltage": 0.1},
    )
    assert results_override[0].candidate.source_sku == "a"


def test_match_by_tech_no_common_specs_excluded():
    target = make_product(brand="X", specs=[make_spec("voltage", num=12.0)])
    cand = make_product(brand="Y", specs=[make_spec("color", text="red")])
    results = match_by_tech(target, [cand])
    assert results == []


def test_match_by_tech_empty_target_specs():
    target = make_product(brand="X", specs=[])
    cand = make_product(brand="Y", specs=[make_spec("voltage", num=12.0)])
    assert match_by_tech(target, [cand]) == []


def test_match_by_tech_none_target():
    cand = make_product(brand="Y", specs=[make_spec("voltage", num=12.0)])
    assert match_by_tech(None, [cand]) == []


def test_match_by_tech_empty_candidates():
    target = make_product(specs=[make_spec("voltage", num=12.0)])
    assert match_by_tech(target, []) == []


def test_match_by_tech_respects_limit():
    spec = make_spec("voltage", num=12.0)
    target = make_product(brand="X", specs=[spec])
    candidates = [
        make_product(brand=f"B{i}", specs=[spec], sku=f"c{i}") for i in range(10)
    ]
    results = match_by_tech(target, candidates, limit=3)
    assert len(results) == 3


def test_match_by_tech_breakdown_sorted_by_contribution():
    target = make_product(
        brand="X",
        specs=[
            make_spec("voltage", num=12.0, weight=3.0),
            make_spec("ip_rating", text="65", weight=2.5),
        ],
    )
    cand = make_product(
        brand="Y",
        specs=[
            make_spec("voltage", num=12.0, weight=3.0),
            make_spec("ip_rating", text="65", weight=2.5),
        ],
    )
    results = match_by_tech(target, [cand])
    breakdown = results[0].breakdown
    contributions = [f.contribution for f in breakdown]
    assert contributions == sorted(contributions, reverse=True)


def test_match_by_tech_breakdown_fields_present():
    target = make_product(brand="X", specs=[make_spec("voltage", num=12.0, weight=2.0)])
    cand = make_product(brand="Y", specs=[make_spec("voltage", num=12.0, weight=2.0)])
    results = match_by_tech(target, [cand])
    feat = results[0].breakdown[0]
    assert feat.name == "voltage"
    assert feat.target == "12.0"
    assert feat.candidate == "12.0"
    assert feat.similarity == pytest.approx(1.0)
    assert feat.weight == pytest.approx(2.0)
    assert feat.note is None


def test_match_by_tech_canonical_inferred_from_spec_name():
    spec = SimpleNamespace(
        spec_name="питание",
        spec_name_canonical=None,
        spec_value_num=12.0,
        spec_value_text=None,
        weight=1.0,
    )
    target = make_product(brand="X", specs=[spec])
    cand_spec = SimpleNamespace(
        spec_name="питание",
        spec_name_canonical=None,
        spec_value_num=12.0,
        spec_value_text=None,
        weight=1.0,
    )
    cand = make_product(brand="Y", specs=[cand_spec])
    results = match_by_tech(target, [cand])
    assert len(results) == 1
    assert results[0].score == pytest.approx(1.0)


def test_match_by_tech_duplicate_specs_highest_weight_wins():
    spec_low = make_spec("voltage", num=12.0, weight=1.0)
    spec_high = make_spec("voltage", num=24.0, weight=3.0)
    target = make_product(brand="X", specs=[spec_low, spec_high])

    cand = make_product(brand="Y", specs=[make_spec("voltage", num=24.0, weight=3.0)])
    results = match_by_tech(target, [cand])
    assert results[0].score == pytest.approx(1.0)


def test_text_similarity_exact():
    assert _text_similarity("встроенная", "встроенная") == 1.0


def test_text_similarity_paren_stripped():
    assert _text_similarity("встроенная (если не poe)", "встроенная") == pytest.approx(
        0.97
    )


def test_text_similarity_partial_jaccard():
    score = _text_similarity("настенная", "настенный")
    assert 0.0 < score <= 1.0


def test_text_similarity_completely_different():
    score = _text_similarity("красный", "синий")
    assert 0.0 <= score < 1.0


def test_similarity_both_numeric_exact():
    assert _similarity(10.0, None, 10.0, None) == (pytest.approx(1.0), None)


def test_similarity_both_numeric_half():
    sim, note = _similarity(10.0, None, 5.0, None)
    assert sim == pytest.approx(0.5)
    assert note is None


def test_similarity_zero_values():
    sim, note = _similarity(0.0, None, 0.0, None)
    assert sim == pytest.approx(1.0)
    assert note is None


def test_similarity_both_text():
    sim, note = _similarity(None, "abc", None, "abc")
    assert sim == pytest.approx(1.0)
    assert note is None


def test_similarity_type_mismatch_num_vs_text():
    sim, note = _similarity(12.0, None, None, "abc")
    assert sim == pytest.approx(0.0)
    assert note == "type_mismatch"


def test_similarity_type_mismatch_text_vs_num():
    sim, note = _similarity(None, "abc", 12.0, None)
    assert sim == pytest.approx(0.0)
    assert note == "type_mismatch"


def test_similarity_both_none():
    sim, note = _similarity(None, None, None, None)
    assert sim == pytest.approx(0.0)
