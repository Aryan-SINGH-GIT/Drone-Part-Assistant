import pytest
from datetime import datetime, timezone, timedelta
from src.core.models import Product, StockStatus, WatchlistItem
from src.scheduler.engine import MonitoringEngine
from src.extractor.schemas import ProductExtractionResult


@pytest.mark.asyncio
async def test_favorable_change_detection(monkeypatch):
    dispatched_alerts = []

    async def mock_alert_cb(chat_id, msg):
        dispatched_alerts.append((chat_id, msg))

    engine = MonitoringEngine(alert_callback=mock_alert_cb)

    # Mock product transitioning OUT_OF_STOCK -> IN_STOCK
    product = Product(
        id=1,
        retailer_id="robu",
        title="Raspberry Pi 5 8GB",
        url="https://robu.in/product/raspberry-pi-5-8gb/",
        last_price=7500.0,
        last_status=StockStatus.OUT_OF_STOCK.value,
        last_checked_at=datetime.now(timezone.utc),
    )

    mock_extraction = ProductExtractionResult(
        title="Raspberry Pi 5 8GB",
        stock_status="IN_STOCK",
        price=7500.0,
        currency="INR",
        confidence=1.0,
    )

    class MockAdapter:
        name = "Robu"
        retailer_id = "robu"
        async def extract_product_data(self, url):
            return mock_extraction, "hash123"

    monkeypatch.setattr("src.adapters.registry.adapter_registry.get_adapter_for_url", lambda url: MockAdapter())

    class MockSession:
        def add(self, obj): pass
        async def commit(self): pass
        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self): return []
                    return MockScalars()
            return MockResult()

    alert_info = await engine.check_single_product(product, MockSession())
    assert alert_info is not None
    assert alert_info["old_status"] == "OUT_OF_STOCK"
    assert alert_info["new_status"] == "IN_STOCK"
    assert "BACK IN STOCK" in alert_info["reason"]


@pytest.mark.asyncio
async def test_none_price_alert_safety(monkeypatch):
    """Verifies that products without a price do not crash the alert message formatter."""
    dispatched = []

    async def mock_alert(uid, msg):
        dispatched.append(msg)

    engine = MonitoringEngine(alert_callback=mock_alert)
    product = Product(
        id=2,
        retailer_id="evelta",
        title="Evelta Flight Controller Case",
        url="https://evelta.com/case/",
        last_price=None,  # Price is None!
        last_status=StockStatus.OUT_OF_STOCK.value,
    )

    mock_extraction = ProductExtractionResult(
        title="Evelta Flight Controller Case",
        stock_status="IN_STOCK",
        price=None,
        currency="INR",
    )

    class MockAdapter:
        name = "Evelta"
        retailer_id = "evelta"
        async def extract_product_data(self, url):
            return mock_extraction, "hash_none"

    monkeypatch.setattr("src.adapters.registry.adapter_registry.get_adapter_for_url", lambda url: MockAdapter())

    class MockSession:
        def add(self, obj): pass
        async def commit(self): pass
        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self):
                            return [WatchlistItem(user_id="user_123", product_id=2)]
                    return MockScalars()
            return MockResult()

    alert_info = await engine.check_single_product(product, MockSession())
    assert alert_info is not None
    assert len(dispatched) >= 1
    assert "Price N/A" in dispatched[0]


@pytest.mark.asyncio
async def test_true_restock_bypasses_cooldown(monkeypatch):
    """Verifies that an out_of_stock -> in_stock transition is delivered even during cooldown."""
    engine = MonitoringEngine()
    now = datetime.now(timezone.utc)

    product = Product(
        id=3,
        retailer_id="thinkrobotics",
        title="Pixhawk 6X",
        url="https://thinkrobotics.com/products/pixhawk",
        last_price=25000.0,
        last_status=StockStatus.OUT_OF_STOCK.value,
        last_alerted_at=now - timedelta(minutes=30),  # Alerted 30 mins ago (in cooldown window)
    )

    mock_extraction = ProductExtractionResult(
        title="Pixhawk 6X",
        stock_status="IN_STOCK",
        price=25000.0,
        currency="INR",
    )

    class MockAdapter:
        name = "ThinkRobotics"
        retailer_id = "thinkrobotics"
        async def extract_product_data(self, url):
            return mock_extraction, "hash_pix"

    monkeypatch.setattr("src.adapters.registry.adapter_registry.get_adapter_for_url", lambda url: MockAdapter())

    class MockSession:
        def add(self, obj): pass
        async def commit(self): pass
        async def execute(self, stmt):
            class MockResult:
                def scalars(self):
                    class MockScalars:
                        def all(self): return []
                    return MockScalars()
            return MockResult()

    alert_info = await engine.check_single_product(product, MockSession())
    # Should NOT be suppressed by cooldown because it's a true restock!
    assert alert_info is not None
    assert alert_info["new_status"] == "IN_STOCK"
