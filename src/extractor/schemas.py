from typing import Optional, Literal
from pydantic import BaseModel, Field


class ProductExtractionResult(BaseModel):
    title: Optional[str] = Field(default=None, description="Cleaned title of the product")
    stock_status: Literal["IN_STOCK", "OUT_OF_STOCK", "PRE_ORDER", "DISCONTINUED", "UNKNOWN"] = Field(
        default="UNKNOWN",
        description="Current availability status of the product"
    )
    price: Optional[float] = Field(default=None, description="Current selling price in INR (numeric value only)")
    currency: str = Field(default="INR", description="Currency code, e.g. INR")
    pre_order_eta: Optional[str] = Field(default=None, description="Estimated dispatch or shipping date if pre-order")
    specs_summary: Optional[str] = Field(default=None, description="Brief 1-2 sentence specification summary")
    confidence: float = Field(default=1.0, description="Extraction confidence score between 0.0 and 1.0")
