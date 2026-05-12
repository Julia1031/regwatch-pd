"""
RegWatch PD — Стартовый скрипт
===============================
Мониторинг федеральных законов и подзаконных актов
с publication.pravo.gov.ru

Запуск:
    pip install httpx pymupdf
    python regwatch_starter.py

Что делает:
    1. Подключается к API publication.pravo.gov.ru
    2. Получает список блоков и видов документов (справочники)
    3. Забирает свежие ФЗ, указы Президента, постановления Правительства,
       приказы ФОИВ
    4. Скачивает PDF и извлекает текст
    5. Выводит результат в консоль

Это каркас — потом к нему подключается LLM-анализ.
"""

import httpx
import fitz  # PyMuPDF
import json
import asyncio
from datetime import date, timedelta
from pathlib import Path

# ═══════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════

API_BASE = "http://publication.pravo.gov.ru/api"

# Блоки, которые нас интересуют:
# president          — ФЗ, ФКЗ, указы, распоряжения Президента
# government         — постановления и распоряжения Правительства
# federal_authorities — приказы ФОИВ (Минцифры, Роскомнадзор и т.д.)
TARGET_BLOCKS = ["president", "government", "federal_authorities"]

# Папка для скачанных PDF
PDF_DIR = Path("downloaded_pdfs")
PDF_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════
# ЧАСТЬ 1: СПРАВОЧНИКИ (запускаем один раз)
# ═══════════════════════════════════════════════════

async def discover_blocks(client: httpx.AsyncClient) -> list[dict]:
    """Получить список всех блоков публикации"""
    resp = await client.get(f"{API_BASE}/PublicBlocks")
    resp.raise_for_status()
    return resp.json()


async def discover_document_types(client: httpx.AsyncClient, block: str) -> list[dict]:
    """Получить виды документов для блока"""
    resp = await client.get(f"{API_BASE}/DocumentTypes", params={"block": block})
    resp.raise_for_status()
    return resp.json()


async def discover_authorities(client: httpx.AsyncClient, block: str) -> list[dict]:
    """Получить принявшие органы для блока"""
    resp = await client.get(f"{API_BASE}/SignatoryAuthorities", params={"block": block})
    resp.raise_for_status()
    return resp.json()


async def print_reference_data():
    """
    ЗАПУСТИ ЭТО ПЕРВЫМ — покажет структуру данных на портале.
    Из вывода ты узнаешь GUID видов документов, которые нужны.
    """
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        print("=" * 70)
        print("СПРАВОЧНИКИ publication.pravo.gov.ru")
        print("=" * 70)

        # Блоки
        blocks = await discover_blocks(client)
        print("\n📦 БЛОКИ ПУБЛИКАЦИИ:")
        for b in blocks:
            print(f"  {b['code']:25s}  {b['shortName']}")

        # Виды документов для каждого целевого блока
        for block_code in TARGET_BLOCKS:
            print(f"\n📄 ВИДЫ ДОКУМЕНТОВ [{block_code}]:")
            doc_types = await discover_document_types(client, block_code)
            for dt in doc_types:
                print(f"  {dt['name']:40s}  id={dt['id']}")

            print(f"\n🏛  ПРИНЯВШИЕ ОРГАНЫ [{block_code}]:")
            authorities = await discover_authorities(client, block_code)
            for a in authorities[:10]:  # первые 10
                print(f"  {a['name'][:60]:60s}  id={a['id']}")
            if len(authorities) > 10:
                print(f"  ... и ещё {len(authorities) - 10}")


# ═══════════════════════════════════════════════════
# ЧАСТЬ 2: ПОЛУЧЕНИЕ ДОКУМЕНТОВ
# ═══════════════════════════════════════════════════

async def fetch_documents(
    client: httpx.AsyncClient,
    block: str = None,
    document_type_id: str = None,
    date_from: str = None,
    date_to: str = None,
    period_type: str = None,
    page_size: int = 100,
) -> list[dict]:
    """
    Универсальный запрос документов.

    Примеры:
        # Все документы за сегодня
        fetch_documents(client, period_type="daily")

        # ФЗ за последнюю неделю
        fetch_documents(client, block="president",
                        document_type_id="<GUID ФЗ>",
                        period_type="weekly")

        # Постановления Правительства за период
        fetch_documents(client, block="government",
                        date_from="2026-05-01", date_to="2026-05-12")
    """
    all_docs = []
    page = 1

    while True:
        params = {"PageSize": page_size, "Index": page}
        if block:
            params["Block"] = block
        if document_type_id:
            params["DocumentTypeId"] = document_type_id
        if period_type:
            params["PeriodType"] = period_type
        if date_from:
            params["PublishDateFrom"] = date_from
        if date_to:
            params["PublishDateTo"] = date_to

        resp = await client.get(f"{API_BASE}/Documents", params=params)
        resp.raise_for_status()
        data = resp.json()

        all_docs.extend(data.get("items", []))

        total_pages = data.get("pagesTotalCount", 1)
        if page >= total_pages:
            break
        page += 1

    return all_docs


