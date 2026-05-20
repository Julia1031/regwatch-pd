import asyncio
import logging
from pathlib import Path

import fitz  # PyMuPDF
import httpx
from sqlalchemy import select

from .config import settings
from .database import AsyncSessionLocal, Document

logger = logging.getLogger(__name__)

PDF_DIR = Path(__file__).parent.parent / "downloaded_pdfs"
PDF_DIR.mkdir(exist_ok=True)


async def fetch_documents_for_block(block: str, period_date: str = None) -> list[dict]:
    params = {
        "PeriodType": "daily",
        "PageSize": 100,
        "Block": block,
    }
    if period_date:
        params["Date"] = period_date

    url = settings.PRAVO_API_URL
    logger.info("Requesting block=%s url=%s params=%s", block, url, params)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        try:
            resp = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            logger.error(
                "Timeout fetching block=%s url=%s: %s: %s",
                block, url, type(exc).__name__, exc,
            )
            raise
        except httpx.RequestError as exc:
            logger.error(
                "Request error fetching block=%s url=%s: %s: %s",
                block, url, type(exc).__name__, exc,
            )
            raise

        logger.info("block=%s status=%s", block, resp.status_code)

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP error fetching block=%s url=%s status=%s body=%r: %s",
                block, url, exc.response.status_code,
                exc.response.text[:500], exc,
            )
            raise

        data = resp.json()

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("Items", "items", "Documents", "documents", "Result", "result"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


async def download_pdf(eo_number: str) -> bytes | None:
    pdf_path = PDF_DIR / f"{eo_number}.pdf"

    if pdf_path.exists():
        return pdf_path.read_bytes()

    params = {"eoNumber": eo_number}
    async with httpx.AsyncClient(
        timeout=settings.PDF_DOWNLOAD_TIMEOUT, follow_redirects=True
    ) as client:
        try:
            resp = await client.get(settings.PRAVO_PDF_URL, params=params)
            if resp.status_code == 200 and len(resp.content) > 100:
                pdf_path.write_bytes(resp.content)
                logger.info("PDF saved: %s", pdf_path.name)
                return resp.content
        except Exception as e:
            logger.error("PDF download error for %s: %s", eo_number, e)
    return None


def _ocr_page(page: fitz.Page) -> str:
    try:
        import pytesseract
        from PIL import Image

        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(img, lang="rus+eng")
    except ImportError:
        logger.warning("pytesseract not available, skipping OCR")
        return ""
    except Exception as e:
        logger.error("OCR error: %s", e)
        return ""


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts = []
        for page in doc:
            text = page.get_text().strip()
            parts.append(text)
        full_text = "\n".join(parts).strip()

        if len(full_text) < 100:
            logger.info("Text too short, trying OCR")
            ocr_parts = []
            for page in doc:
                ocr_parts.append(_ocr_page(page))
            full_text = "\n".join(ocr_parts).strip()

        doc.close()
        return full_text
    except Exception as e:
        logger.error("Text extraction error: %s", e)
        return ""


def _normalize(item: dict, block: str) -> dict:
    eo = (
        item.get("EoNumber")
        or item.get("eoNumber")
        or item.get("Id")
        or item.get("id")
        or ""
    )
    name = (
        item.get("Name")
        or item.get("name")
        or item.get("Title")
        or item.get("title")
        or ""
    )
    complex_name = (
        item.get("ComplexName")
        or item.get("complexName")
        or item.get("FullName")
        or item.get("fullName")
        or ""
    )
    date_val = (
        item.get("Date")
        or item.get("date")
        or item.get("SignDate")
        or item.get("signDate")
        or ""
    )
    return {
        "eo_number": str(eo),
        "name": name,
        "complex_name": complex_name,
        "date": date_val,
        "block": block,
    }


async def collect_today(target_date: str = None) -> dict:
    from datetime import date

    today = target_date or date.today().isoformat()
    stats = {"added": 0, "skipped": 0, "errors": 0}

    for block in settings.BLOCKS:
        logger.info("Fetching block: %s for %s", block, today)
        try:
            items = await fetch_documents_for_block(block, today)
            logger.info("Block %s: %d documents found", block, len(items))
        except Exception as e:
            logger.exception(
                "Failed to fetch block %s (%s: %s)",
                block, type(e).__name__, e,
            )
            stats["errors"] += 1
            continue

        for item in items:
            doc_data = _normalize(item, block)
            if not doc_data["eo_number"]:
                stats["errors"] += 1
                continue

            try:
                async with AsyncSessionLocal() as session:
                    existing = await session.execute(
                        select(Document).where(
                            Document.eo_number == doc_data["eo_number"]
                        )
                    )
                    if existing.scalar_one_or_none():
                        stats["skipped"] += 1
                        continue

                    pdf_bytes = await download_pdf(doc_data["eo_number"])
                    full_text = extract_text_from_pdf(pdf_bytes) if pdf_bytes else ""

                    doc = Document(
                        eo_number=doc_data["eo_number"],
                        name=doc_data["name"],
                        complex_name=doc_data["complex_name"],
                        date=doc_data["date"],
                        block=block,
                        full_text=full_text,
                    )
                    session.add(doc)
                    await session.commit()
                    stats["added"] += 1
                    logger.info("Saved document: %s", doc_data["eo_number"])

            except Exception as e:
                logger.error(
                    "Error saving document %s: %s", doc_data["eo_number"], e
                )
                stats["errors"] += 1

            await asyncio.sleep(settings.REQUEST_DELAY)

    logger.info("Collection complete: %s", stats)
    return stats


async def backfill_pdfs() -> dict:
    """Download missing PDFs for all documents already in DB."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Document.eo_number))
        eo_numbers = [row[0] for row in result.fetchall() if row[0]]

    stats = {"downloaded": 0, "already_exists": 0, "failed": 0}
    total = len(eo_numbers)
    logger.info("backfill_pdfs: %d documents to check", total)

    for i, eo_number in enumerate(eo_numbers, 1):
        pdf_path = PDF_DIR / f"{eo_number}.pdf"
        if pdf_path.exists():
            stats["already_exists"] += 1
            logger.info("backfill_pdfs: [%d/%d] already exists — %s", i, total, eo_number)
            continue

        logger.info("backfill_pdfs: [%d/%d] downloading %s", i, total, eo_number)
        pdf_bytes = await download_pdf(eo_number)
        if pdf_bytes:
            stats["downloaded"] += 1
        else:
            stats["failed"] += 1

        await asyncio.sleep(settings.REQUEST_DELAY)

    logger.info("backfill_pdfs complete: %s", stats)
    return stats
