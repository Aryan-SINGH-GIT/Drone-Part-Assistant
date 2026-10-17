import logging
import re
from typing import List, Dict, Any
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from src.adapters.base import BaseScraperAdapter

logger = logging.getLogger(__name__)


class EveltaAdapter(BaseScraperAdapter):
    def __init__(self):
        super().__init__(
            retailer_id="evelta",
            name="Evelta",
            base_url="https://evelta.com"
        )

    def matches_url(self, url: str) -> bool:
        return "evelta.com" in url.lower()

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        search_url = f"{self.base_url}/search.php?search_query={quote_plus(query)}"
        results = []
        try:
            html = await self.fetch_html(search_url)
            soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__dict__ else "html.parser")

            product_cards = soup.find_all("article", class_=re.compile(r"card|product", re.I))
            if not product_cards:
                product_cards = soup.find_all("div", class_=re.compile(r"card|product-item", re.I))

            for card in product_cards[:limit]:
                title_elem = card.find("h4") or card.find("h3") or card.find("a", class_=re.compile(r"card-title|title", re.I))
                link_elem = card.find("a", href=True)
                if not link_elem:
                    continue

                url = link_elem["href"]
                # Clean tracking query parameters
                url = url.split("?")[0].rstrip("/")
                if url and not url.startswith("http"):
                    url = f"{self.base_url}/{url.lstrip('/')}"
                if not url.endswith("/"):
                    url = f"{url}/"

                title = title_elem.get_text(strip=True) if title_elem else link_elem.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                price = None
                price_elem = card.find("span", class_=re.compile(r"price|amount", re.I))
                if price_elem:
                    m = re.search(r"[\d,]+(?:\.\d+)?", price_elem.get_text(strip=True))
                    if m:
                        try:
                            price = float(m.group(0).replace(",", ""))
                        except ValueError:
                            pass

                card_text = card.get_text(separator=" ", strip=True).lower()
                stock_status = "IN_STOCK"
                if "out of stock" in card_text or "sold out" in card_text:
                    stock_status = "OUT_OF_STOCK"
                elif "pre-order" in card_text or "preorder" in card_text:
                    stock_status = "PRE_ORDER"

                results.append({
                    "retailer": self.name,
                    "retailer_id": self.retailer_id,
                    "title": title,
                    "url": url,
                    "price": price,
                    "currency": "INR",
                    "stock_status": stock_status,
                })
        except Exception as e:
            logger.error(f"Evelta search error for '{query}': {e}")

        return results
