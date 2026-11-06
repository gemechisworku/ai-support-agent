"""Request dependencies: DB session, service-token auth and the bank's "today"."""

import datetime as dt
import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from supportagent.common import clock
from supportagent.common.db import bank_engine, session_scope
from supportagent.common.settings import get_settings

_token_header = APIKeyHeader(name="X-Service-Token", auto_error=False)


def get_session() -> Iterator[Session]:
    """One transaction per request: committed on success, rolled back on error."""
    with session_scope(bank_engine()) as session:
        yield session


def require_service_token(token: Annotated[str | None, Security(_token_header)]) -> None:
    expected = get_settings().services.gateway_service_token.get_secret_value()
    if token is None or not secrets.compare_digest(token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-Service-Token")


def get_as_of() -> dt.date:
    """`BANK_AS_OF` (tests, demos) or today in UTC."""
    return get_settings().data.bank_as_of or clock.today()


SessionDep = Annotated[Session, Depends(get_session)]
AsOfDep = Annotated[dt.date, Depends(get_as_of)]
