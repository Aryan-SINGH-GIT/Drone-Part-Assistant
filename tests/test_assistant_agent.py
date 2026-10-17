import pytest
from src.bot.assistant_agent import assistant_agent
from src.core.database import init_db


@pytest.mark.asyncio
async def test_assistant_agent_watchlist_flow():
    await init_db()
    user_id = "test_user_42"

    # 1. Check initially empty watchlist
    msg = await assistant_agent.handle_user_message(user_id, "Show my watchlist")
    assert "Your Watchlist is empty" in msg

    # 2. Add product conversationally by URL
    add_msg = await assistant_agent.handle_user_message(
        user_id,
        "Please track this item: https://thinkrobotics.com/products/pixhawk-kit-online"
    )
    assert "Added to Watchlist" in add_msg or "Pixhawk" in add_msg

    # 3. View watchlist
    list_msg = await assistant_agent.handle_user_message(user_id, "my watchlist")
    assert "Pixhawk" in list_msg

    # 4. Remove product
    rem_msg = await assistant_agent.handle_user_message(user_id, "remove Pixhawk")
    assert "removed" in rem_msg.lower() or "not in" in rem_msg.lower()
