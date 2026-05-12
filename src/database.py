from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    eo_number = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    complex_name = Column(String, nullable=True)
    date = Column(String, nullable=True)
    block = Column(String, nullable=True)
    full_text = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)
    law_branch = Column(String, nullable=True)
    significance = Column(Integer, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
