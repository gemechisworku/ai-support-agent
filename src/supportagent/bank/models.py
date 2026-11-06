"""SQLAlchemy models for the `bank` database (BANK-R1). Migration: migrations/bank/versions/bank_0002_*.py.

Ledger tables (customers, accounts, loan_products, loans, installments, transactions) are written only
by the seed. The API writes only the interaction tables (interactions, promises_to_pay, tasks,
contact_preferences flags, audit_log, outbox) — constitution II.3.

`call_id` on promises, tasks and audit rows is a plain column, not a foreign key: the gateway records
them during a call, before the interaction row exists (it is written when the call ends).
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from supportagent.common.db import BankBase

MONEY = Numeric(12, 2)
RATE = Numeric(5, 2)


def _in(column: str, *values: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"{column}_valid")


class Customer(BankBase):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(Text)
    dob: Mapped[dt.date] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(Text, unique=True, index=True)
    email: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    segment: Mapped[str] = mapped_column(Text)
    kyc_status: Mapped[str] = mapped_column(Text)
    preferred_language: Mapped[str] = mapped_column(Text, server_default="en")


class Account(BankBase):
    __tablename__ = "accounts"
    __table_args__ = (_in("type", "current", "savings"), _in("status", "active", "closed"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    number: Mapped[str] = mapped_column(String(12), unique=True)
    balance: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16))


class LoanProduct(BankBase):
    __tablename__ = "loan_products"
    __table_args__ = (_in("rate_type", "fixed", "variable"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    rate_type: Mapped[str] = mapped_column(String(16))
    apr_min: Mapped[Decimal] = mapped_column(RATE)
    apr_max: Mapped[Decimal] = mapped_column(RATE)
    term_min: Mapped[int] = mapped_column(Integer)
    term_max: Mapped[int] = mapped_column(Integer)
    grace_days: Mapped[int] = mapped_column(Integer)
    late_fee_rule: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy_doc_id: Mapped[str] = mapped_column(Text)


class Loan(BankBase):
    __tablename__ = "loans"
    __table_args__ = (
        _in("status", "active", "closed", "default"),
        _in("bucket", "current", "1-30", "31-60", "61-90", "90+"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("loan_products.code"))
    number: Mapped[str] = mapped_column(String(10), unique=True)
    principal: Mapped[Decimal] = mapped_column(MONEY)
    apr: Mapped[Decimal] = mapped_column(RATE)
    term_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    days_past_due: Mapped[int] = mapped_column(Integer, server_default="0")
    bucket: Mapped[str] = mapped_column(String(8), server_default="current")
    repayment_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))


class Installment(BankBase):
    __tablename__ = "installments"
    __table_args__ = (_in("status", "scheduled", "paid", "partial", "missed"),)

    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    due_date: Mapped[dt.date] = mapped_column(Date, index=True)
    principal_due: Mapped[Decimal] = mapped_column(MONEY)
    interest_due: Mapped[Decimal] = mapped_column(MONEY)
    fee_due: Mapped[Decimal] = mapped_column(MONEY, server_default="0")
    paid_amount: Mapped[Decimal] = mapped_column(MONEY, server_default="0")
    paid_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))


class Transaction(BankBase):
    __tablename__ = "transactions"
    __table_args__ = (
        _in("channel", "branch", "mobile", "transfer"),
        _in("status", "posted", "pending"),
        Index("ix_transactions_account_id_ts", "account_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    loan_id: Mapped[int | None] = mapped_column(ForeignKey("loans.id"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str] = mapped_column(Text)


class ContactPreference(BankBase):
    __tablename__ = "contact_preferences"

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    consent_voice_ai: Mapped[bool] = mapped_column(Boolean)
    dnc: Mapped[bool] = mapped_column(Boolean, server_default="false")
    preferred_window: Mapped[str | None] = mapped_column(String(11))
    cease_contact: Mapped[bool] = mapped_column(Boolean, server_default="false")
    dispute_open: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Interaction(BankBase):
    __tablename__ = "interactions"
    __table_args__ = (
        _in("direction", "inbound", "outbound"),
        Index("ix_interactions_loan_id_started_at", "loan_id", "started_at"),
    )

    call_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    loan_id: Mapped[int | None] = mapped_column(ForeignKey("loans.id"))
    direction: Mapped[str] = mapped_column(String(16))
    campaign: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    contact_result: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    transcript_uri: Mapped[str | None] = mapped_column(Text)
    recording_uri: Mapped[str | None] = mapped_column(Text)
    versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class PromiseToPay(BankBase):
    __tablename__ = "promises_to_pay"
    __table_args__ = (_in("status", "open", "kept", "broken"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str | None] = mapped_column(Text, index=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), index=True)
    promised_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(BankBase):
    __tablename__ = "tasks"
    __table_args__ = (
        _in("type", "callback", "escalation"),
        _in("status", "open", "done", "cancelled"),
        Index("ix_tasks_status_due_at", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    loan_id: Mapped[int | None] = mapped_column(ForeignKey("loans.id"))
    type: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, server_default="")
    due_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    owner: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(BankBase):
    __tablename__ = "audit_log"
    __table_args__ = (_in("decision", "allow", "deny", "error"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    call_id: Mapped[str] = mapped_column(Text, index=True)
    tool: Mapped[str] = mapped_column(Text)
    args_hash: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(8))
    latency_ms: Mapped[int] = mapped_column(Integer)


class Outbox(BankBase):
    __tablename__ = "outbox"
    __table_args__ = (_in("channel", "sms", "email"), _in("status", "pending", "sent"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("interactions.call_id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(8))
    recipient: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), server_default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


TABLES = [
    "customers",
    "accounts",
    "loan_products",
    "loans",
    "installments",
    "transactions",
    "contact_preferences",
    "interactions",
    "promises_to_pay",
    "tasks",
    "audit_log",
    "outbox",
]