async def fetch_document_detail(client: httpx.AsyncClient, eo_number: str) -> dict:
    """Получить расширенные данные по одному документу"""
    resp = await client.get(f"{API_BASE}/Document", params={"eoNumber": eo_number})
    resp.raise_for_status()
    return resp.json()


# ═══════════════════════════════════════════════════
# ЧАСТЬ 3: СКАЧИВАНИЕ PDF И ИЗВЛЕЧЕНИЕ ТЕКСТА
# ═══════════════════════════════════════════════════

async def download_pdf(client: httpx.AsyncClient, eo_number: str) -> Path:
    """Скачивает PDF документа, возвращает путь к файлу"""
    pdf_path = PDF_DIR / f"{eo_number}.pdf"

    if pdf_path.exists():
        return pdf_path  # уже скачан

    url = f"http://publication.pravo.gov.ru/file/pdf?eoNumber={eo_number}"
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()

    pdf_path.write_bytes(resp.content)
    return pdf_path


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Извлекает текст из PDF. Если PDF — скан, текст будет пустым."""
    doc = fitz.open(str(pdf_path))
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts).strip()


# ═══════════════════════════════════════════════════
# ЧАСТЬ 4: ГЛАВНЫЙ КОНВЕЙЕР
# ═══════════════════════════════════════════════════

async def run_daily_collection():
    """
    Главная функция — собирает свежие законы и подзаконные акты.
    Запускай её по расписанию (каждые 4 часа).
    """
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:

        print("\n" + "=" * 70)
        print(f"🔍 RegWatch PD — сбор данных за сегодня ({date.today()})")
        print("=" * 70)

        # --- Собираем документы по каждому блоку ---
        all_documents = []

        for block in TARGET_BLOCKS:
            docs = await fetch_documents(
                client,
                block=block,
                period_type="daily",
            )
            all_documents.extend(docs)
            print(f"\n📦 Блок [{block}]: найдено {len(docs)} документов")

        if not all_documents:
            print("\n😴 Сегодня новых документов нет")
            return

        print(f"\n📊 ИТОГО найдено: {len(all_documents)} документов")
        print("-" * 70)

        # --- Обрабатываем каждый документ ---
        for i, doc in enumerate(all_documents, 1):
            eo = doc["eoNumber"]
            name = doc.get("name", "Без названия")
            complex_name = doc.get("complexName", "").replace("\n", " ")
            doc_date = doc.get("viewDate", "?")
            pages = doc.get("pagesCount", "?")

            print(f"\n[{i}/{len(all_documents)}] 📋 {complex_name[:100]}")
            print(f"    Дата опубликования: {doc_date}")
            print(f"    Номер опубликования: {eo}")
            print(f"    Страниц PDF: {pages}")

            # Скачиваем PDF
            try:
                pdf_path = await download_pdf(client, eo)
                print(f"    ✅ PDF скачан: {pdf_path}")

                # Извлекаем текст
                text = extract_text_from_pdf(pdf_path)
                if len(text) < 50:
                    print(f"    ⚠️  Мало текста ({len(text)} символов) — возможно, скан")
                else:
                    print(f"    ✅ Текст извлечён: {len(text)} символов")
                    # Показываем начало текста
                    preview = text[:300].replace("\n", " ")
                    print(f"    📝 Начало: {preview}...")

            except Exception as e:
                print(f"    ❌ Ошибка при скачивании PDF: {e}")

        print("\n" + "=" * 70)
        print(f"✅ Обработка завершена: {len(all_documents)} документов")
        print("=" * 70)


# ═══════════════════════════════════════════════════
# ЧАСТЬ 5: РАСШИРЕННЫЙ СБОР ЗА ПЕРИОД
# ═══════════════════════════════════════════════════

async def run_period_collection(days_back: int = 7):
    """
    Собирает документы за последние N дней.
    Удобно для первоначального наполнения базы.
    """
    date_to = date.today().isoformat()
    date_from = (date.today() - timedelta(days=days_back)).isoformat()

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        print(f"\n🗓  Сбор за период: {date_from} — {date_to}")

        all_documents = []
        for block in TARGET_BLOCKS:
            docs = await fetch_documents(
                client,
                block=block,
                date_from=date_from,
                date_to=date_to,
            )
            all_documents.extend(docs)
            print(f"  📦 [{block}]: {len(docs)} документов")

        print(f"\n📊 Всего за {days_back} дней: {len(all_documents)} документов")

        # Группируем по виду для наглядности
        by_type = {}
        for doc in all_documents:
            cn = doc.get("complexName", "")
            # Вид документа — первое слово в complexName
            kind = cn.split(" ")[0] if cn else "Неизвестно"
            by_type[kind] = by_type.get(kind, 0) + 1

        print("\n📈 Распределение по видам:")
        for kind, count in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"  {kind:40s}  {count}")

        return all_documents


# ═══════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════

async def main():
    """
    Раскомментируй нужную функцию:
    """

    # --- Шаг 1: Изучи справочники (запусти один раз) ---
    await print_reference_data()

    # --- Шаг 2: Собери документы за сегодня ---
    await run_daily_collection()

    # --- Шаг 3: Собери документы за последнюю неделю ---
    # await run_period_collection(days_back=7)


if __name__ == "__main__":
    asyncio.run(main())
