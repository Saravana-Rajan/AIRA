"""Zero-setup fallbacks: a WhatsApp click-to-chat link and a phone-call link, plus the order wording."""
import re
from urllib.parse import quote


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


def order_text(shop_name: str, items: list[dict]) -> str:
    return f"New order from {shop_name}: {items_text(items)}. Please confirm delivery time."
