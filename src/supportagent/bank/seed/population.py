"""The random synthetic population (BANK-R2). Deterministic for a given `seed` and `as_of`.

Every value comes from `random.Random(seed)` or a Faker instance seeded with the same `seed`; nothing
reads the wall clock. Phones are in the reserved +1555 range (outside the personas' +155501003NN
block), emails use example.com.

Loan counts per product and per delinquency bucket are fixed quotas (shuffled), so totals stay near the
architecture targets for any seed: ~200 customers, 260 accounts, 320 loans, ~9,000 instalments and
exactly `n_transactions` transactions.
"""

import datetime as dt
import itertools
import random
from decimal import Decimal

from faker import Faker

from supportagent.bank import ledger
from supportagent.bank.seed.builder import LoanPlan, SeedData, build_loan
from supportagent.bank.seed.products import PRODUCTS, product_row

TIMEZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
)
SEGMENTS = ("retail", "retail", "retail", "premium", "sme")

# Share of loans per product (fractions of n_loans) and the term choices used for each.
PRODUCT_SHARE = {"PERSONAL": 0.37, "AUTO": 0.22, "MORTGAGE": 0.03, "SME_WC": 0.14, "SALARY_ADV": 0.24}
TERMS = {
    "PERSONAL": (12, 24, 36, 48),
    "AUTO": (24, 36, 48, 60),
    "MORTGAGE": (120, 180),
    "SME_WC": (6, 12, 18, 24, 36),
    "SALARY_ADV": (1, 3, 6),
}
PRINCIPAL = {  # (min, max) in dollars, rounded to 50
    "PERSONAL": (2_000, 25_000),
    "AUTO": (8_000, 45_000),
    "MORTGAGE": (120_000, 600_000),
    "SME_WC": (10_000, 150_000),
    "SALARY_ADV": (300, 2_500),
}
# Share of loans per bucket and the DPD range drawn for each.
BUCKET_SHARE = {"current": 0.70, "1-30": 0.17, "31-60": 0.06, "61-90": 0.04, "90+": 0.03}
DPD_RANGE = {"current": (0, 0), "1-30": (1, 30), "31-60": (31, 60), "61-90": (61, 90), "90+": (91, 150)}

POPULATION_PHONE_MIN = 2_000_000  # +1555 2000000 … 9999999 (personas use +1555 0100301…)


def _quotas(shares: dict[str, float], n: int) -> list[str]:
    counts = {k: round(v * n) for k, v in shares.items()}
    first = next(iter(counts))
    counts[first] += n - sum(counts.values())  # absorb rounding
    return [k for k, c in counts.items() for _ in range(c)]


def _apr(rng: random.Random, lo: Decimal, hi: Decimal) -> Decimal:
    cents = rng.randint(int(lo * 100), int(hi * 100))
    return (Decimal(cents) / 100).quantize(ledger.CENT)


