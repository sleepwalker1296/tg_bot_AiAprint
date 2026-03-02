from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы в базе данных если их нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Миграция: добавляем tshirt_color если колонки ещё нет (SQLite не поддерживает IF NOT EXISTS)
        try:
            await conn.execute(text("ALTER TABLE orders ADD COLUMN tshirt_color VARCHAR(50)"))
        except Exception as exc:
            if "duplicate column" not in str(exc).lower() and "already exists" not in str(exc).lower():
                raise
