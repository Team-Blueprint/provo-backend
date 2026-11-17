from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.jobs import router as jobs_router
from app.api.reports import router as reports_router
from app.api.sandbox import router as sandbox_router
from app.api.webhooks import router as webhooks_router
from app.api.workers import router as workers_router
from app.core.config import settings
from app.core.database import Base, engine
from app.web.views import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is ready on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Proofwork holds client payments in escrow, releases day by day upon confirmation, and creates trusted records for lenders.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Clean Domain API Routers
app.include_router(auth_router)
app.include_router(jobs_router)
app.include_router(workers_router)
app.include_router(reports_router)
app.include_router(admin_router)
app.include_router(webhooks_router)
app.include_router(sandbox_router)
app.include_router(web_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "escrow_window_hours": settings.AUTO_RELEASE_WINDOW_HOURS,
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
