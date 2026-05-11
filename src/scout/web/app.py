from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from scout.db import run_migrations
from scout.logging import configure_logging, get_logger
from scout.web.routes import applications, dashboard, opportunities, runs, settings

log = get_logger("scout.web")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    applied = run_migrations()
    if applied:
        log.info("migrations_applied_on_startup", count=len(applied), names=applied)
    yield


app = FastAPI(title="Scout", lifespan=lifespan)
app.include_router(dashboard.router)
app.include_router(opportunities.router)
app.include_router(applications.router)
app.include_router(runs.router)
app.include_router(settings.router)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})
