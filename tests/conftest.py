import asyncio

# Force test database and mock Paga adapter during automated test runs
import os
from collections.abc import AsyncGenerator

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_proofwork.db"

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

settings.PAGA_USE_MOCK = True
settings.DATABASE_URL = "sqlite+aiosqlite:///./test_proofwork.db"

# Import all models so metadata is completely populated
import app.models  # noqa: F401
from app.core.database import Base

# Test async sqlite database
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    future=True,
)

TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
