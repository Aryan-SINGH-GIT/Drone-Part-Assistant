import hashlib
import json
import re
from typing import Optional, Dict, Any, Tuple
from bs4 import BeautifulSoup


def compute_content_hash(text: str) -> str:
    """Computes SHA-256 hash of cleaned text to skip LLM calls on unchanged pages."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_json_ld_microdata(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """Attempts to find and parse schema.org/Product JSON-LD."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
            # JSON-LD could be a list, or a graph dict
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Product":
                        return item
            elif isinstance(data, dict):
                if data.get("@type") == "Product":
                    return data
                if "@graph" in data and isinstance(data["@graph"], list):
                    for item in data["@graph"]:
                        if item.get("@type") == "Product":
                            return item
        except Exception:
            continue
    return None


def clean_html_for_llm(html: str, max_chars: int = 2500) -> Tuple[str, Optional[Dict[str, Any]], str]:
    """
    Cleans raw HTML into a dense, token-efficient text representation for the LLM.
    Returns:
        (cleaned_text, json_ld_data, content_hash)
    """
    soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__dict__ else "html.parser")

    # 1. Try to extract JSON-LD first
    json_ld = extract_json_ld_microdata(soup)

    # 2. Extract title
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    # 3. Extract OpenGraph / Meta price & availability if present
    meta_price = ""
    for prop in ["product:price:amount", "og:price:amount"]:
        meta = soup.find("meta", property=prop)
        if meta and meta.get("content"):
            meta_price = meta["content"].strip()
            break

    meta_availability = ""
    for prop in ["product:availability", "og:availability"]:
        meta = soup.find("meta", property=prop)
        if meta and meta.get("content"):
            meta_availability = meta["content"].strip()
            break

    # 4. Remove noisy non-content tags
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form"]):
        # Keep button or submit inputs if inside form before removing
        tag.decompose()

    # 5. Look for typical e-commerce containers
    main_container = (
        soup.find("div", class_=re.compile(r"product(-detail|-single|_single|entry)", re.I))
        or soup.find("main")
        or soup.find("article")
        or soup.body
    )

    extracted_lines = []
    if title:
        extracted_lines.append(f"Title: {title}")
    if meta_price:
        extracted_lines.append(f"Meta Price: {meta_price}")
    if meta_availability:
        extracted_lines.append(f"Meta Availability: {meta_availability}")

    if main_container:
        # Extract text elements
        for element in main_container.find_all(["h1", "h2", "p", "span", "div", "button", "li"]):
            cls = " ".join(element.get("class", []))
            txt = element.get_text(separator=" ", strip=True)
            if not txt:
                continue

            # Prioritize elements that hint at stock, price, or buttons
            is_pertinent = any(
                keyword in (cls + " " + txt).lower()
                for keyword in [
                    "stock", "availab", "cart", "buy", "sold", "pre-order", "preorder",
                    "eta", "dispatch", "price", "₹", "rs", "inr", "lead time"
                ]
            )
            if is_pertinent and len(txt) < 300:
                extracted_lines.append(f"{cls}: {txt}" if cls else txt)

    # If pertinent lines are few, fall back to getting stripped text
    if len(extracted_lines) < 3 and main_container:
        cleaned_body = main_container.get_text(separator="\n", strip=True)
        # Take first 1500 chars
        extracted_lines.append(cleaned_body[:1500])

    # De-duplicate adjacent lines
    unique_lines = []
    for line in extracted_lines:
        line_clean = re.sub(r"\s+", " ", line).strip()
        if line_clean and (not unique_lines or unique_lines[-1] != line_clean):
            unique_lines.append(line_clean)

    combined_text = "\n".join(unique_lines)[:max_chars]
    content_hash = compute_content_hash(combined_text)

    return combined_text, json_ld, content_hash
