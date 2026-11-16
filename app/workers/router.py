import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.workers.schemas import WorkerCreate, WorkerResponse
from app.workers.service import WorkerService

router = APIRouter(prefix="/workers", tags=["Workers"])
worker_service = WorkerService()


@router.post("/", response_model=WorkerResponse, status_code=status.HTTP_201_CREATED)
async def register_worker(
    payload: WorkerCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        worker = await worker_service.register_worker(db, payload)
        return worker
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{worker_id}", response_model=WorkerResponse)
async def get_worker(
    worker_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    worker = await worker_service.get_worker_by_id(db, worker_id)
    if not worker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    return worker
