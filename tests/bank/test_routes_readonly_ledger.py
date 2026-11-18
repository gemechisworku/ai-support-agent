"""T-BANK-06 · BANK-R7: no route can mutate balances, loans, instalments or transactions (no database).

The route table is read from the OpenAPI schema, which lists every operation regardless of how
FastAPI nests included routers internally.
"""

from supportagent.bank.api import create_app

LEDGER_PREFIXES = ("/accounts", "/loans", "/installments", "/transactions")
ALLOWED_WRITES = {
    ("POST", "/interactions"),
    ("POST", "/promises"),
    ("POST", "/tasks"),
    ("PATCH", "/tasks/{task_id}"),  # T-BANK-07: the console's follow-up queue
    ("PATCH", "/contact-preferences/{customer_id}"),
    ("POST", "/audit"),
}


def _routes() -> set[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return {(method.upper(), path) for path, ops in paths.items() for method in ops}


def test_route_table_is_not_empty() -> None:
    routes = _routes()
    assert ("GET", "/loans/{loan_id}") in routes
    assert ("GET", "/healthz") in routes


def test_ledger_resources_are_read_only() -> None:
    for method, path in _routes():
        if path.startswith(LEDGER_PREFIXES):
            assert method == "GET", f"{method} {path} would mutate the ledger"


def test_the_only_writes_are_interaction_tables() -> None:
    writes = {(m, p) for m, p in _routes() if m != "GET"}
    assert writes == ALLOWED_WRITES
