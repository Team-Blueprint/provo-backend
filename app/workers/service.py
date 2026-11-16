import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import normalize_phone_number
from app.payments.service import PaymentService
from app.workers.models import Worker
from app.workers.schemas import WorkerCreate

logger = logging.getLogger(__name__)


class WorkerService:
    def __init__(self, payment_service: PaymentService | None = None):
        self.payment_service = payment_service or PaymentService()

    async def register_worker(self, db: AsyncSession, data: WorkerCreate) -> Worker:
        clean_phone = normalize_phone_number(data.phone)

        # Check existing worker
        stmt = select(Worker).where(Worker.phone == clean_phone)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        worker = Worker(
            name=data.name.strip(),
            phone=clean_phone,
            trade=data.trade.strip(),
        )
        db.add(worker)
        await db.flush()

        # Provision Paga persistent virtual account
        worker = await self.payment_service.register_worker_paga_account(db, worker)
        logger.info(
            "Registered worker %s with Paga account %s",
            worker.id,
            worker.paga_account_number,
        )
        return worker

    async def get_worker_by_id(self, db: AsyncSession, worker_id: uuid.UUID) -> Worker | None:
        stmt = select(Worker).where(Worker.id == worker_id)
        return (await db.execute(stmt)).scalar_one_or_none()

    async def get_worker_by_phone(self, db: AsyncSession, phone: str) -> Worker | None:
        clean_phone = normalize_phone_number(phone)
        stmt = select(Worker).where(Worker.phone == clean_phone)
        return (await db.execute(stmt)).scalar_one_or_none()
