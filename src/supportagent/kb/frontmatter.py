"""Policy document front-matter: parse and validate (KB-R1).

A policy document is Markdown with a YAML front-matter block::

    ---
    doc_id: product-personal-loan
    title: Personal Loan — Terms
    product: PERSONAL            # product code or "all"
    version: "1.0"
    effective_date: 2026-01-01
    audience: customer           # customer | internal
    grace_days: 15               # product docs only
    late_fee: {type: flat, value: 25}
    ---
    # Personal Loan — Terms
    ...

`grace_days` and `late_fee` are required on product documents (product ≠ "all") and forbidden on
the others. They carry the same values as the bank's `loan_products` rows (T-BANK-02), so the
cross-check test can compare them exactly rather than by parsing prose.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

PRODUCT_CODES = ("PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV")
Product = Literal["PERSONAL", "AUTO", "MORTGAGE", "SME_WC", "SALARY_ADV", "all"]

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?(.*)\Z", re.DOTALL)


class FrontMatterError(ValueError):
    """A policy document has missing or invalid front-matter."""


class LateFeeRule(BaseModel):
    """Same shape as the bank's `loan_products.late_fee_rule` JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["flat", "pct"]
    value: Decimal = Field(gt=0)  # dollars for flat, percent of the overdue instalment for pct
    cap: Decimal | None = Field(None, gt=0)  # dollars; only meaningful for pct


class PolicyDocMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str = Field(min_length=3)
    product: Product
    version: str = Field(pattern=r"^\d+\.\d+$")
    effective_date: dt.date
    audience: Literal["customer", "internal"]
    grace_days: int | None = Field(None, ge=0)
    late_fee: LateFeeRule | None = None

    @model_validator(mode="after")
    def _product_terms(self) -> Self:
        is_product_doc = self.product != "all"
        has_terms = self.grace_days is not None and self.late_fee is not None
        if is_product_doc and not has_terms:
            raise ValueError("product documents must set grace_days and late_fee")
        if not is_product_doc and (self.grace_days is not None or self.late_fee is not None):
            raise ValueError("grace_days and late_fee are only allowed on product documents")
        return self


class PolicyDoc(BaseModel):
    model_config = ConfigDict(frozen=True)

    meta: PolicyDocMeta
    body: str
    path: Path | None = None


def parse(text: str, *, source: str = "<string>") -> PolicyDoc:
    """Split and validate front-matter. Raises `FrontMatterError` naming the source."""
    m = _FRONT_MATTER.match(text)
    if not m:
        raise FrontMatterError(f"{source}: missing '---' front-matter block")
    try:
        raw = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"{source}: invalid YAML front-matter: {exc}") from exc
    if not isinstance(raw, dict):
        raise FrontMatterError(f"{source}: front-matter must be a mapping")
    try:
        meta = PolicyDocMeta.model_validate(raw)
    except ValidationError as exc:
        raise FrontMatterError(f"{source}: {exc}") from exc
    return PolicyDoc(meta=meta, body=m.group(2))


def load(path: Path) -> PolicyDoc:
    """Parse one document; its `doc_id` must equal the file name (without `.md`)."""
    doc = parse(path.read_text(encoding="utf-8"), source=str(path))
    if doc.meta.doc_id != path.stem:
        raise FrontMatterError(f"{path}: doc_id '{doc.meta.doc_id}' must equal the file name '{path.stem}'")
    return doc.model_copy(update={"path": path})


def load_corpus(directory: Path) -> list[PolicyDoc]:
    """All `*.md` documents in `directory`, sorted by file name; doc_ids must be unique."""
    docs = [load(p) for p in sorted(directory.glob("*.md"))]
    seen: set[str] = set()
    for d in docs:
        if d.meta.doc_id in seen:
            raise FrontMatterError(f"duplicate doc_id '{d.meta.doc_id}'")
        seen.add(d.meta.doc_id)
    return docs
