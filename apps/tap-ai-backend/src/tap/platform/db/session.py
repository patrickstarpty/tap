"""Async SQLAlchemy session construction for request and worker paths."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tap.platform.db.lock_contention import (
    CLAIM_LOCK_WAIT_OPTION,
    DEFAULT_CLAIM_LOCK_WAIT_SECONDS,
    validated_claim_lock_wait_seconds,
)


def create_engine_and_session_factory(
    database_url: str,
    *,
    claim_lock_wait_seconds: int = DEFAULT_CLAIM_LOCK_WAIT_SECONDS,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """The engine carries the job-claim lock-wait bound (`claim_transaction`)
    as an execution option; it changes no statement outside a claim."""

    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        execution_options={
            CLAIM_LOCK_WAIT_OPTION: validated_claim_lock_wait_seconds(claim_lock_wait_seconds)
        },
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