def generate_population(
    seed: int,
    as_of: dt.date,
    *,
    n_customers: int = 200,
    n_accounts: int = 260,
    n_loans: int = 320,
    n_transactions: int = 12_000,
) -> SeedData:
    rng = random.Random(seed)  # noqa: S311  (deterministic synthetic data, not security)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    data = SeedData(loan_products=[product_row(p) for p in PRODUCTS.values()])
    now = dt.datetime(as_of.year, as_of.month, as_of.day, tzinfo=dt.UTC)

    phones = rng.sample(range(POPULATION_PHONE_MIN, 10_000_000), n_customers)
    for cid in range(1, n_customers + 1):
        first, last = fake.first_name(), fake.last_name()
        dob = dt.date(1950, 1, 1) + dt.timedelta(days=rng.randint(0, 20_000))
        data.customers.append(
            {
                "id": cid,
                "full_name": f"{first} {last}",
                "dob": dob,
                "phone": f"+1555{phones[cid - 1]:07d}",
                "email": f"{first}.{last}.{cid}@example.com".lower().replace("'", ""),
                "address": fake.address().replace("\n", ", "),
                "timezone": rng.choice(TIMEZONES),
                "segment": rng.choice(SEGMENTS),
                "kyc_status": "verified" if rng.random() < 0.97 else "pending",
                "preferred_language": "en",
            }
        )
        data.contact_preferences.append(
            {
                "customer_id": cid,
                "consent_voice_ai": rng.random() < 0.92,
                "dnc": rng.random() < 0.03,
                "preferred_window": rng.choice(["09:00-12:00", "12:00-17:00", "18:00-20:00"])
                if rng.random() < 0.2
                else None,
                "cease_contact": rng.random() < 0.02,
                "dispute_open": rng.random() < 0.02,
                "updated_at": now - dt.timedelta(days=rng.randint(30, 400)),
            }
        )

    # Accounts: one current account per customer, savings accounts for a random subset.
    current_account: dict[int, int] = {}
    savers = sorted(rng.sample(range(1, n_customers + 1), max(0, n_accounts - n_customers)))
    owners = [(cid, "current") for cid in range(1, n_customers + 1)] + [(cid, "savings") for cid in savers]
    for aid, (cid, kind) in enumerate(owners, start=1):
        if kind == "current":
            current_account[cid] = aid
        data.accounts.append(
            {
                "id": aid,
                "customer_id": cid,
                "type": kind,
                "number": f"{300_000_000_000 + aid:012d}",
                "balance": ledger.money(Decimal(rng.randint(0, 2_000_000)) / 100),
                "status": "active",
            }
        )

    # Loans: every customer has one, a random subset a second.
    borrowers = list(range(1, n_customers + 1)) + rng.sample(range(1, n_customers + 1), n_loans - n_customers)
    products = _quotas(PRODUCT_SHARE, n_loans)
    buckets = _quotas(BUCKET_SHARE, n_loans)
    rng.shuffle(products)
    rng.shuffle(buckets)
    tx_ids = itertools.count(1)
    for lid, (cid, code, target) in enumerate(zip(borrowers, products, buckets, strict=True), start=1):
        product = PRODUCTS[code]
        term = rng.choice(TERMS[code])
        lo, hi = PRINCIPAL[code]
        principal = Decimal(rng.randint(lo // 50, hi // 50) * 50)
        dpd = rng.randint(*DPD_RANGE[target])
        if target == "current":
            anchor = as_of + dt.timedelta(days=rng.randint(0, 30))
        else:
            anchor = as_of - dt.timedelta(days=dpd)
        plan = LoanPlan(
            loan_id=lid,
            customer_id=cid,
            account_id=current_account[cid],
            product=product,
            number=f"51{lid:08d}",
            principal=principal,
            apr=_apr(rng, product.apr_min, product.apr_max),
            term=term,
            anchor=anchor,
            anchor_seq=rng.randint(1, term),
        )
        if target != "current" and rng.random() < 0.2:
            plan.partial_paid = ledger.money(principal / term / 3)
        loan, insts, txs = build_loan(plan, as_of, rng, tx_ids)
        data.loans.append(loan)
        data.installments.extend(insts)
        data.transactions.extend(txs)

    # Other account activity, so the transaction table reaches its target size.
    for _ in range(max(0, n_transactions - len(data.transactions))):
        account = rng.choice(data.accounts)
        day = as_of - dt.timedelta(days=rng.randint(1, 120))
        tid = next(tx_ids)
        data.transactions.append(
            {
                "id": tid,
                "account_id": account["id"],
                "loan_id": None,
                "ts": dt.datetime(
                    day.year, day.month, day.day, rng.randint(0, 23), rng.randint(0, 59), tzinfo=dt.UTC
                ),
                "amount": ledger.money(Decimal(rng.randint(500, 300_000)) / 100),
                "channel": rng.choice(("branch", "mobile", "transfer")),
                "status": "posted",
                "reference": f"TX-{tid:07d}",
            }
        )
    return data
