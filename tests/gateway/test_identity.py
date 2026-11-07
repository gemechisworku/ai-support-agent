"""T-GW-05 · GW-R3: name normalisation and matching."""

import pytest

from supportagent.gateway.identity import levenshtein, names_match, normalise_name


def test_normalise_name() -> None:
    assert normalise_name("José  O'Neil") == ["jose", "oneil"]
    assert normalise_name("jose oneil") == ["jose", "oneil"]
    assert normalise_name("  FRANK   Fischer-Smith ") == ["frank", "fischersmith"]


def test_levenshtein() -> None:
    assert levenshtein("fischer", "fisher") == 1
    assert levenshtein("frank", "frank") == 0
    assert levenshtein("frank", "fronk") == 1
    assert levenshtein("frank", "frunky") == 2


@pytest.mark.parametrize(
    ("spoken", "ok"),
    [("Frank Fischer", True), ("frank fisher", True), ("Frank J. Fischer", True), ("Frenk Fischer", True),
     ("Franz Fisher", True), ("Fred Fischer", False), ("Frank Fishman", False), ("Frank", False),
     ("Fischer Frank", False)],
)  # fmt: skip
def test_names_match_within_one_edit_per_token(spoken: str, ok: bool) -> None:
    assert names_match(spoken, "Frank Fischer") is ok
