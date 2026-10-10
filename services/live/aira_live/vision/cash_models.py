"""CashRead: the result of reading cash (re-exported by aira_live.vision.cash, per the interfaces doc)."""
from pydantic import BaseModel


class CashRead(BaseModel):
    notes: list[dict]          # [{"denomination": int, "count": int}], highest first
    total: int | None          # computed in code; None unless both reads agree
    agreed: bool
    confidence: float          # 0..1
    hint_key: str = ""         # i18n key to speak when not agreed, e.g. "cash.spread_notes" (additive field)
