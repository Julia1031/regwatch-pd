import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from . import notifier, state
from .analyzer import analyze_document
from .database import AsyncSessionLocal, Document
from .scraper import collect_today

logger = logging.getLogger(__name__)


async def collect_and_analyze() -> dict:
    logger.info("=== collect_and_analyze: starting ===")

    cycle_start = datetime.now()
    collect_stats = await collect_today()
    logger.info("collect_and_analyze: collection done — %s", collect_stats)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document.id).where(Document.analysis.is_(None))
        )
        doc_ids = [row[0] for row in result.fetchall()]

    total = len(doc_ids)
    logger.info("collect_and_analyze: %d documents pending analysis", total)

    analyzed = 0
    for i, doc_id in enumerate(doc_ids, 1):
        logger.info("collect_and_analyze: analyzing %d/%d (id=%d)", i, total, doc_id)
        success = await analyze_document(doc_id)
        if success:
            analyzed += 1

    logger.info("collect_and_analyze: done — analyzed %d/%d", analyzed, total)

    now = datetime.now()
    state.last_successful_update = now
    state.last_cycle_time = now

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document.id).where(Document.created_at >= cycle_start)
        )
        state.last_cycle_doc_ids = [row[0] for row in result.fetchall()]

    if state.last_cycle_doc_ids:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id.in_(state.last_cycle_doc_ids))
            )
            analyzed_docs = result.scalars().all()
        await notifier.send_daily_digest(analyzed_docs)

    return {"collected": collect_stats, "analyzed": analyzed, "pending": total}


async def restore_state_from_db() -> None:
    """Restore in-memory state from DB after a restart."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.max(Document.created_at)))
        max_created_at: datetime | None = result.scalar_one_or_none()

    if max_created_at is None:
        return

    state.last_cycle_time = max_created_at
    state.last_successful_update = max_created_at

    cycle_window = max_created_at - timedelta(hours=2)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document.id).where(Document.created_at >= cycle_window)
        )
        state.last_cycle_doc_ids = [row[0] for row in result.fetchall()]

    logger.info(
        "restore_state_from_db: last_cycle_time=%s, %d docs restored",
        state.last_cycle_time,
        len(state.last_cycle_doc_ids),
    )
