import logging
from typing import List, Dict, Any
from urllib.parse import urlparse

from src.adapters.base import BaseScraperAdapter

logger = logging.getLogger(__name__)


class GenericAdapter(BaseScraperAdapter):
    """
    Fallback adapter for any arbitrary e-commerce URL.
    Relies purely on universal HTML cleaning, JSON-LD, and LLM extraction.
    """

    def __init__(self, url: str = ""):
        domain = urlparse(url).netloc if url else "generic"
        super().__init__(
            retailer_id=domain.replace(".", "_"),
            name=domain,
            base_url=f"https://{domain}" if domain != "generic" else ""
        )

    def matches_url(self, url: str) -> bool:
        return True

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        # Generic sites do not have a known search endpoint schema
        return []
