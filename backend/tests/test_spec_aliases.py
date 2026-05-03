import pytest

from app.normalization.spec_aliases import canonicalize_spec_name, weight_for


@pytest.mark.parametrize(
    "name, expected",
    [
        ("питание", "power"),
        ("потребление питания", "power"),
        ("разрешение", "display_resolution"),
        ("класс защиты", "ip_rating"),
        ("рабочая температура", "temperature_range"),
        ("вес", "weight"),
        ("power", "power"),
        ("resolution", "display_resolution"),
    ],
)
def test_canonicalize_known_aliases(name, expected):
    assert canonicalize_spec_name(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "image",
        "артикул",
        "производитель",
    ],
)
def test_canonicalize_excluded_names(name):
    assert canonicalize_spec_name(name) == ""


@pytest.mark.parametrize("name", [None, ""])
def test_canonicalize_empty_or_none(name):
    assert canonicalize_spec_name(name) == ""


def test_canonicalize_whitespace_only():
    assert canonicalize_spec_name("   ") == ""


def test_canonicalize_strips_and_lowercases():
    assert canonicalize_spec_name("  ПИТАНИЕ  ") == "power"


def test_canonicalize_nbsp_stripped():

    assert canonicalize_spec_name("питание\xa0") == "power"


def test_canonicalize_non_breaking_hyphen():

    result = canonicalize_spec_name("класс‑защиты")

    assert isinstance(result, str)


def test_canonicalize_unknown_passthrough():
    result = canonicalize_spec_name("some_totally_unknown_spec")
    assert result == "some_totally_unknown_spec"


def test_canonicalize_unknown_preserves_lowercase():
    result = canonicalize_spec_name("MyCustomSpec")
    assert result == "mycustomspec"


@pytest.mark.parametrize(
    "canonical, expected",
    [
        ("display_resolution", 3.0),
        ("ip_rating", 2.5),
        ("temperature_range", 2.5),
        ("weight", 1.0),
    ],
)
def test_weight_for_known_canonicals(canonical, expected):
    assert weight_for(canonical) == pytest.approx(expected)


@pytest.mark.parametrize(
    "canonical",
    [
        "completely_unknown_canonical_xyz",
        "",
        "random_spec_that_does_not_exist",
    ],
)
def test_weight_for_unknown_returns_default(canonical):
    assert weight_for(canonical) == pytest.approx(1.0)
