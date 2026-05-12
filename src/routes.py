import html as html_module
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .analyzer import analyze_document
from .database import Document, get_db
from .scraper import collect_today

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
    # Bold markers
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Newlines to <br>
    text = text.replace("\n", "<br>")
    return text


templates.env.filters["format_analysis"] = _format_analysis


@router.get("/", response_class=HTMLResponse)
async def index(
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
        name="index.html",
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

    return templates.TemplateResponse(
        request=request,
        name="document.html",
        context={
            "doc": doc,
            "block_labels": BLOCK_LABELS,
        },
    )


@router.post("/collect")
async def collect(background_tasks: BackgroundTasks):
    background_tasks.add_task(collect_today)
    return RedirectResponse("/?status=collecting", status_code=303)


@router.post("/analyze/{doc_id}")
async def analyze(doc_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(analyze_document, doc_id)
    return RedirectResponse(f"/document/{doc_id}?status=analyzing", status_code=303)
