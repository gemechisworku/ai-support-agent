"""The five loan products. Terms equal the front matter and text of data/policies/product-*.md
(tests/kb/test_corpus_bank_consistency.py keeps them in step)."""

from decimal import Decimal

from supportagent.contracts.bank import LateFeeRule, LoanProduct

PRODUCTS: dict[str, LoanProduct] = {
    p.code: p
    for p in [
        LoanProduct(
            code="PERSONAL",
            name="Personal Loan",
            rate_type="fixed",
            apr_min=Decimal("8.99"),
            apr_max=Decimal("24.99"),
            term_min=12,
            term_max=60,
            grace_days=15,
            late_fee_rule=LateFeeRule(type="flat", value=Decimal("25")),
            policy_doc_id="product-personal-loan",
        ),
        LoanProduct(
            code="AUTO",
            name="Auto Loan",
            rate_type="fixed",
            apr_min=Decimal("5.49"),
            apr_max=Decimal("14.99"),
            term_min=24,
            term_max=72,
            grace_days=10,
            late_fee_rule=LateFeeRule(type="pct", value=Decimal("5"), cap=Decimal("50")),
            policy_doc_id="product-auto-loan",
        ),
        LoanProduct(
            code="MORTGAGE",
            name="Home Mortgage",
            rate_type="variable",
            apr_min=Decimal("5.25"),
            apr_max=Decimal("7.75"),
            term_min=120,
            term_max=360,
            grace_days=15,
            late_fee_rule=LateFeeRule(type="pct", value=Decimal("4")),
            policy_doc_id="product-mortgage",
        ),
        LoanProduct(
            code="SME_WC",
            name="Small-Business Working Capital Loan",
            rate_type="variable",
            apr_min=Decimal("9.50"),
            apr_max=Decimal("19.50"),
            term_min=6,
            term_max=36,
            grace_days=7,
            late_fee_rule=LateFeeRule(type="flat", value=Decimal("75")),
            policy_doc_id="product-sme-working-capital",
        ),
        LoanProduct(
            code="SALARY_ADV",
            name="Salary-Advance Microloan",
            rate_type="fixed",
            apr_min=Decimal("18.00"),
            apr_max=Decimal("35.99"),
            term_min=1,
            term_max=6,
            grace_days=3,
            late_fee_rule=LateFeeRule(type="flat", value=Decimal("10")),
            policy_doc_id="product-salary-advance",
        ),
    ]
}


def product_row(p: LoanProduct) -> dict[str, object]:
    return {
        "code": p.code,
        "name": p.name,
        "rate_type": p.rate_type,
        "apr_min": p.apr_min,
        "apr_max": p.apr_max,
        "term_min": p.term_min,
        "term_max": p.term_max,
        "grace_days": p.grace_days,
        "late_fee_rule": p.late_fee_rule.model_dump(mode="json", exclude_none=True),
        "policy_doc_id": p.policy_doc_id,
    }
