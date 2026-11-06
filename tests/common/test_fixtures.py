"""T-PLAT-05 · PLAT-R6: migrated test databases with per-test rollback (`bank_db`, `kb_db`).

The probe tables are created and committed outside the per-test transaction (module fixture), so
the isolation tests below prove that each test's writes are rolled back.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from supportagent.common.db import make_engine
from supportagent.common.settings import Settings
from tests.conftest import require_postgres

PROBE = "plat05_probe"


def _engine(db_settings: Settings, database: str) -> Engine:
    return make_engine(db_settings.postgres.dsn("bank" if database == "bank" else "kb"))


@pytest.fixture(scope="module", params=["bank", "kb"])
def probe(request: pytest.FixtureRequest, db_settings: Settings) -> Iterator[str]:
    engine = _engine(db_settings, request.param)
    with engine.begin() as conn:
        conn.execute(text(f"create table if not exists {PROBE} (id int primary key)"))
        conn.execute(text(f"truncate {PROBE}"))
    yield request.param
    with engine.begin() as conn:
        conn.execute(text(f"drop table if exists {PROBE}"))
    engine.dispose()


def _session(request: pytest.FixtureRequest, database: str) -> Session:
    return request.getfixturevalue("bank_db" if database == "bank" else "kb_db")  # type: ignore[no-any-return]


@pytest.mark.integration
@pytest.mark.parametrize("attempt", [1, 2])
def test_same_unique_row_in_two_tests(probe: str, attempt: int, request: pytest.FixtureRequest) -> None:
    """Runs twice per database; the second run only passes if the first was rolled back."""
    session = _session(request, probe)
    session.execute(text(f"insert into {PROBE} values (1)"))
    assert session.execute(text(f"select count(*) from {PROBE}")).scalar_one() == 1


@pytest.mark.integration
def test_commit_inside_a_test_is_still_rolled_back(
    probe: str, request: pytest.FixtureRequest, db_settings: Settings
) -> None:
    session = _session(request, probe)
    session.execute(text(f"insert into {PROBE} values (2)"))
    session.commit()  # releases a SAVEPOINT only; the outer test transaction stays open
    assert session.execute(text(f"select count(*) from {PROBE} where id = 2")).scalar_one() == 1
    other = _engine(db_settings, probe)
    with other.connect() as conn:  # another connection cannot see uncommitted test data
        assert conn.execute(text(f"select count(*) from {PROBE}")).scalar_one() == 0
    other.dispose()


@pytest.mark.integration
def test_databases_are_the_test_databases_at_head(bank_db: Session, kb_db: Session) -> None:
    assert bank_db.execute(text("select current_database()")).scalar_one() == "bank_test"
    assert kb_db.execute(text("select current_database()")).scalar_one() == "kb_test"
    for session in (bank_db, kb_db):
        assert session.execute(text("select version_num from alembic_version")).scalar_one()
    assert kb_db.execute(text("select '[1,2,3]'::vector <-> '[1,2,4]'::vector")).scalar_one() == 1.0


def test_unreachable_postgres_skips_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    engine = make_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none?connect_timeout=1")
    with pytest.raises(pytest.skip.Exception, match="make up"):
        require_postgres(engine)


def test_unreachable_postgres_fails_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    engine = make_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none?connect_timeout=1")
    with pytest.raises(pytest.fail.Exception, match="CI"):
        require_postgres(engine)
