import pytest
from src.extractor.html_cleaner import clean_html_for_llm, extract_json_ld_microdata
from bs4 import BeautifulSoup


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Holybro Pixhawk 6X Autopilot Flight Controller - ThinkRobotics</title>
    <meta property="og:title" content="Holybro Pixhawk 6X Autopilot Flight Controller">
    <meta property="product:price:amount" content="36999.00">
    <meta property="product:availability" content="instock">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": "Holybro Pixhawk 6X Autopilot Flight Controller",
        "offers": {
            "@type": "Offer",
            "price": "36999.00",
            "priceCurrency": "INR",
            "availability": "https://schema.org/InStock"
        }
    }
    </script>
    <style>.banner { color: red; }</style>
</head>
<body>
    <header><nav><a href="/">Home</a></nav></header>
    <main>
        <div class="product-single">
            <h1>Holybro Pixhawk 6X Autopilot Flight Controller</h1>
            <span class="price">₹36,999.00</span>
            <div class="stock in-stock">In Stock (5 units left)</div>
            <button type="submit" name="add">Add to Cart</button>
        </div>
    </main>
    <footer>Copyright 2026</footer>
</body>
</html>
"""


def test_clean_html_extracts_json_ld():
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    json_ld = extract_json_ld_microdata(soup)
    assert json_ld is not None
    assert json_ld["name"] == "Holybro Pixhawk 6X Autopilot Flight Controller"
    assert json_ld["offers"]["price"] == "36999.00"


def test_clean_html_strips_noise():
    cleaned_text, json_ld, content_hash = clean_html_for_llm(SAMPLE_HTML)
    assert "banner" not in cleaned_text  # style stripped
    assert "Copyright" not in cleaned_text  # footer stripped
    assert "Holybro Pixhawk 6X" in cleaned_text
    assert len(content_hash) == 64
