import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from .database import init_db
from .routes import router
from .tasks import collect_and_analyze, restore_state_from_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialised")
    await restore_state_from_db()

    scheduler.add_job(collect_and_analyze, "cron", hour=9, minute=0, id="daily_collect")
    scheduler.start()
    logger.info("Scheduler started — daily collect+analyze at 09:00 MSK")

    yield

    scheduler.shutdown(wait=False)
    logger.info("RegWatch stopped")


app = FastAPI(
    title="RegWatch",
    description="Мониторинг законодательных изменений РФ",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)
