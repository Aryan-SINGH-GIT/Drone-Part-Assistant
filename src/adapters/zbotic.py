import logging
import re
from typing import List, Dict, Any
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from src.adapters.base import BaseScraperAdapter

logger = logging.getLogger(__name__)


class ZboticAdapter(BaseScraperAdapter):
    def __init__(self):
        super().__init__(
            retailer_id="zbotic",
            name="Zbotic",
            base_url="https://zbotic.in"
        )

    def matches_url(self, url: str) -> bool:
        return "zbotic.in" in url.lower()

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        search_url = f"{self.base_url}/?s={quote_plus(query)}&post_type=product"
        results = []
        try:
            html = await self.fetch_html(search_url)
            soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__dict__ else "html.parser")

            product_cards = soup.find_all("div", class_=re.compile(r"product-(?:inner|small|item)|type-product", re.I))
            if not product_cards:
                product_cards = soup.find_all("li", class_=re.compile(r"product", re.I))

            for card in product_cards[:limit * 2]:
                # Specifically search for actual product URL (skip wishlist, compare, my-account)
                product_link = None
                for a in card.find_all("a", href=True):
                    href = a["href"]
                    if (
                        "/product/" in href
                        and "wishlist" not in href
                        and "compare" not in href
                        and "my-account" not in href
                        and "add-to-cart" not in href
                    ):
                        product_link = a
                        break

                if not product_link:
                    continue

                url = product_link["href"]

                # Extract title: look for heading or title attribute or anchor text
                title_elem = (
                    card.find("h3")
                    or card.find("h2")
                    or card.find("a", class_=re.compile(r"title|name", re.I))
                    or product_link
                )
                title = title_elem.get_text(strip=True) if title_elem else ""
                if not title or len(title) < 3 or title.lower() in ["out of stock", "add to cart"]:
                    # Try title attribute
                    title = product_link.get("title") or ""
                if not title or len(title) < 3:
                    continue

                price = None
                price_elem = card.find("span", class_=re.compile(r"price|amount", re.I))
                if price_elem:
                    price_txt = price_elem.get_text(strip=True)
                    m = re.search(r"[\d,]+(?:\.\d+)?", price_txt)
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

                if len(results) >= limit:
                    break
        except Exception as e:
            logger.error(f"Zbotic search error for '{query}': {e}")

        return results
