import html as html_module
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import state
from .analyzer import analyze_document
from .database import Document, get_db
from .scraper import collect_today
from .tasks import collect_and_analyze

PDF_DIR = Path(__file__).parent.parent / "downloaded_pdfs"

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

BLOCK_LABELS = {
    "president": "Президент",
    "government": "Правительство",
    "federal_authorities": "Федеральные органы",
}


def _format_analysis(text: str) -> str:
    if not text:
        return ""
    text = html_module.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = text.replace("\n", "<br>")
    return text


def _format_date(date_str: str) -> str:
    if not date_str or len(date_str) < 10:
        return date_str or ""
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return date_str[:10]


templates.env.filters["format_analysis"] = _format_analysis
templates.env.filters["format_date"] = _format_date
templates.env.globals["get_last_update"] = lambda: state.last_successful_update
templates.env.globals["get_cycle_time"] = lambda: state.last_cycle_time


async def _get_digest_docs(db: AsyncSession):
    """Docs from last cycle → last 24h → all, in that priority order."""
    if state.last_cycle_doc_ids:
        result = await db.execute(
            select(Document)
            .where(Document.id.in_(state.last_cycle_doc_ids))
            .order_by(desc(Document.created_at))
        )
        docs = result.scalars().all()
        if docs:
            return docs, "last_cycle"

    cutoff = datetime.utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(Document)
        .where(Document.created_at >= cutoff)
        .order_by(desc(Document.created_at))
    )
    docs = result.scalars().all()
    if docs:
        return docs, "last_24h"

    result = await db.execute(
        select(Document).order_by(desc(Document.significance), desc(Document.created_at))
    )
    return result.scalars().all(), "all"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    digest_docs, digest_source = await _get_digest_docs(db)

    digest_total = len(digest_docs)
    digest_analyzed = sum(1 for d in digest_docs if d.analysis)
    docs_with_sig = [d for d in digest_docs if d.significance is not None]
    top_doc = max(docs_with_sig, key=lambda d: d.significance) if docs_with_sig else None
    top5 = sorted(docs_with_sig, key=lambda d: d.significance, reverse=True)[:5]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "block_labels": BLOCK_LABELS,
            "digest_total": digest_total,
            "digest_analyzed": digest_analyzed,
            "digest_source": digest_source,
            "top_doc": top_doc,
            "top5": top5,
        },
    )


@router.get("/all", response_class=HTMLResponse)
async def all_documents(
    request: Request,
    date: str = None,
    block: str = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Document).order_by(desc(Document.created_at))
    if date:
        query = query.where(Document.date.startswith(date))
    if block:
        query = query.where(Document.block == block)
    result = await db.execute(query)
    documents = result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="all.html",
        context={
            "documents": documents,
            "block_labels": BLOCK_LABELS,
            "selected_date": date or "",
            "selected_block": block or "",
            "total": len(documents),
        },
    )


@router.get("/document/{doc_id}", response_class=HTMLResponse)
async def document_detail(
    request: Request, doc_id: int, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if not doc:
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:2rem'>Документ не найден</h1>",
            status_code=404,
        )

    pdf_exists = (PDF_DIR / f"{doc.eo_number}.pdf").exists() if doc.eo_number else False

    return templates.TemplateResponse(
        request=request,
        name="document.html",
        context={
            "doc": doc,
            "block_labels": BLOCK_LABELS,
            "pdf_exists": pdf_exists,
        },
    )


@router.post("/collect")
async def collect(background_tasks: BackgroundTasks):
    background_tasks.add_task(collect_today)
    return RedirectResponse("/?status=collecting", status_code=303)


@router.post("/collect-and-analyze")
async def collect_analyze_all(background_tasks: BackgroundTasks):
    background_tasks.add_task(collect_and_analyze)
    return RedirectResponse("/?status=analyzing", status_code=303)


@router.post("/analyze/{doc_id}")
async def analyze(doc_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(analyze_document, doc_id)
    return RedirectResponse(f"/document/{doc_id}?status=analyzing", status_code=303)
