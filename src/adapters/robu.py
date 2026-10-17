import logging
import re
from typing import List, Dict, Any, Optional

try:
    from curl_cffi.requests import AsyncSession
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

from src.adapters.base import BaseScraperAdapter, Tuple_Result
from src.extractor.schemas import ProductExtractionResult
from src.extractor.html_cleaner import compute_content_hash

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = "https://robu.in/api/proxy/graphql/"

PRODUCT_DETAIL_QUERY = """
query ProductDetails($slug: String!) {
  productdetails(slug: $slug) {
    status
    code
    message
    data {
      id
      name
      sku
      in_stock
      available_quantity
      is_backorder
      backorder_message
      price {
        regular
        final
        special
      }
    }
  }
}
"""

SEARCH_QUERY = """
query CategoryHoverProducts($search: String, $sort: String, $limit: Int) {
  visibleMenuCategories(search: $search, sort: $sort, limit: $limit) {
    status
    message
    data {
      products {
        id
        name
        slug
        sku
        price
        sale_price
        in_stock
      }
    }
  }
}
"""


def _slug_from_url(url: str) -> Optional[str]:
    """Extracts clean product slug from a robu.in product URL."""
    clean = url.split("?")[0].rstrip("/")
    m = re.search(r'/product/([^/?#]+)', clean)
    if m:
        return m.group(1)
    # Fallback to last segment
    parts = clean.split("/")
    return parts[-1] if parts else None


class RobuAdapter(BaseScraperAdapter):
    def __init__(self):
        super().__init__(
            retailer_id="robu",
            name="Robu.in",
            base_url="https://robu.in"
        )

    def matches_url(self, url: str) -> bool:
        return "robu.in" in url.lower()

    async def _graphql_request(self, query: str, variables: dict) -> Optional[dict]:
        """Executes a GraphQL query against the Robu Next.js proxy endpoint."""
        if not HAS_CURL_CFFI:
            return None
        try:
            async with AsyncSession(impersonate="chrome120") as s:
                resp = await s.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": query, "variables": variables},
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Origin": "https://robu.in",
                        "Referer": "https://robu.in/",
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data")
        except Exception as e:
            logger.debug(f"Robu GraphQL request error: {e}")
        return None

    async def extract_product_data(self, url: str, html: Optional[str] = None) -> Tuple_Result:
        """
        Extracts product availability and price via GraphQL productdetails.
        Gracefully falls back to HTML/LLM extraction.
        """
        slug = _slug_from_url(url)
        if slug:
            gql_data = await self._graphql_request(PRODUCT_DETAIL_QUERY, {"slug": slug})
            if gql_data:
                pd = gql_data.get("productdetails", {})
                pdata = pd.get("data")
                if pdata and isinstance(pdata, dict):
                    in_stock = pdata.get("in_stock", False)
                    is_backorder = pdata.get("is_backorder", False)
                    qty = pdata.get("available_quantity", 0)

                    if in_stock or (qty and qty > 0):
                        stock_status = "IN_STOCK"
                    elif is_backorder:
                        stock_status = "PRE_ORDER"
                    else:
                        stock_status = "OUT_OF_STOCK"

                    price_info = pdata.get("price") or {}
                    price = price_info.get("final") or price_info.get("regular") or price_info.get("special")
                    if price:
                        try:
                            price = float(price)
                        except ValueError:
                            price = None

                    eta = pdata.get("backorder_message") if is_backorder else None

                    result = ProductExtractionResult(
                        title=pdata.get("name"),
                        stock_status=stock_status,
                        price=price,
                        currency="INR",
                        pre_order_eta=eta,
                        confidence=0.99,
                        specs_summary=pdata.get("name"),
                    )
                    content_hash = compute_content_hash(str(pdata))
                    return result, content_hash

        # Fallback to standard base HTML / LLM parser
        return await super().extract_product_data(url, html)

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Searches Robu catalog via GraphQL visibleMenuCategories."""
        results = []
        gql_data = await self._graphql_request(SEARCH_QUERY, {"search": query, "sort": "latest", "limit": limit})
        if gql_data:
            categories = gql_data.get("visibleMenuCategories", {}).get("data", [])
            seen_ids = set()
            for cat in categories:
                products = cat.get("products", [])
                for p in products:
                    pid = p.get("id")
                    if pid and pid in seen_ids:
                        continue
                    seen_ids.add(pid)

                    slug = p.get("slug")
                    url = f"{self.base_url}/product/{slug}/" if slug else ""
                    price = p.get("sale_price") or p.get("price")
                    in_stock = p.get("in_stock", False)

                    results.append({
                        "retailer": self.name,
                        "retailer_id": self.retailer_id,
                        "title": p.get("name", ""),
                        "url": url,
                        "price": float(price) if price else None,
                        "currency": "INR",
                        "stock_status": "IN_STOCK" if in_stock else "OUT_OF_STOCK",
                    })
                    if len(results) >= limit:
                        return results

        return results
