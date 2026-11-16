from app.core.config import settings
from app.integrations.paga.base import (
    PagaAdapter,
    PagaEscrowRequestResult,
    PagaPayoutResult,
)
from app.integrations.paga.mock import MockPagaAdapter
from app.integrations.paga.real import RealPagaAdapter


def get_paga_adapter() -> PagaAdapter:
    """Returns real or mock Paga adapter depending on environment configuration."""
    if settings.PAGA_USE_MOCK:
        return MockPagaAdapter()
    return RealPagaAdapter()


__all__ = [
    "MockPagaAdapter",
    "PagaAdapter",
    "PagaEscrowRequestResult",
    "PagaPayoutResult",
    "RealPagaAdapter",
    "get_paga_adapter",
]
