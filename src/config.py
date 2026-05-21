import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL: str = "sqlite+aiosqlite:///./regwatch.db"
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:14b"
    PRAVO_API_URL: str = "http://publication.pravo.gov.ru/api/Documents"
    PRAVO_PDF_URL: str = "http://publication.pravo.gov.ru/file/pdf"
    BLOCKS: list = ["president", "government", "federal_authorities"]
    TEXT_LIMIT: int = 5000
    ANALYZER_TIMEOUT: int = 300
    PDF_DOWNLOAD_TIMEOUT: int = 60
    REQUEST_DELAY: float = 0.5

    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")


settings = Settings()
