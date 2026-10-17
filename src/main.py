import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output on Windows consoles
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from sqlalchemy import select

from src.core.config import settings
from src.core.database import init_db, AsyncSessionLocal
from src.core.models import Product, StockStatus
from src.scheduler.engine import monitoring_engine
from src.adapters.registry import adapter_registry
from src.bot.telegram_bot import telegram_service

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("drone_assistant")
console = Console()


async def run_cli_check_now():
    """Forces an immediate monitoring run on all tracked products and outputs a Rich table."""
    await init_db()
    console.print(Panel.fit("[bold cyan]DronePartAssistant: Running Immediate Monitoring Check[/bold cyan]"))

    async with AsyncSessionLocal() as session:
        stmt = select(Product)
        res = await session.execute(stmt)
        products = res.scalars().all()

        if not products:
            console.print("[yellow]No products found in database. Run with --seed first.[/yellow]")
            return

        table = Table(title="Live Product Availability Status")
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Retailer", style="magenta")
        table.add_column("Title", style="white")
        table.add_column("Status", justify="center")
        table.add_column("Price (INR)", justify="right", style="green")
        table.add_column("Pre-Order ETA", style="dim")

        for p in products:
            console.print(f"[dim]Checking [{p.id}] {p.title[:45]}...[/dim]")
            await monitoring_engine.check_single_product(p, session)

            status_style = {
                "IN_STOCK": "[bold green]IN_STOCK[/bold green]",
                "OUT_OF_STOCK": "[bold red]OUT_OF_STOCK[/bold red]",
                "PRE_ORDER": "[bold yellow]PRE_ORDER[/bold yellow]",
                "UNKNOWN": "[grey50]UNKNOWN[/grey50]",
            }.get(p.last_status, p.last_status)

            price_val = f"{p.last_price:,.2f}" if p.last_price else "N/A"
            table.add_row(
                str(p.id),
                p.retailer_id.upper(),
                p.title[:50],
                status_style,
                price_val,
                p.pre_order_eta or "-",
            )

        console.print(table)


async def run_cli_search(query: str):
    """Searches across all retailers from the command line."""
    console.print(f"[cyan]Searching all retailers for:[/cyan] [bold]{query}[/bold]...")
    results = await adapter_registry.search_all(query, limit_per_store=3)

    table = Table(title=f"Search Results for '{query}'")
    table.add_column("Retailer", style="magenta")
    table.add_column("Title", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Price (INR)", justify="right", style="green")
    table.add_column("URL", style="dim")

    for r in results:
        status = r.get("stock_status", "UNKNOWN")
        status_style = (
            "[green]IN_STOCK[/green]" if status == "IN_STOCK"
            else ("[yellow]PRE_ORDER[/yellow]" if status == "PRE_ORDER" else "[red]OUT_OF_STOCK[/red]")
        )
        price_val = f"{r.get('price', 0):,.2f}" if r.get("price") else "N/A"
        table.add_row(r["retailer"], r["title"][:45], status_style, price_val, r["url"])

    console.print(table)


async def run_simulated_alert():
    """Simulates a product transitioning from OUT_OF_STOCK to IN_STOCK."""
    await init_db()
    console.print(Panel.fit("[bold green]Simulating OUT_OF_STOCK -> IN_STOCK Transition Alert[/bold green]"))

    async with AsyncSessionLocal() as session:
        stmt = select(Product)
        res = await session.execute(stmt)
        p = res.scalars().first()

        if not p:
            console.print("[red]No product found to simulate.[/red]")
            return

        # Temporarily mock as OUT_OF_STOCK
        p.last_status = StockStatus.OUT_OF_STOCK.value
        await session.commit()

        console.print(f"[yellow]Simulating stock drop for: {p.title}[/yellow]")
        alert_info = await monitoring_engine.check_single_product(p, session, force_alert=True)
        console.print(f"[bold green]Alert Triggered Successfully![/bold green] Details:\n{alert_info}")


async def run_daemon():
    """Runs the full system: Database + Scheduler + Telegram Bot."""
    console.print(Panel.fit("[bold green]Starting DronePartAssistant Full Daemon[/bold green]"))
    await init_db()

    # Wire alert callback from scheduler to telegram
    monitoring_engine.set_alert_callback(telegram_service.send_alert)
    monitoring_engine.start_scheduler()

    if not settings.TELEGRAM_BOT_TOKEN:
        console.print("[bold yellow]Warning: TELEGRAM_BOT_TOKEN is not configured in .env.[/bold yellow]")
        console.print("[white]The background monitoring scheduler is active. To enable the Telegram bot, add TELEGRAM_BOT_TOKEN to .env.[/white]")
        # Keep alive for scheduler
        while True:
            await asyncio.sleep(3600)
    else:
        app = telegram_service.create_application()
        console.print("[bold green]Telegram Bot polling started. Press Ctrl+C to stop.[/bold green]")
        
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        try:
            while True:
                await asyncio.sleep(1)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            console.print("\n[yellow]Shutting down bot and scheduler cleanly...[/yellow]")
            try:
                if app.updater and app.updater.running:
                    await app.updater.stop()
                if app.running:
                    await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.debug(f"Error during bot shutdown: {e}")

            try:
                if monitoring_engine.scheduler.running:
                    monitoring_engine.scheduler.shutdown(wait=False)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="AI-Based Shopping Assistant for Drone Parts")
    parser.add_argument("--check-now", action="store_true", help="Run immediate monitoring pass and display status table")
    parser.add_argument("--search", type=str, help="Search all stores for query")
    parser.add_argument("--seed", action="store_true", help="Initialize and seed database")
    parser.add_argument("--test-alert", action="store_true", help="Simulate a stock transition alert")
    args = parser.parse_args()

    try:
        if args.check_now:
            asyncio.run(run_cli_check_now())
        elif args.search:
            asyncio.run(run_cli_search(args.search))
        elif args.seed:
            asyncio.run(init_db())
            console.print("[green]Database seeded successfully.[/green]")
        elif args.test_alert:
            asyncio.run(run_simulated_alert())
        else:
            asyncio.run(run_daemon())
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
    finally:
        console.print("[dim]DronePartAssistant stopped.[/dim]")


if __name__ == "__main__":
    main()
