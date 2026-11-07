"""T-KB-06 · KB-R6: reciprocal rank fusion maths (pure)."""

import pytest

from supportagent.kb.retrieval import RRF_K, rrf


def test_scores_are_sum_of_reciprocal_ranks() -> None:
    fused = dict(rrf([[10, 20, 30], [20, 40]]))
    assert fused[20] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert fused[10] == pytest.approx(1 / (RRF_K + 1))
    assert fused[30] == pytest.approx(1 / (RRF_K + 3))
    assert fused[40] == pytest.approx(1 / (RRF_K + 2))


def test_items_in_both_lists_rank_first() -> None:
    assert rrf([[1, 2, 3], [3, 4, 5]])[0][0] == 3


def test_one_sided_candidates_are_kept() -> None:
    assert [i for i, _ in rrf([[7, 8], []])] == [7, 8]
    assert [i for i, _ in rrf([[], [9]])] == [9]
    assert rrf([[], []]) == []


def test_ties_break_on_best_rank_then_id() -> None:
    # 5 and 6 each appear once at rank 1 → equal scores, equal best rank → lower id first.
    assert [i for i, _ in rrf([[6], [5]])] == [5, 6]
    # equal scores but different best ranks: 1 is rank 1 then rank 3; 2 is rank 2 twice
    scores = dict(rrf([[1, 2, 3], [4, 2, 1]], k=0))
    assert scores[1] == pytest.approx(1 / 1 + 1 / 3)
    assert scores[2] == pytest.approx(1 / 2 + 1 / 2)


def test_k_dampens_rank_differences() -> None:
    near = dict(rrf([[1, 2]], k=60))
    assert near[1] / near[2] == pytest.approx(62 / 61)
