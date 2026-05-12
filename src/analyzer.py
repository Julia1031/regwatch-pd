import logging
import re
from datetime import datetime

import httpx
from sqlalchemy import select

from .config import settings
from .database import AsyncSessionLocal, Document

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """/think

Ты опытный юрист-аналитик. Проанализируй следующий нормативно-правовой акт Российской Федерации и дай структурированный юридический комментарий.

Название документа: {name}

Текст документа (первые {limit} символов):
{text}

Ответ оформи строго по следующей структуре:

**Отрасль права:** [укажи одну основную отрасль: конституционное / административное / гражданское / трудовое / налоговое / уголовное / процессуальное / земельное / экологическое / финансовое / международное / иное]

**1. Резюме изменений**
[2-3 предложения о сути и цели документа]

**2. Затрагиваемые нормы**
[перечисли изменяемые или дополняемые законы, статьи и нормы]

**3. Кого касается**
[субъекты права: граждане, организации, государственные органы, отдельные категории лиц]

**4. Что делать**
[конкретные практические шаги для тех, кого касается документ]

**5. Оценка значимости: X/5**
[X — цифра от 1 до 5; 1 = техническое изменение, 3 = умеренная значимость, 5 = ключевое системное изменение. Обоснуй оценку одним предложением.]
"""


def strip_thinking(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Handle unclosed <think> block (model still reasoning)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def extract_significance(text: str) -> int | None:
    patterns = [
        r"Оценка значимости[:\s*]*(\d)[/\s]5",
        r"\*\*5\.\s*Оценка значимости[^*]*\*\*[:\s]*(\d)",
        r"(\d)[/]5\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            return max(1, min(5, val))
    return None


def extract_law_branch(text: str) -> str | None:
    m = re.search(r"\*\*Отрасль права[:\*\s]+([^\n\*<]+)", text, re.IGNORECASE)
    if m:
        branch = m.group(1).strip().rstrip("*").strip()
        return branch[:100] if branch else None
    return None


async def analyze_document(doc_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.id == doc_id)
        )
        doc = result.scalar_one_or_none()

        if not doc:
            logger.error("Document %d not found", doc_id)
            return False

        text = (doc.full_text or "")[: settings.TEXT_LIMIT]
        prompt = PROMPT_TEMPLATE.format(
            name=doc.name or doc.complex_name or "Без названия",
            text=text or "[Текст не извлечён]",
            limit=settings.TEXT_LIMIT,
        )

        try:
            async with httpx.AsyncClient(timeout=settings.ANALYZER_TIMEOUT) as client:
                logger.info("Sending doc %d to Ollama (%s)", doc_id, settings.OLLAMA_MODEL)
                resp = await client.post(
                    f"{settings.OLLAMA_URL}/api/generate",
                    json={
                        "model": settings.OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.6,
                            "num_ctx": 8192,
                        },
                    },
                )
                resp.raise_for_status()
                raw = resp.json().get("response", "")

            clean = strip_thinking(raw)
            doc.analysis = clean
            doc.law_branch = extract_law_branch(clean)
            doc.significance = extract_significance(clean)
            doc.analyzed_at = datetime.utcnow()
            await session.commit()
            logger.info(
                "Doc %d analyzed: branch=%s, significance=%s",
                doc_id,
                doc.law_branch,
                doc.significance,
            )
            return True

        except httpx.ConnectError:
            logger.error("Ollama not reachable at %s", settings.OLLAMA_URL)
            return False
        except Exception as e:
            logger.error("Analysis error for doc %d: %s", doc_id, e)
            return False
