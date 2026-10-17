import json
import logging
import re
from typing import Optional, Dict, Any

from src.core.config import settings
from src.extractor.schemas import ProductExtractionResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a specialized e-commerce extraction engine for Indian drone and electronics retail sites.
Analyze the provided cleaned page content and extract structured product information.

Classification guidelines for stock_status:
- "IN_STOCK": The product can currently be purchased / added to cart.
- "OUT_OF_STOCK": Clearly marked as out of stock, sold out, or unavailable.
- "PRE_ORDER": Available to book or purchase in advance with a future dispatch date or ETA.
- "DISCONTINUED": Product has been retired or discontinued.
- "UNKNOWN": Not enough unambiguous evidence.

Return ONLY a valid JSON object matching this schema:
{
  "title": "string or null",
  "stock_status": "IN_STOCK" | "OUT_OF_STOCK" | "PRE_ORDER" | "DISCONTINUED" | "UNKNOWN",
  "price": number or null (e.g. 7499.0),
  "currency": "INR",
  "pre_order_eta": "string or null (e.g. 'Dispatching in 2 weeks')",
  "specs_summary": "string or null",
  "confidence": number between 0.0 and 1.0
}
"""


def heuristic_fallback_parse(cleaned_text: str, json_ld: Optional[Dict[str, Any]] = None) -> ProductExtractionResult:
    """
    Fallback deterministic parser using regex and JSON-LD schema.org data.
    Ensures the system functions even without LLM API keys.
    """
    title = None
    stock_status = "UNKNOWN"
    price = None
    pre_order_eta = None

    # Check JSON-LD
    if json_ld:
        title = json_ld.get("name")
        offers = json_ld.get("offers", {})
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict):
            avail = str(offers.get("availability", "")).lower()
            if "instock" in avail:
                stock_status = "IN_STOCK"
            elif "outofstock" in avail or "soldout" in avail:
                stock_status = "OUT_OF_STOCK"
            elif "preorder" in avail or "backorder" in avail:
                stock_status = "PRE_ORDER"

            price_val = offers.get("price")
            if price_val:
                try:
                    price = float(re.sub(r"[^\d.]", "", str(price_val)))
                except ValueError:
                    pass

    # Regex analysis on text
    lower_text = cleaned_text.lower()

    if not title:
        m = re.search(r"title:\s*(.+)", cleaned_text, re.IGNORECASE)
        if m:
            title = m.group(1).strip()

    # Stock determination if still unknown
    if stock_status == "UNKNOWN":
        if any(w in lower_text for w in ["pre-order", "preorder", "booking now", "dispatch from"]):
            stock_status = "PRE_ORDER"
            eta_match = re.search(r"(dispatch\s*(?:from|by|in)|eta:?|arriving\s*by)\s*([^.\n]+)", lower_text)
            if eta_match:
                pre_order_eta = eta_match.group(0).strip()
        elif any(w in lower_text for w in ["out of stock", "sold out", "currently unavailable"]):
            stock_status = "OUT_OF_STOCK"
        elif any(w in lower_text for w in ["in stock", "add to cart", "buy now"]):
            stock_status = "IN_STOCK"

    # Price determination if still missing
    if price is None:
        price_matches = re.findall(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{2})?)", cleaned_text, re.IGNORECASE)
        if price_matches:
            try:
                price = float(price_matches[0].replace(",", ""))
            except ValueError:
                pass

    return ProductExtractionResult(
        title=title,
        stock_status=stock_status,
        price=price,
        currency="INR",
        pre_order_eta=pre_order_eta,
        specs_summary=title,
        confidence=0.8 if stock_status != "UNKNOWN" else 0.4,
    )


_GEMINI_KEY_VALID: Optional[bool] = None


async def parse_product_with_llm(
    cleaned_text: str,
    json_ld: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None
) -> ProductExtractionResult:
    """
    Parses product availability and attributes using a lightweight LLM.
    Gracefully falls back to heuristic parsing if API key is missing or call fails.
    """
    global _GEMINI_KEY_VALID

    # 1. Check if LLM is enabled and configured
    provider = settings.LLM_PROVIDER

    if provider == "mock":
        return heuristic_fallback_parse(cleaned_text, json_ld)

    # 2. Gemini 1.5 Flash
    if provider == "gemini" and settings.GEMINI_API_KEY and _GEMINI_KEY_VALID is not False:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            prompt = f"Product URL: {url or 'N/A'}\n\nContent:\n{cleaned_text}"

            response = client.models.generate_content(
                model="gemini-1.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=ProductExtractionResult,
                    temperature=0.1,
                ),
            )
            data = json.loads(response.text)
            _GEMINI_KEY_VALID = True
            return ProductExtractionResult(**data)
        except Exception as e:
            if "API_KEY_INVALID" in str(e) or "400" in str(e):
                _GEMINI_KEY_VALID = False
                logger.info("Gemini API key is invalid or unconfigured. Auto-switching to fast heuristic parser.")
            else:
                logger.warning(f"Gemini API parse failed: {e}. Falling back to heuristic.")
            return heuristic_fallback_parse(cleaned_text, json_ld)

    # 3. OpenAI GPT-4o-mini / Groq Llama 3
    if (provider == "openai" and settings.OPENAI_API_KEY) or (provider == "groq" and settings.GROQ_API_KEY):
        try:
            from openai import AsyncOpenAI
            if provider == "groq":
                client = AsyncOpenAI(api_key=settings.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
                model_name = "llama-3.1-8b-instant"
            else:
                client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                model_name = "gpt-4o-mini"

            prompt = f"Product URL: {url or 'N/A'}\n\nContent:\n{cleaned_text}"
            res = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            content = res.choices[0].message.content
            data = json.loads(content)
            return ProductExtractionResult(**data)
        except Exception as e:
            logger.warning(f"{provider} API parse failed: {e}. Falling back to heuristic.")
            return heuristic_fallback_parse(cleaned_text, json_ld)

    # If no valid API key was found, use heuristic parser
    return heuristic_fallback_parse(cleaned_text, json_ld)
