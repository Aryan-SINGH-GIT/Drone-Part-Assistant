import json
import logging
import re
from typing import Dict, Any, List, Optional
from sqlalchemy import select, delete

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models import Product, WatchlistItem, ProductHistory, StockStatus, Retailer
from src.adapters.registry import adapter_registry

logger = logging.getLogger(__name__)


class AssistantAgent:
    """
    Conversational agent capable of tool execution:
    - Search & compare across retailers
    - Watchlist management (add/remove/list)
    - Product history & status tracking
    """

    async def handle_user_message(self, user_id: str, text: str) -> str:
        """
        Main entry point for processing a natural language message from a user.
        Uses LLM intent resolution if available, otherwise deterministic rule-based router.
        """
        text_clean = text.strip()

        # 0. Check greetings
        if text_clean.lower().strip("!.,?") in ["hi", "hello", "hey", "start", "help", "hola"]:
            return (
                "👋 **Hello! I'm your AI Drone Parts Assistant.**\n\n"
                "You can ask me to:\n"
                "• 🔍 Search & compare parts: _'find Pixhawk 6X'_ or _'prices of SF propeller'_\n"
                "• ➕ Track a product: _'track https://robu.in/product/...'_ or _'/add <URL>'_\n"
                "• 📋 View your list: _'show my watchlist'_\n"
                "• 📈 Check price history: _'history Pixhawk'_"
            )

        # 1. Check for direct URL submission (common shortcut)
        urls = re.findall(r"https?://[^\s]+", text_clean)
        if urls and any(w in text_clean.lower() for w in ["track", "watch", "add", "monitor", "alert"]):
            return await self.tool_add_to_watchlist(urls[0], user_id)
        if len(urls) == 1 and len(text_clean.split()) <= 2:
            return await self.tool_add_to_watchlist(urls[0], user_id)

        # 2. Check for watchlist list command
        if any(w in text_clean.lower() for w in ["watchlist", "my list", "tracked", "tracking"]):
            return await self.tool_get_watchlist(user_id)

        # 3. Check for remove command
        remove_match = re.search(r"(?:remove|delete|untrack|stop tracking)\s+(?:id\s+)?(\d+|https?://\S+|.+)", text_clean, re.IGNORECASE)
        if remove_match:
            target = remove_match.group(1).strip()
            return await self.tool_remove_from_watchlist(target, user_id)

        # 4. Check for price / stock history query
        history_match = re.search(r"(?:history|timeline|price trend|trend for)\s+(.+)", text_clean, re.IGNORECASE)
        if history_match:
            query = history_match.group(1).strip()
            return await self.tool_get_product_history(query)

        # 5. Check for comparison query
        if any(w in text_clean.lower() for w in ["compare", "vs", "versus"]):
            return await self.tool_compare_products(text_clean)

        # 6. Try LLM Tool Calling if configured
        if settings.LLM_PROVIDER in ["gemini", "openai", "groq"] and (
            settings.GEMINI_API_KEY or settings.OPENAI_API_KEY or settings.GROQ_API_KEY
        ):
            try:
                llm_response = await self._route_with_llm(user_id, text_clean)
                if llm_response:
                    return llm_response
            except Exception as e:
                logger.warning(f"LLM conversational routing fallback: {e}")

        # 7. Default to searching products across retailers
        return await self.tool_search_and_compare(text_clean)

    async def _route_with_llm(self, user_id: str, text: str) -> Optional[str]:
        """Uses lightweight LLM to classify intent and arguments."""
        prompt = f"""You are an AI Drone Shopping Assistant. The user said: "{text}".
Categorize the user's intent into ONE of the following JSON actions:
1. {{"action": "watchlist"}} -> User wants to view their tracked items.
2. {{"action": "add", "url": "URL if present", "title": "keywords if no URL", "target_price": number or null}}
3. {{"action": "remove", "target": "id or title"}}
4. {{"action": "history", "query": "product title or keyword"}}
5. {{"action": "search", "query": "product keywords", "max_price": number or null, "in_stock_only": boolean}}
6. {{"action": "chat", "reply": "direct polite reply"}}

Return ONLY valid JSON.
"""
        # Call LLM
        from src.extractor import llm_parser
        if settings.LLM_PROVIDER == "gemini" and settings.GEMINI_API_KEY and llm_parser._GEMINI_KEY_VALID is not False:
            try:
                from google import genai
                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                resp = client.models.generate_content(
                    model="gemini-1.5-flash",
                    contents=prompt,
                    config={"response_mime_type": "application/json"}
                )
                data = json.loads(resp.text)
                llm_parser._GEMINI_KEY_VALID = True
            except Exception as e:
                if "API_KEY_INVALID" in str(e) or "400" in str(e):
                    llm_parser._GEMINI_KEY_VALID = False
                return None
        elif (settings.LLM_PROVIDER in ["openai", "groq"]):
            from openai import AsyncOpenAI
            if settings.LLM_PROVIDER == "groq":
                client = AsyncOpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
                model_name = "llama-3.1-8b-instant"
            else:
                client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                model_name = "gpt-4o-mini"
            res = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            data = json.loads(res.choices[0].message.content)
        else:
            return None

        action = data.get("action")
        if action == "watchlist":
            return await self.tool_get_watchlist(user_id)
        elif action == "add":
            url = data.get("url")
            if url:
                return await self.tool_add_to_watchlist(url, user_id, data.get("target_price"))
            else:
                return await self.tool_search_and_compare(data.get("title", text))
        elif action == "remove":
            return await self.tool_remove_from_watchlist(data.get("target", ""), user_id)
        elif action == "history":
            return await self.tool_get_product_history(data.get("query", ""))
        elif action == "search":
            return await self.tool_search_and_compare(
                data.get("query", text),
                max_price=data.get("max_price"),
                in_stock_only=data.get("in_stock_only", False)
            )
        elif action == "chat":
            return data.get("reply", "How can I assist you with drone parts today?")

        return None

    # --- TOOLS ---

    async def tool_get_watchlist(self, user_id: str) -> str:
        """Lists all products the user is currently monitoring."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Product, WatchlistItem)
                .join(WatchlistItem, Product.id == WatchlistItem.product_id)
                .where(WatchlistItem.user_id == user_id, WatchlistItem.is_active == True)
            )
            res = await session.execute(stmt)
            rows = res.all()

            if not rows:
                return (
                    "📋 **Your Watchlist is empty.**\n\n"
                    "To track a drone part, send me a product link or say:\n"
                    "`/add https://robu.in/product/...`"
                )

            lines = ["📋 **Your Monitored Drone Parts:**\n"]
            for idx, (p, w) in enumerate(rows, 1):
                status_emoji = "🟢" if p.last_status == "IN_STOCK" else ("🟡" if p.last_status == "PRE_ORDER" else "🔴")
                price_str = f"₹{p.last_price:,.2f}" if p.last_price else "Price N/A"
                lines.append(
                    f"{idx}. {status_emoji} **{p.title}**\n"
                    f"   • Status: `{p.last_status}`\n"
                    f"   • Price: {price_str}\n"
                    f"   • ID: `{p.id}` | [Store Link]({p.url})"
                )

            lines.append("\n_To remove an item, type:_ `/remove <ID>`")
            return "\n".join(lines)

    async def tool_add_to_watchlist(self, url: str, user_id: str, target_price: Optional[float] = None) -> str:
        """Adds a product URL to the user's watchlist, scraping it immediately."""
        clean_url = url.strip("<>")
        adapter = adapter_registry.get_adapter_for_url(clean_url)

        async with AsyncSessionLocal() as session:
            # Check if product exists in DB
            stmt = select(Product).where(Product.url == clean_url)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()

            if not product:
                # Scrape on-the-fly
                try:
                    extraction, content_hash = await adapter.extract_product_data(clean_url)
                except Exception as e:
                    return f"❌ Could not access this URL ({e}). Please check the link."

                product = Product(
                    retailer_id=adapter.retailer_id,
                    title=extraction.title or clean_url,
                    url=clean_url,
                    last_price=extraction.price,
                    last_status=extraction.stock_status,
                    pre_order_eta=extraction.pre_order_eta,
                    content_hash=content_hash,
                )
                session.add(product)
                await session.flush()

            # Ensure retailer exists
            ret = await session.get(Retailer, adapter.retailer_id)
            if not ret:
                session.add(Retailer(id=adapter.retailer_id, name=adapter.name, base_url=adapter.base_url))

            # Add to user watchlist
            watch_stmt = select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.product_id == product.id
            )
            w_res = await session.execute(watch_stmt)
            existing_watch = w_res.scalar_one_or_none()

            if existing_watch:
                existing_watch.is_active = True
                if target_price:
                    existing_watch.target_price = target_price
            else:
                watch_item = WatchlistItem(
                    user_id=user_id,
                    product_id=product.id,
                    target_price=target_price,
                    is_active=True,
                )
                session.add(watch_item)

            await session.commit()

            status_emoji = "🟢" if product.last_status == "IN_STOCK" else ("🟡" if product.last_status == "PRE_ORDER" else "🔴")
            price_str = f"₹{product.last_price:,.2f}" if product.last_price else "N/A"
            return (
                f"✅ **Added to Watchlist!**\n\n"
                f"📦 **{product.title}**\n"
                f"🏪 Retailer: {adapter.name}\n"
                f"📊 Current Status: {status_emoji} `{product.last_status}`\n"
                f"💰 Price: {price_str}\n\n"
                f"I will alert you as soon as stock or pre-order status changes!"
            )

    async def tool_remove_from_watchlist(self, target: str, user_id: str) -> str:
        """Removes an item from the user's watchlist by ID, URL, or Title."""
        async with AsyncSessionLocal() as session:
            # Try ID first
            product_id = None
            if target.isdigit():
                product_id = int(target)
            else:
                # Find matching product by URL or title
                stmt = select(Product).where(
                    (Product.url == target) | (Product.title.ilike(f"%{target}%"))
                )
                res = await session.execute(stmt)
                p = res.scalars().first()
                if p:
                    product_id = p.id

            if not product_id:
                return f"🔍 Could not find product '{target}' in your watchlist."

            stmt = delete(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.product_id == product_id
            )
            res = await session.execute(stmt)
            await session.commit()

            if res.rowcount > 0:
                return f"🗑️ Product ID `{product_id}` has been removed from your watchlist."
            else:
                return f"ℹ️ Product ID `{product_id}` was not in your active watchlist."

    async def tool_search_and_compare(
        self, query: str, max_price: Optional[float] = None, in_stock_only: bool = False
    ) -> str:
        """Searches across all Indian drone retailer sites and presents comparisons."""
        # Clean conversational query prefixes
        query_clean = re.sub(
            r"^(?:search|find|show me|look for|compare|buy|prices? of|best price for|what(?:'s| is) the (?:best )?price for|prices? for|d me)\s*",
            "",
            query,
            flags=re.I
        ).strip().rstrip("?.,")
        # Also strip trailing words like "currently", "in stock", "please"
        query_clean = re.sub(r"\s+(?:in stock|currently|available|please)$", "", query_clean, flags=re.I).strip()
        if not query_clean:
            return "Please specify a product to search, e.g. `search Pixhawk 6X` or `Raspberry Pi 5`."

        results = await adapter_registry.search_all(query_clean, limit_per_store=3)

        # Also check local DB for cached matches
        async with AsyncSessionLocal() as session:
            db_stmt = select(Product).where(Product.title.ilike(f"%{query_clean}%"))
            db_res = await session.execute(db_stmt)
            db_products = db_res.scalars().all()
            for db_p in db_products:
                if not any(r["url"] == db_p.url for r in results):
                    results.append({
                        "retailer": db_p.retailer_id.upper(),
                        "retailer_id": db_p.retailer_id,
                        "title": db_p.title,
                        "url": db_p.url,
                        "price": db_p.last_price,
                        "currency": db_p.currency,
                        "stock_status": db_p.last_status,
                    })

        if not results:
            return f"🔍 No products found for '{query_clean}' across Robu, ThinkRobotics, Zbotic, and Evelta."

        # Filter
        if max_price:
            results = [r for r in results if r.get("price") and r["price"] <= max_price]
        if in_stock_only:
            results = [r for r in results if r.get("stock_status") == "IN_STOCK"]

        # Sort by price if available
        results.sort(key=lambda x: x.get("price") or 999999)

        lines = [f"🔍 **Search & Comparison: '{query_clean}'**\n"]
        for idx, item in enumerate(results[:8], 1):
            status = item.get("stock_status", "UNKNOWN")
            emoji = "🟢" if status == "IN_STOCK" else ("🟡" if status == "PRE_ORDER" else "🔴")
            price_str = f"₹{item['price']:,.2f}" if item.get("price") else "Price N/A"

            lines.append(
                f"{idx}. **{item['title']}**\n"
                f"   • Store: **{item['retailer']}**\n"
                f"   • Price: {price_str} | Status: {emoji} `{status}`\n"
                f"   • [View Product]({item['url']})"
            )

        lines.append("\n💡 _Tip: Send `/add <URL>` to track any of these for stock alerts!_")
        return "\n".join(lines)

    async def tool_compare_products(self, query: str) -> str:
        """Compares specs/prices across stores for a given part category or name."""
        return await self.tool_search_and_compare(query)

    async def tool_get_product_history(self, query: str) -> str:
        """Fetches price and availability history for a product."""
        async with AsyncSessionLocal() as session:
            # Find product
            stmt = select(Product).where(
                (Product.title.ilike(f"%{query}%")) | (Product.url.ilike(f"%{query}%"))
            )
            res = await session.execute(stmt)
            p = res.scalars().first()

            if not p:
                return f"🔍 No tracked product matching '{query}' found."

            hist_stmt = (
                select(ProductHistory)
                .where(ProductHistory.product_id == p.id)
                .order_by(ProductHistory.recorded_at.desc())
                .limit(5)
            )
            h_res = await session.execute(hist_stmt)
            records = h_res.scalars().all()

            lines = [
                f"📈 **Status & Price History: {p.title}**",
                f"Store: `{p.retailer_id.upper()}` | Current: `{p.last_status}`\n",
            ]
            if not records:
                lines.append("No historical transitions recorded yet.")
            else:
                for r in records:
                    date_str = r.recorded_at.strftime("%Y-%m-%d %H:%M UTC") if r.recorded_at else "N/A"
                    price_str = f"₹{r.price:,.2f}" if r.price else "N/A"
                    lines.append(f"• `{date_str}`: {r.status} @ {price_str}")

            return "\n".join(lines)


# Singleton
assistant_agent = AssistantAgent()
