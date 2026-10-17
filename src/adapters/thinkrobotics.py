import json
import logging
import re
from typing import List, Dict, Any, Optional
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

try:
    from curl_cffi.requests import AsyncSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    import httpx

from src.adapters.base import BaseScraperAdapter, Tuple_Result, DEFAULT_HEADERS
from src.extractor.schemas import ProductExtractionResult
from src.extractor.html_cleaner import compute_content_hash

logger = logging.getLogger(__name__)


class ThinkRoboticsAdapter(BaseScraperAdapter):
    def __init__(self):
        super().__init__(
            retailer_id="thinkrobotics",
            name="ThinkRobotics",
            base_url="https://thinkrobotics.com"
        )

    def matches_url(self, url: str) -> bool:
        return "thinkrobotics.com" in url.lower()

    async def _fetch_json(self, url: str) -> Optional[dict]:
        """Fetches JSON with Cloudflare TLS impersonation when available."""
        if HAS_CURL_CFFI:
            try:
                async with AsyncSession(impersonate="chrome120", timeout=10) as s:
                    resp = await s.get(url, headers=DEFAULT_HEADERS)
                    if resp.status_code == 200:
                        return resp.json()
            except Exception as e:
                logger.debug(f"curl_cffi JSON fetch error on {url}: {e}")

        # Fallback to httpx
        try:
            import httpx
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10, verify=False) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug(f"httpx JSON fetch error on {url}: {e}")

        return None

    async def extract_product_data(self, url: str, html: str | None = None) -> Tuple_Result:
        """
        ThinkRobotics is built on Shopify.
        Attempts to fetch the clean JSON endpoint (.json) first.
        """
        clean_url = url.split("?")[0].rstrip("/")
        if "/products/" in clean_url and not clean_url.endswith(".json"):
            json_url = f"{clean_url}.json"
            data_dict = await self._fetch_json(json_url)
            if data_dict and "product" in data_dict:
                data = data_dict["product"]
                title = data.get("title")
                variants = data.get("variants", [])
                is_available = any(v.get("available", False) for v in variants)
                stock_status = "IN_STOCK" if is_available else "OUT_OF_STOCK"

                prices = [float(v.get("price", 0)) for v in variants if v.get("price")]
                price = min(prices) if prices else None

                result = ProductExtractionResult(
                    title=title,
                    stock_status=stock_status,
                    price=price,
                    currency="INR",
                    confidence=0.98,
                    specs_summary=title,
                )
                content_hash = compute_content_hash(json.dumps(data, sort_keys=True))
                return result, content_hash

        # Fallback to standard base extractor
        return await super().extract_product_data(url, html)

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        results = []
        # Try Shopify search suggest API
        suggest_url = f"{self.base_url}/search/suggest.json?q={quote_plus(query)}&resources[type]=product"
        data = await self._fetch_json(suggest_url)
        if data:
            products = data.get("resources", {}).get("results", {}).get("products", [])
            for p in products[:limit]:
                url = p.get("url")
                if url:
                    url = url.split("?")[0]
                    if not url.startswith("http"):
                        url = f"{self.base_url}{url}"
                results.append({
                    "retailer": self.name,
                    "retailer_id": self.retailer_id,
                    "title": p.get("title"),
                    "url": url,
                    "price": float(p.get("price", 0)) if p.get("price") else None,
                    "currency": "INR",
                    "stock_status": "IN_STOCK" if p.get("available") else "OUT_OF_STOCK",
                })
            if results:
                return results

        # Fallback to HTML search
        try:
            search_url = f"{self.base_url}/search?q={quote_plus(query)}&type=product"
            html = await self.fetch_html(search_url)
            soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__dict__ else "html.parser")
            for item in soup.find_all("div", class_=re.compile(r"grid-product|product-item|card-wrapper", re.I))[:limit]:
                title_elem = item.find("a", class_=re.compile(r"title|name", re.I)) or item.find("h3") or item.find("a")
                if not title_elem:
                    continue
                url = title_elem.get("href", "")
                if url:
                    url = url.split("?")[0]
                    if not url.startswith("http"):
                        url = f"{self.base_url}{url}"

                txt = item.get_text(separator=" ", strip=True).lower()
                stock_status = "OUT_OF_STOCK" if "sold out" in txt or "out of stock" in txt else "IN_STOCK"
                results.append({
                    "retailer": self.name,
                    "retailer_id": self.retailer_id,
                    "title": title_elem.get_text(strip=True),
                    "url": url,
                    "price": None,
                    "currency": "INR",
                    "stock_status": stock_status,
                })
        except Exception as e:
            logger.error(f"ThinkRobotics HTML search error: {e}")

        return results
