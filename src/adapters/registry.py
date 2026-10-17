import asyncio
import logging
from typing import List, Dict, Any, Optional

from src.adapters.base import BaseScraperAdapter
from src.adapters.robu import RobuAdapter
from src.adapters.thinkrobotics import ThinkRoboticsAdapter
from src.adapters.zbotic import ZboticAdapter
from src.adapters.evelta import EveltaAdapter
from src.adapters.generic import GenericAdapter

logger = logging.getLogger(__name__)


class AdapterRegistry:
    def __init__(self):
        self._adapters: List[BaseScraperAdapter] = [
            RobuAdapter(),
            ThinkRoboticsAdapter(),
            ZboticAdapter(),
            EveltaAdapter(),
        ]

    def register(self, adapter: BaseScraperAdapter):
        """Allows dynamic registration of new retailer adapters at runtime."""
        self._adapters.insert(0, adapter)

    def get_adapter_for_url(self, url: str) -> BaseScraperAdapter:
        for adapter in self._adapters:
            if adapter.matches_url(url):
                return adapter
        return GenericAdapter(url)

    def get_adapter_by_id(self, retailer_id: str) -> Optional[BaseScraperAdapter]:
        for adapter in self._adapters:
            if adapter.retailer_id == retailer_id:
                return adapter
        return None

    def list_all(self) -> List[BaseScraperAdapter]:
        return list(self._adapters)

    async def search_all(self, query: str, limit_per_store: int = 3) -> List[Dict[str, Any]]:
        """
        Concurrently queries all registered retailer search endpoints.
        Aggregates and formats cross-store search results.
        """
        tasks = [adapter.search(query, limit=limit_per_store) for adapter in self._adapters]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)

        aggregated = []
        for res in results_lists:
            if isinstance(res, list):
                aggregated.extend(res)
            elif isinstance(res, Exception):
                logger.warning(f"Store search failed: {res}")

        return aggregated


# Global singleton registry
adapter_registry = AdapterRegistry()
