"""T-KB-01 · KB-R1: the synthetic policy corpus parses, is complete and states the agreed figures."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from supportagent.kb.frontmatter import PRODUCT_CODES, FrontMatterError, PolicyDoc, load_corpus, parse

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "data" / "policies"

# specs/03-knowledge-base/plan.md#corpus — the 20 documents and their product/audience.
EXPECTED_DOCS = {
    "product-personal-loan": ("PERSONAL", "customer"),
    "product-auto-loan": ("AUTO", "customer"),
    "product-mortgage": ("MORTGAGE", "customer"),
    "product-sme-working-capital": ("SME_WC", "customer"),
    "product-salary-advance": ("SALARY_ADV", "customer"),
    "repayment-schedules": ("all", "customer"),
    "late-fees-penalty-interest": ("all", "customer"),
    "delinquency-stages": ("all", "customer"),
    "credit-bureau-reporting": ("all", "customer"),
    "hardship-assistance": ("all", "customer"),
    "promise-to-pay-rules": ("all", "customer"),
    "payment-channels": ("all", "customer"),
    "early-repayment-payoff": ("all", "customer"),
    "disputes-error-resolution": ("all", "customer"),
    "complaints-escalation": ("all", "customer"),
    "identity-verification-privacy": ("all", "internal"),
    "contact-calling-rules": ("all", "internal"),
    "required-disclosures": ("all", "internal"),
    "faq": ("all", "customer"),
    "fees-schedule": ("all", "customer"),
}

# Plan table: grace days and late-fee rule per product (the bank seed must use the same values).
PRODUCT_TERMS = {
    "PERSONAL": (15, "flat", Decimal("25"), None, ["15-day grace period", "$25"]),
    "AUTO": (10, "pct", Decimal("5"), Decimal("50"), ["10-day grace period", "5%", "$50"]),
    "MORTGAGE": (15, "pct", Decimal("4"), None, ["15-day grace period", "4%"]),
    "SME_WC": (7, "flat", Decimal("75"), None, ["7-day grace period", "$75"]),
    "SALARY_ADV": (3, "flat", Decimal("10"), None, ["3-day grace period", "$10"]),
}

REAL_BANKS = [
    "Chase", "JPMorgan", "Wells Fargo", "Bank of America", "Citibank", "Citigroup", "Capital One",
    "HSBC", "Barclays", "Goldman Sachs", "Morgan Stanley", "U.S. Bank", "PNC", "Truist", "TD Bank",
    "Santander", "Ally Bank", "Discover", "American Express", "Synchrony",
]  # fmt: skip


@pytest.fixture(scope="module")
def corpus() -> dict[str, PolicyDoc]:
    return {d.meta.doc_id: d for d in load_corpus(POLICIES)}


def _text(doc: PolicyDoc) -> str:
    return " ".join(doc.body.split())  # collapse line wrapping


def test_exactly_the_planned_documents(corpus: dict[str, PolicyDoc]) -> None:
    assert set(corpus) == set(EXPECTED_DOCS)


@pytest.mark.parametrize("doc_id", sorted(EXPECTED_DOCS))
def test_front_matter_values(corpus: dict[str, PolicyDoc], doc_id: str) -> None:
    meta = corpus[doc_id].meta
    assert (meta.product, meta.audience) == EXPECTED_DOCS[doc_id]
    assert meta.version == "1.0"
    assert meta.effective_date.isoformat() == "2026-01-01"


@pytest.mark.parametrize("doc_id", sorted(EXPECTED_DOCS))
def test_structure_title_and_two_to_six_sections(corpus: dict[str, PolicyDoc], doc_id: str) -> None:
    body = corpus[doc_id].body
    assert body.lstrip().startswith(f"# {corpus[doc_id].meta.title}\n")
    sections = re.findall(r"^## \S.*$", body, flags=re.M)
    assert 2 <= len(sections) <= 6, sections


def test_each_product_code_has_exactly_one_product_doc(corpus: dict[str, PolicyDoc]) -> None:
    products = [d.meta.product for d in corpus.values() if d.meta.product != "all"]
    assert sorted(products) == sorted(PRODUCT_CODES)


@pytest.mark.parametrize("code", PRODUCT_CODES)
def test_product_terms_match_plan_and_are_stated(corpus: dict[str, PolicyDoc], code: str) -> None:
    (doc,) = [d for d in corpus.values() if d.meta.product == code]
    grace, fee_type, value, cap, phrases = PRODUCT_TERMS[code]
    assert doc.meta.grace_days == grace
    assert doc.meta.late_fee is not None
    assert (doc.meta.late_fee.type, doc.meta.late_fee.value, doc.meta.late_fee.cap) == (fee_type, value, cap)
    for phrase in phrases:
        assert phrase in _text(doc), f"{doc.meta.doc_id} must state '{phrase}'"


def test_fee_tables_agree_with_product_docs(corpus: dict[str, PolicyDoc]) -> None:
    for doc_id in ("late-fees-penalty-interest", "fees-schedule"):
        text = _text(corpus[doc_id])
        for _grace, _t, _v, _c, phrases in PRODUCT_TERMS.values():
            for phrase in phrases[1:]:  # the fee amounts
                assert phrase in text, f"{doc_id} must list '{phrase}'"
    grace_text = _text(corpus["late-fees-penalty-interest"])
    for grace, *_ in PRODUCT_TERMS.values():
        assert f"| {grace} days |" in grace_text


@pytest.mark.parametrize(
    ("doc_id", "phrases"),
    [
        ("delinquency-stages", ["30 days past due", "60 days past due", "90 days past due", "credit bureau"]),
        ("credit-bureau-reporting", ["30 days past due"]),
        ("promise-to-pay-rules", ["14 days", "partial payment", "broken"]),
        ("disputes-error-resolution", ["paused", "30 days"]),
        ("hardship-assistance", ["specialist", "decides", "restructur"]),
        ("identity-verification-privacy", ["date of birth", "last 4 digits", "two failed", "third party"]),
        ("payment-channels", ["mobile", "branch", "transfer", "2 business days", "pending"]),
        ("early-repayment-payoff", ["2%", "1%", "payoff quote"]),
        ("contact-calling-rules", ["08:00", "21:00", "7 call attempts", "7 days", "do-not-call", "consent"]),
    ],
)
def test_policy_values_from_architecture(
    corpus: dict[str, PolicyDoc], doc_id: str, phrases: list[str]
) -> None:
    text = _text(corpus[doc_id]).lower()
    for phrase in phrases:
        assert phrase.lower() in text, f"{doc_id} must state '{phrase}'"


def test_required_disclosures_doc_has_each_versioned_text(corpus: dict[str, PolicyDoc]) -> None:
    body = corpus["required-disclosures"].body
    for key in ("ai_recording", "collections", "third_party_neutral"):
        m = re.search(rf"^## {key} \(version 1\.0\)\n+> (.+)$", body, flags=re.M)
        assert m, f"required-disclosures must have a '## {key} (version 1.0)' section with a '> text' line"


def test_no_real_banks_people_or_phone_numbers(corpus: dict[str, PolicyDoc]) -> None:
    for doc in corpus.values():
        raw = doc.body  # front-matter holds an ISO date, which would look like a number run
        assert not re.search(r"\+?\d[\d\s().-]{8,}\d", raw), f"{doc.meta.doc_id}: looks like a phone number"
        assert not re.search(r"\b\d{3}[-.]\d{4}\b", raw), f"{doc.meta.doc_id}: looks like a phone number"
        for bank in REAL_BANKS:
            assert bank not in raw, f"{doc.meta.doc_id}: mentions real institution '{bank}'"
        names = set(re.findall(r"\b[A-Z][a-z]+ Bank\b", raw))
        assert names <= {"Meridian Bank"}, names


# --------------------------------------------------------------------------- parser behaviour

GOOD = """---
doc_id: faq
title: FAQ
product: all
version: "1.0"
effective_date: 2026-01-01
audience: customer
---
# FAQ
"""


def test_parse_ok() -> None:
    doc = parse(GOOD)
    assert doc.meta.doc_id == "faq"
    assert doc.body.startswith("# FAQ")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("product: all", "product: CREDIT_CARD"), "product"),
        (("audience: customer", "audience: public"), "audience"),
        (('version: "1.0"', "version: v1"), "version"),
        (("effective_date: 2026-01-01", "effective_date: soon"), "effective_date"),
        (("doc_id: faq", "doc_id: FAQ Doc"), "doc_id"),
        (("audience: customer\n", "audience: customer\nowner: me\n"), "owner"),
        (("product: all", "product: all\ngrace_days: 3"), "only allowed on product"),
        (("product: all", "product: AUTO"), "must set grace_days"),
        (("title: FAQ\n", ""), "title"),
    ],
)
def test_parse_rejects_invalid_front_matter(change: tuple[str, str], message: str) -> None:
    with pytest.raises(FrontMatterError, match=message):
        parse(GOOD.replace(*change), source="x.md")


def test_parse_requires_front_matter_block() -> None:
    with pytest.raises(FrontMatterError, match="missing"):
        parse("# No front matter\n")
    with pytest.raises(FrontMatterError, match="mapping"):
        parse("---\n- a list\n---\n# x\n")
    with pytest.raises(FrontMatterError, match="invalid YAML"):
        parse("---\ndoc_id: [unclosed\n---\n# x\n")


def test_doc_id_must_match_file_name_and_be_unique(tmp_path: Path) -> None:
    (tmp_path / "faq.md").write_text(GOOD)
    assert [d.meta.doc_id for d in load_corpus(tmp_path)] == ["faq"]
    (tmp_path / "other.md").write_text(GOOD)
    with pytest.raises(FrontMatterError, match="must equal the file name"):
        load_corpus(tmp_path)
