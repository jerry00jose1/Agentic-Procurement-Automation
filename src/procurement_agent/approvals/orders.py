"""The only ERP write this project can propose: one draft purchase order."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class PurchaseOrderLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_code: str
    qty: float = Field(gt=0)
    rate: float = Field(ge=0)


class DraftPurchaseOrder(BaseModel):
    """Exact parameters that will be sent to ERPNext after approval."""

    model_config = ConfigDict(extra="forbid")

    supplier: str
    company: str
    warehouse: str
    transaction_date: date
    schedule_date: date
    currency: str = "EUR"
    items: list[PurchaseOrderLine] = Field(min_length=1)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()
