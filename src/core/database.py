import json
import logging
from pathlib import Path
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, event

from src.core.config import settings
from src.core.models import Base, Retailer, Product, StockStatus

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL, echo=False)

# Enable WAL mode for SQLite to eliminate concurrency lock issues
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    except Exception as e:
        logger.debug(f"Could not set SQLite WAL pragma: {e}")

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Initializes tables and populates default retailers and seed products if missing."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # 1. Seed Retailers
        default_retailers = [
            Retailer(id="robu", name="Robu.in", base_url="https://robu.in"),
            Retailer(id="thinkrobotics", name="ThinkRobotics", base_url="https://thinkrobotics.com"),
            Retailer(id="zbotic", name="Zbotic", base_url="https://zbotic.in"),
            Retailer(id="evelta", name="Evelta", base_url="https://evelta.com"),
        ]

        for r in default_retailers:
            existing = await session.get(Retailer, r.id)
            if not existing:
                session.add(r)
        await session.commit()

        # 2. Seed Products from seed_data.json
        seed_file = Path("seed_data.json")
        if seed_file.exists():
            try:
                with open(seed_file, "r", encoding="utf-8") as f:
                    seeds = json.load(f)
                    for item in seeds:
                        stmt = select(Product).where(Product.url == item["url"])
                        res = await session.execute(stmt)
                        if not res.scalar_one_or_none():
                            p = Product(
                                retailer_id=item.get("retailer_id", "robu"),
                                title=item["title"],
                                url=item["url"],
                                category=item.get("category", "flight_controller"),
                                last_price=item.get("target_price"),
                                last_status=StockStatus.UNKNOWN.value,
                            )
                            session.add(p)
                    await session.commit()
                    logger.info("Database initialized and seeded successfully.")
            except Exception as e:
                logger.error(f"Error seeding database: {e}")
