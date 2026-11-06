"""T-KB-01 · KB-R1: product grace days and late fees in the corpus equal the bank seed's products.

The bank seed (T-BANK-02) exports `PRODUCTS` from `supportagent.bank.seed`. Until that task lands this
test is skipped with a reason, so the cross-check switches on automatically.
"""

import importlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from supportagent.kb.frontmatter import load_corpus

POLICIES = Path(__file__).resolve().parents[2] / "data" / "policies"


def _bank_products() -> list[Any]:
    try:
        seed = importlib.import_module("supportagent.bank.seed")
    except ModuleNotFoundError:
        pytest.skip("supportagent.bank.seed not implemented yet (T-BANK-02)")
    products = getattr(seed, "PRODUCTS", None)
    if products is None:
        pytest.skip("supportagent.bank.seed.PRODUCTS not exported yet (T-BANK-02)")
    return list(products.values() if isinstance(products, dict) else products)


def _get(obj: Any, key: str) -> Any:
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def _rule(raw: Any) -> tuple[str, Decimal, Decimal | None]:
    if not isinstance(raw, dict):
        raw = raw.model_dump() if hasattr(raw, "model_dump") else vars(raw)
    cap = raw.get("cap")
    return str(raw["type"]), Decimal(str(raw["value"])), None if cap is None else Decimal(str(cap))


def test_grace_days_and_late_fees_match_bank_seed() -> None:
    bank = {_get(p, "code"): p for p in _bank_products()}
    docs = {d.meta.product: d.meta for d in load_corpus(POLICIES) if d.meta.product != "all"}
    assert set(bank) == set(docs)
    for code, meta in docs.items():
        product = bank[code]
        assert _get(product, "grace_days") == meta.grace_days, code
        assert meta.late_fee is not None
        assert _rule(_get(product, "late_fee_rule")) == (
            meta.late_fee.type,
            meta.late_fee.value,
            meta.late_fee.cap,
        )
        assert _get(product, "policy_doc_id") == meta.doc_id, code
