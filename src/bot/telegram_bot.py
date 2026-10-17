import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from src.core.config import settings
from src.bot.assistant_agent import assistant_agent
from src.scheduler.engine import monitoring_engine

logger = logging.getLogger(__name__)


async def _safe_reply(update: Update, text: str, disable_preview: bool = True):
    """Sends a reply using Markdown, falling back to plain text if Telegram fails entity parsing."""
    try:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=disable_preview)
    except Exception as e:
        logger.warning(f"Markdown reply failed ({e}), falling back to plain text.")
        await update.message.reply_text(text, disable_web_page_preview=disable_preview)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user and explains capabilities."""
    user = update.effective_user
    welcome_text = (
        f"🚁 **Hello {user.first_name}! Welcome to DronePartAssistant.**\n\n"
        "I monitor Indian drone electronics retailers (`Robu.in`, `ThinkRobotics`, `Zbotic`, `Evelta`) "
        "and alert you the second flight controllers, companion computers, and HATs return in stock or open for pre-order!\n\n"
        "🛠 **What you can do:**\n"
        "• 📋 `/watchlist` - View your tracked drone parts\n"
        "• ➕ `/add <URL>` - Track any product URL\n"
        "• 🗑️ `/remove <ID>` - Remove an item from watchlist\n"
        "• 🔍 `/search <query>` - Search & compare prices across all stores\n"
        "• ⚡ `/check_now` - Trigger a live monitoring cycle immediately\n\n"
        "💬 **You can also just chat with me normally!** E.g.:\n"
        "_- 'Find me Pixhawk 6X in stock'_\n"
        "_- 'Track this https://robu.in/product/...'_\n"
        "_- 'Compare Raspberry Pi 5 prices'_"
    )
    await _safe_reply(update, welcome_text, disable_preview=True)


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    response = await assistant_agent.tool_get_watchlist(user_id)
    await _safe_reply(update, response, disable_preview=True)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    if not context.args:
        await _safe_reply(update, "Please provide a product URL. Usage: `/add <URL>`")
        return

    url = context.args[0]
    await _safe_reply(update, "🔎 Fetching and analyzing product...")
    response = await assistant_agent.tool_add_to_watchlist(url, user_id)
    await _safe_reply(update, response, disable_preview=False)


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    if not context.args:
        await _safe_reply(update, "Usage: `/remove <product_id or title>`")
        return

    target = " ".join(context.args)
    response = await assistant_agent.tool_remove_from_watchlist(target, user_id)
    await _safe_reply(update, response)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await _safe_reply(update, "Usage: `/search <keywords>` (e.g., `/search Raspberry Pi 5`)")
        return

    query = " ".join(context.args)
    await _safe_reply(update, f"🔍 Searching Indian retailers for *{query}*...")
    response = await assistant_agent.tool_search_and_compare(query)
    await _safe_reply(update, response, disable_preview=True)


async def check_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forced simulation / instant run of the monitoring engine."""
    await _safe_reply(update, "⚡ Starting immediate monitoring sweep across all tracked products...")
    try:
        await monitoring_engine.run_monitoring_cycle()
        await _safe_reply(update, "✅ Monitoring cycle finished! Checked all products for stock/pre-order changes.")
    except Exception as e:
        await _safe_reply(update, f"⚠️ Monitoring cycle encountered an error: {e}")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles natural language chat with the AI assistant."""
    if not update.message or not update.message.text:
        return

    user_id = str(update.effective_chat.id)
    user_text = update.message.text

    # Show typing action
    await update.message.chat.send_action("typing")

    reply = await assistant_agent.handle_user_message(user_id, user_text)
    await _safe_reply(update, reply, disable_preview=True)


class TelegramBotService:
    def __init__(self):
        self.app: Optional[Application] = None

    async def send_alert(self, chat_id: str, message: str):
        """Dispatches an alert directly to a Telegram user or group."""
        if not self.app or not self.app.bot:
            logger.warning("Telegram Bot is not initialized. Alert not sent.")
            return

        reply_markup = None
        import re
        urls = re.findall(r'https?://[^\s\)]+', message)
        if urls:
            clean_btn_url = urls[0].rstrip(">.,")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Open Product Page", url=clean_btn_url)]
            ])

        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"Markdown alert failed ({e}), retrying plain text.")
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    disable_web_page_preview=False,
                    reply_markup=reply_markup,
                )
            except Exception as e2:
                logger.error(f"Failed to send alert to Telegram chat {chat_id}: {e2}")

    def create_application(self) -> Application:
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set in environment or .env file.")

        builder = Application.builder().token(token)
        app = builder.build()

        # Command handlers
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", start_command))
        app.add_handler(CommandHandler("watchlist", watchlist_command))
        app.add_handler(CommandHandler("add", add_command))
        app.add_handler(CommandHandler("remove", remove_command))
        app.add_handler(CommandHandler("search", search_command))
        app.add_handler(CommandHandler("check_now", check_now_command))

        # Catch-all natural language message handler
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

        self.app = app
        return app


telegram_service = TelegramBotService()
