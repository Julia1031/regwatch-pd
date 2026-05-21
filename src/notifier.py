import logging
from datetime import datetime

import httpx

from .config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(text: str) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.info("Telegram disabled (token or chat_id not set)")
        return

    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Telegram message sent (status %d)", resp.status_code)
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)


async def send_daily_digest(documents: list) -> None:
    today = datetime.now().strftime("%d.%m.%Y")
    total = len(documents)

    lines = [f"📋 *RegWatch — сводка за {today}*", f"Всего новых документов: {total}"]

    docs_with_sig = [d for d in documents if d.significance is not None]
    top3 = sorted(docs_with_sig, key=lambda d: d.significance, reverse=True)[:3]

    if top3:
        lines.append("\n*Топ-3 значимых документа:*")
        for doc in top3:
            sig = int(doc.significance) if doc.significance else 0
            title = (doc.title or "Без названия")[:80]
            industry = doc.industry or "—"
            lines.append(f"⭐ [{sig}/5] {title}")
            lines.append(f"    отрасль: {industry}")

    lines.append(f"\n🔗 http://localhost:8000")

    await send_telegram("\n".join(lines))
