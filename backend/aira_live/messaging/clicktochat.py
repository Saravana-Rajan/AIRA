"""Zero-setup fallbacks: a WhatsApp click-to-chat link and a phone-call link, plus the order wording."""
import re
from urllib.parse import quote

from aira_live.business.delivery_time import delivery_by_words


def normalize_in_phone(phone: str) -> str:
    """Digits only, with the Indian country code: '+91 90000 00002' / '9000000002' / '09000000002' → '919000000002'."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def wa_link(phone: str, text: str) -> str:
    """https://wa.me/<digits>?text=<text>. Without a number, WhatsApp opens with the text and asks for the chat."""
    return f"https://wa.me/{normalize_in_phone(phone)}?text={quote(text)}"


def tel_link(phone: str) -> str:
    """tel:+919000000003, or a bare "tel:" (opens the dialer) when the supplier has no number yet."""
    digits = normalize_in_phone(phone)
    return f"tel:+{digits}" if digits else "tel:"


def fmt_qty(q: float) -> str:
    q = float(q)
    return str(int(q)) if q.is_integer() else f"{q:g}"


def items_text(items: list[dict]) -> str:
    """[{name, qty, unit}] → '30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g' (single line: WhatsApp template rule)."""
    return ", ".join(f"{fmt_qty(it['qty'])} {it['unit']} {it['name']}" for it in items)


def order_summary(items: list[dict], delivery_by: dict | None = None, note: str | None = None) -> str:
    """One line for the WhatsApp template parameter: items, then '; deliver by …' and '; note: …' when present."""
    parts = [items_text(items)]
    time_words = delivery_by_words(delivery_by)
    if time_words:
        parts.append(f"deliver by {time_words}")
    if note:
        parts.append(f"note: {note}")
    return "; ".join(parts)


def order_text(shop_name: str, items: list[dict], delivery_by: dict | None = None, note: str | None = None) -> str:
    """The vendor message (Telegram, wa.me link): one line per item, the delivery time and note when the owner
    gave them, and a clear ask to reply."""
    lines = [f"New order from {shop_name}", *(f"- {fmt_qty(it['qty'])} {it['unit']} {it['name']}" for it in items)]
    time_words = delivery_by_words(delivery_by)
    if time_words:
        lines.append(f"Deliver by: {time_words}")
    if note:
        lines.append(f"Note: {note}")
    lines.append("Reply here to confirm.")
    return "\n".join(lines)
