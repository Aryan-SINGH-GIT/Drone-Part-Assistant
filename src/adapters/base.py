import abc
import logging
from typing import Optional, List, Dict, Any

try:
    from curl_cffi.requests import AsyncSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import httpx

from src.core.config import settings
from src.extractor.html_cleaner import clean_html_for_llm
from src.extractor.llm_parser import parse_product_with_llm
from src.extractor.schemas import ProductExtractionResult

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BaseScraperAdapter(abc.ABC):
    """
    Abstract base adapter for e-commerce sources.
    Any new retailer can be integrated simply by subclassing this class.
    Uses TLS/JA3 impersonation via curl_cffi to bypass Cloudflare protection reliably.
    """

    def __init__(self, retailer_id: str, name: str, base_url: str):
        self.retailer_id = retailer_id
        self.name = name
        self.base_url = base_url.rstrip("/")

    @abc.abstractmethod
    def matches_url(self, url: str) -> bool:
        """Returns True if this adapter should handle the given URL."""
        pass

    async def fetch_html(self, url: str) -> str:
        """Fetches raw HTML with browser impersonation to handle Cloudflare WAF."""
        if HAS_CURL_CFFI:
            # Try chrome120 first, fallback to safari15_5 if 403
            for impersonate_profile in ["chrome120", "safari15_5"]:
                try:
                    async with AsyncSession(impersonate=impersonate_profile, timeout=settings.REQUEST_TIMEOUT_SECONDS) as s:
                        resp = await s.get(url, headers=DEFAULT_HEADERS)
                        if resp.status_code == 200:
                            return resp.text
                        elif resp.status_code != 403:
                            resp.raise_for_status()
                except Exception as e:
                    logger.debug(f"Fetch attempt with {impersonate_profile} on {url}: {e}")
                    continue

        # Fallback to standard httpx if curl_cffi is unavailable
        import httpx
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=settings.REQUEST_TIMEOUT_SECONDS,
            verify=False,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def extract_product_data(self, url: str, html: Optional[str] = None) -> Tuple_Result:
        """
        Default two-phase extraction:
        1. Fetch & clean HTML into condensed text + JSON-LD
        2. Feed into LLM / Heuristic classifier
        Can be overridden by adapters if a retailer provides an API (e.g. Shopify .json)
        """
        if html is None:
            html = await self.fetch_html(url)

        cleaned_text, json_ld, content_hash = clean_html_for_llm(html)
        result = await parse_product_with_llm(cleaned_text, json_ld=json_ld, url=url)
        return result, content_hash

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Searches the retailer catalog for products matching the query.
        Returns a list of dicts: {"title": ..., "url": ..., "price": ..., "stock_status": ...}
        """
        pass


# Helper type alias for extraction return
Tuple_Result = tuple[ProductExtractionResult, str]
