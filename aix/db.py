from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from os import environ
from typing import Annotated

from fastapi import Depends
from sqlalchemy import URL, create_engine, make_url
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def database_url() -> URL:
    url = make_url(environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must point to PostgreSQL")
    return url.set(drivername="postgresql+psycopg")


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(
        database_url(), pool_pre_ping=True, hide_parameters=True, isolation_level="READ COMMITTED",
    )


def database() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session


DB = Annotated[Session, Depends(database)]
