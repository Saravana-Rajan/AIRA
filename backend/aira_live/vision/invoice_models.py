"""Invoice result models. Field names are frozen by the interfaces doc; `invoice.py` re-exports them."""
from pydantic import BaseModel


class InvoiceLine(BaseModel):
    name: str
    qty: float
    unit: str
    unit_price: int
    line_total: int


class InvoiceRead(BaseModel):
    supplier: str | None
    date: str | None
    lines: list[InvoiceLine]
    total: int | None
    agreed: bool
    disagreements: list[str]
