import asyncio
from datetime import datetime, timezone, timedelta
import logging
from typing import Optional, List, Dict, Any, Callable
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models import Product, ProductHistory, WatchlistItem, StockStatus
from src.adapters.registry import adapter_registry
from src.extractor.schemas import ProductExtractionResult

logger = logging.getLogger(__name__)


class MonitoringEngine:
    def __init__(self, alert_callback: Optional[Callable[[str, str], Any]] = None):
        """
        alert_callback: Optional async function(chat_id: str, message: str) to send alerts
        """
        self.alert_callback = alert_callback
        self.scheduler = AsyncIOScheduler()

    def set_alert_callback(self, cb: Callable[[str, str], Any]):
        self.alert_callback = cb

    async def check_single_product(
        self, product: Product, session, force_alert: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Scrapes, parses, and evaluates state change for a single product.
        Records history and dispatches alert if state change is favorable.
        """
        adapter = adapter_registry.get_adapter_for_url(product.url)
        logger.info(f"Checking [{product.id}] {product.title} via {adapter.name}...")

        try:
            extraction_result, content_hash = await adapter.extract_product_data(product.url)
        except Exception as e:
            logger.error(f"Failed to scrape {product.url}: {e}")
            return None

        # Compare old state vs new state
        old_status = product.last_status
        old_price = product.last_price
        new_status = extraction_result.stock_status
        new_price = extraction_result.price if extraction_result.price is not None else old_price
        now = datetime.now(timezone.utc)

        # Update product fields
        if extraction_result.title and (not product.title or product.title.startswith("http")):
            product.title = extraction_result.title

        product.last_status = new_status
        product.last_price = new_price
        product.pre_order_eta = extraction_result.pre_order_eta
        product.content_hash = content_hash
        product.last_checked_at = now

        # Record history entry
        history = ProductHistory(
            product_id=product.id,
            price=new_price,
            status=new_status,
            pre_order_eta=extraction_result.pre_order_eta,
            content_hash=content_hash,
            recorded_at=now,
        )
        session.add(history)

        # Determine if favorable transition occurred
        is_favorable_change = False
        change_reason = ""
        is_true_restock = False

        # Case 1: Out of Stock -> In Stock (CRITICAL RESTOCK EVENT)
        if old_status in [StockStatus.OUT_OF_STOCK.value, StockStatus.UNKNOWN.value] and new_status == StockStatus.IN_STOCK.value:
            is_favorable_change = True
            is_true_restock = True
            change_reason = "🎉 **BACK IN STOCK!**"

        # Case 2: Out of Stock -> Pre-Order
        elif old_status == StockStatus.OUT_OF_STOCK.value and new_status == StockStatus.PRE_ORDER.value:
            is_favorable_change = True
            eta_info = f" (ETA: {extraction_result.pre_order_eta})" if extraction_result.pre_order_eta else ""
            change_reason = f"📦 **PRE-ORDER OPENED!**{eta_info}"

        # Case 3: Price drop (> 2%) on available items
        elif (
            old_price
            and new_price
            and old_price > 0
            and new_price < old_price * 0.98
            and new_status in [StockStatus.IN_STOCK.value, StockStatus.PRE_ORDER.value]
        ):
            is_favorable_change = True
            drop_amount = old_price - new_price
            change_reason = f"📉 **PRICE DROP!** Decreased by ₹{drop_amount:,.2f} (from ₹{old_price:,.2f} to ₹{new_price:,.2f})"

        # Check if any user target price was reached
        sub_stmt = select(WatchlistItem).where(
            WatchlistItem.product_id == product.id,
            WatchlistItem.is_active == True
        )
        sub_res = await session.execute(sub_stmt)
        for sub in sub_res.scalars().all():
            if sub.target_price and new_price and new_price <= sub.target_price and (not old_price or old_price > sub.target_price):
                is_favorable_change = True
                change_reason = f"🎯 **TARGET PRICE REACHED!** ₹{new_price:,.2f} (Target was ₹{sub.target_price:,.2f})"
                break

        # Force alert for simulation / testing
        if force_alert:
            is_favorable_change = True
            change_reason = "🧪 **[SIMULATED RESTOCK ALERT]**"

        # Check cooldown: True restocks ALWAYS bypass cooldown!
        in_cooldown = False
        if product.last_alerted_at and not is_true_restock and not force_alert:
            last_alerted = product.last_alerted_at
            if last_alerted.tzinfo is None:
                last_alerted = last_alerted.replace(tzinfo=timezone.utc)
            cooldown_limit = last_alerted + timedelta(hours=settings.ALERT_COOLDOWN_HOURS)
            if now < cooldown_limit:
                in_cooldown = True

        alert_info = None
        if is_favorable_change and not in_cooldown:
            product.last_alerted_at = now
            alert_info = {
                "product_id": product.id,
                "title": product.title,
                "url": product.url,
                "old_status": old_status,
                "new_status": new_status,
                "old_price": old_price,
                "new_price": new_price,
                "reason": change_reason,
            }
            # Dispatch to subscribers
            await self._dispatch_alert(product, alert_info, session)

        await session.commit()
        return alert_info

    async def _dispatch_alert(self, product: Product, alert_info: Dict[str, Any], session):
        """Sends alert message to all users watching this product."""
        stmt = select(WatchlistItem).where(
            WatchlistItem.product_id == product.id,
            WatchlistItem.is_active == True
        )
        res = await session.execute(stmt)
        subscribers = res.scalars().all()

        price_str = f"₹{product.last_price:,.2f}" if product.last_price is not None else "Price N/A"
        message = (
            f"{alert_info['reason']}\n\n"
            f"**{product.title}**\n"
            f"💰 Price: {price_str}\n"
            f"📦 Status: `{product.last_status}`\n"
            f"🔗 [Buy / View on {product.retailer_id.upper()}]({product.url})\n"
        )

        user_ids = {sub.user_id for sub in subscribers}
        if settings.TELEGRAM_DEFAULT_CHAT_ID:
            user_ids.add(settings.TELEGRAM_DEFAULT_CHAT_ID)

        if not user_ids:
            logger.info(f"Alert generated for '{product.title}', but no subscribers or default chat configured.")
            return

        for uid in user_ids:
            if self.alert_callback:
                try:
                    await self.alert_callback(uid, message)
                    logger.info(f"Alert dispatched to user {uid} for product {product.id}")
                except Exception as e:
                    logger.error(f"Failed to dispatch alert to {uid}: {e}")

    async def discover_new_products(self, session):
        """
        Scans key category queries across all stores to detect newly listed drone parts.
        Directly satisfies: 'Detect new matching products appearing in category/search pages'
        """
        discovery_queries = ["Pixhawk", "Raspberry Pi 5", "Flight Controller"]
        logger.info("Scanning stores for new product discoveries...")

        for query in discovery_queries:
            try:
                results = await adapter_registry.search_all(query, limit_per_store=2)
                for item in results:
                    url = item.get("url")
                    if not url:
                        continue

                    stmt = select(Product).where(Product.url == url)
                    res = await session.execute(stmt)
                    if not res.scalar_one_or_none():
                        new_p = Product(
                            retailer_id=item.get("retailer_id", "generic"),
                            title=item.get("title", "New Drone Product"),
                            url=url,
                            last_price=item.get("price"),
                            last_status=item.get("stock_status", "IN_STOCK"),
                            created_at=datetime.now(timezone.utc),
                        )
                        session.add(new_p)
                        await session.flush()
                        logger.info(f"🆕 Discovered new matching product: {new_p.title} on {new_p.retailer_id}")

                        # Announce discovery to default channel
                        if settings.TELEGRAM_DEFAULT_CHAT_ID and self.alert_callback:
                            price_str = f"₹{new_p.last_price:,.2f}" if new_p.last_price else "Price N/A"
                            disc_msg = (
                                f"🆕 **NEW DRONE PART DISCOVERED!**\n\n"
                                f"**{new_p.title}**\n"
                                f"🏪 Store: **{new_p.retailer_id.upper()}**\n"
                                f"💰 Price: {price_str} | Status: `{new_p.last_status}`\n"
                                f"🔗 [View Product]({new_p.url})\n\n"
                                f"_Automatically cataloged for stock tracking._"
                            )
                            try:
                                await self.alert_callback(settings.TELEGRAM_DEFAULT_CHAT_ID, disc_msg)
                            except Exception as e:
                                logger.debug(f"Discovery broadcast error: {e}")
            except Exception as e:
                logger.debug(f"Catalog discovery error for {query}: {e}")

    async def run_monitoring_cycle(self, force_all: bool = False):
        """Executes one pass across all products in the database + checks for new catalog discoveries."""
        logger.info("=== Starting Monitoring Cycle ===")
        async with AsyncSessionLocal() as session:
            stmt = select(Product)
            res = await session.execute(stmt)
            products = res.scalars().all()

            for p in products:
                try:
                    await self.check_single_product(p, session)
                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.error(f"Error checking product {p.id}: {e}")

            # Run new product discovery on search pages
            await self.discover_new_products(session)
            await session.commit()

        logger.info("=== Monitoring Cycle Completed ===")

    def start_scheduler(self):
        """Starts background periodic execution."""
        if not self.scheduler.running:
            self.scheduler.add_job(
                self.run_monitoring_cycle,
                "interval",
                hours=settings.MONITORING_INTERVAL_HOURS,
                id="daily_drone_parts_monitor",
                replace_existing=True,
            )
            self.scheduler.start()
            logger.info(f"Scheduler started. Running every {settings.MONITORING_INTERVAL_HOURS} hours.")


# Global engine instance
monitoring_engine = MonitoringEngine()
