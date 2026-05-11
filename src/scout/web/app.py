from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from scout.db import run_migrations
from scout.logging import configure_logging, get_logger

log = get_logger("scout.web")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    applied = run_migrations()
    if applied:
        log.info("migrations_applied_on_startup", count=len(applied), names=applied)
    yield


app = FastAPI(title="Scout", lifespan=lifespan)


@app.get("/")
def root() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "scout"})


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})
