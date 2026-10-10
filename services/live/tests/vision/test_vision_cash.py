"""Cash reading: two independent reads, INR denominations only, totals computed in code, hint keys.

Fully offline: `generate_json` and `image_part` are monkeypatched on `aira_live.vision.cash`.
"""
import base64
import json
from pathlib import Path

import pytest

from aira_live.frames import Frame
from aira_live.i18n import LANGS
from aira_live.vision import cash
from aira_live.vision.cash_merge import COINS, NOTES, VALID_DENOMINATIONS, merge_cash
from aira_live.vision.cash_models import CashRead

I18N_DIR = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"
CASH_KEYS = ("cash.spread_notes", "cash.unsupported_note", "cash.show_again")

A_1000 = {"items": [{"denomination": 500, "count": 2, "kind": "note"}], "overlapping": False, "confidence": 0.9}
B_1000 = {"pieces": [500, 500], "overlapping": False, "confidence": 0.85}

# Fixed JPEG-looking bytes: image_part is faked, so no image library is needed.
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\xff\xd9").decode()


# ---------- merge_cash (pure) ----------
def test_agreeing_reads_total_in_code():
    r = merge_cash(A_1000, B_1000)
    assert r.agreed is True
    assert r.total == 1000
    assert r.notes == [{"denomination": 500, "count": 2}]
    assert r.confidence == 0.85
    assert r.hint_key == ""


def test_mixed_notes_and_coins_sorted_desc():
    a = {"items": [{"denomination": 100, "count": 1, "kind": "note"}, {"denomination": 10, "count": 2, "kind": "coin"},
                   {"denomination": 50, "count": 1, "kind": "note"}], "overlapping": False, "confidence": 0.8}
    b = {"pieces": [10, 100, 50, 10], "overlapping": False, "confidence": 0.8}
    r = merge_cash(a, b)
    assert r.agreed is True and r.total == 170
    assert [n["denomination"] for n in r.notes] == [100, 50, 10]


def test_disagreement_means_no_total():
    b = {"pieces": [500, 100], "overlapping": False, "confidence": 0.9}
    r = merge_cash(A_1000, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.show_again"
    assert r.confidence < 0.5


def test_overlapping_notes_ask_to_spread():
    b = dict(B_1000, overlapping=True)
    r = merge_cash(A_1000, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.spread_notes"


def test_overlap_reported_by_read_a_only_also_asks_to_spread():
    r = merge_cash(dict(A_1000, overlapping=True), B_1000)
    assert r.agreed is False and r.total is None and r.hint_key == "cash.spread_notes"


def test_unsupported_denomination_flagged():
    a = {"items": [{"denomination": 2000, "count": 1, "kind": "note"}], "overlapping": False, "confidence": 0.9}
    b = {"pieces": [2000], "overlapping": False, "confidence": 0.9}
    r = merge_cash(a, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.unsupported_note"
    assert r.notes == []


def test_unsupported_note_among_valid_ones_is_never_counted():
    a = {"items": [{"denomination": 500, "count": 1, "kind": "note"}, {"denomination": 2000, "count": 1, "kind": "note"}],
         "overlapping": False, "confidence": 0.9}
    b = {"pieces": [2000, 500], "overlapping": False, "confidence": 0.9}
    r = merge_cash(a, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.unsupported_note"
    assert r.notes == [{"denomination": 500, "count": 1}]


def test_note_and_coin_kinds_must_match_real_inr_money():
    # There is no 500 coin and (in this product) no 5 note: both are unsupported, never counted.
    a = {"items": [{"denomination": 500, "count": 1, "kind": "coin"}], "overlapping": False, "confidence": 0.9}
    r = merge_cash(a, {"pieces": [500], "overlapping": False, "confidence": 0.9})
    assert r.agreed is False and r.hint_key == "cash.unsupported_note" and r.notes == []
    a5 = {"items": [{"denomination": 5, "count": 1, "kind": "note"}], "overlapping": False, "confidence": 0.9}
    r5 = merge_cash(a5, {"pieces": [5], "overlapping": False, "confidence": 0.9})
    assert r5.agreed is False and r5.hint_key == "cash.unsupported_note"
    # 10 and 20 exist as both notes and coins.
    a10 = {"items": [{"denomination": 10, "count": 1, "kind": "note"}, {"denomination": 10, "count": 1, "kind": "coin"}],
           "overlapping": False, "confidence": 0.9}
    r10 = merge_cash(a10, {"pieces": [10, 10], "overlapping": False, "confidence": 0.9})
    assert r10.agreed is True and r10.total == 20 and r10.notes == [{"denomination": 10, "count": 2}]


def test_nothing_seen():
    r = merge_cash({"items": [], "overlapping": False, "confidence": 0.2}, {"pieces": [], "confidence": 0.2})
    assert r.agreed is False and r.hint_key == "cash.show_again"
    assert r.total is None


def test_valid_denominations_are_inr_only():
    assert NOTES == {10, 20, 50, 100, 200, 500}
    assert COINS == {1, 2, 5, 10, 20}
    assert VALID_DENOMINATIONS == {1, 2, 5, 10, 20, 50, 100, 200, 500}
    assert cash.VALID_DENOMINATIONS is VALID_DENOMINATIONS
    assert 2000 not in VALID_DENOMINATIONS and 1000 not in VALID_DENOMINATIONS


def test_model_total_is_ignored_and_extra_keys_are_harmless():
    # The total always comes from code; a model-supplied total and generate_json's "_model" key are ignored.
    a = dict(A_1000, total=9999, _model="read-model")
    b = dict(B_1000, total=5, _model="verify-model")
    r = merge_cash(a, b)
    assert r.agreed is True and r.total == 1000


def test_list_reply_wrapped_as_items_counts_as_read_b_pieces():
    # generate_json wraps a bare JSON list reply as {"items": [...]}.
    r = merge_cash(A_1000, {"items": [500, 500], "_model": "verify-model"})
    assert r.agreed is True and r.total == 1000
    assert r.confidence == 0.0  # no confidence given by read B


def test_numeric_strings_are_normalised():
    a = {"items": [{"denomination": "₹500", "count": "2", "kind": "note"}], "overlapping": False, "confidence": "0.9"}
    b = {"pieces": ["500", "௫௦௦"], "overlapping": "false", "confidence": 0.8}  # Tamil digits for 500
    r = merge_cash(a, b)
    assert r.agreed is True and r.total == 1000 and r.confidence == 0.8


def test_confidence_is_clamped_to_0_1():
    r = merge_cash(dict(A_1000, confidence=1.7), dict(B_1000, confidence=3))
    assert r.agreed is True and r.confidence == 1.0
    r = merge_cash(dict(A_1000, confidence=-0.4), B_1000)
    assert r.agreed is True and r.confidence == 0.0


@pytest.mark.parametrize("raw_a, raw_b", [
    (None, None),
    ("500", ["500"]),
    ({"items": "500 x 2"}, {"pieces": "500, 500"}),
    ({"items": [None, 5, "x"]}, {"pieces": [None, {}, []]}),
    ({"items": [{"denomination": True, "count": 1}]}, {"pieces": [True]}),
    ({"items": [{"denomination": 10.7, "count": 1}]}, {"pieces": [10.7]}),
    ({"items": [{"denomination": 500, "count": 1.5}]}, {"pieces": [500]}),
    ({"items": [{"denomination": 500, "count": -2}]}, {"pieces": []}),
    ({"items": [{"denomination": float("nan"), "count": float("inf")}]}, {"pieces": [float("inf")]}),
    (dict(A_1000, confidence="high"), dict(B_1000, confidence=float("nan"))),
    (dict(A_1000, confidence=None), dict(B_1000, confidence=[0.9])),
])
def test_malformed_reads_never_raise(raw_a, raw_b):
    r = merge_cash(raw_a, raw_b)
    assert isinstance(r, CashRead)
    assert 0.0 <= r.confidence <= 1.0
    if not r.agreed:
        assert r.total is None and r.hint_key in CASH_KEYS


def test_malformed_values_are_never_counted_and_ask_to_show_again():
    a = {"items": [{"denomination": True, "count": 1}, {"denomination": 10.7, "count": 1}], "overlapping": False,
         "confidence": 0.9}
    r = merge_cash(a, {"pieces": [True, 10.7], "overlapping": False, "confidence": 0.9})
    assert r.agreed is False and r.total is None
    assert r.notes == []
    assert r.hint_key == "cash.show_again"


def test_malformed_entry_next_to_valid_ones_blocks_agreement():
    a = {"items": [{"denomination": 500, "count": 2, "kind": "note"}, {"denomination": "smudge", "count": 1}],
         "overlapping": False, "confidence": 0.9}
    r = merge_cash(a, B_1000)
    assert r.agreed is False and r.total is None and r.hint_key == "cash.show_again"


# ---------- read_cash (two concurrent reads) ----------
def _frame(fid="c1", w=320, h=200, ts=1.0, b64=JPEG_B64) -> Frame:
    return Frame(id=fid, jpeg_b64=b64, w=w, h=h, ts=ts, purpose="cash")


def _patch(monkeypatch, replies):
    """replies: role -> dict reply, or an Exception to raise."""
    calls = []

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        calls.append({"role": role, "prompt": parts[0], "image": parts[1], "schema": schema_hint})
        reply = replies[role]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(cash, "generate_json", fake)
    monkeypatch.setattr(cash, "image_part", lambda b64: b64)
    return calls


async def test_read_cash_two_roles(monkeypatch):
    calls = _patch(monkeypatch, {"read": A_1000, "verify": B_1000})
    r = await cash.read_cash([_frame()])
    assert r.agreed is True and r.total == 1000
    assert sorted(c["role"] for c in calls) == ["read", "verify"]


async def test_read_cash_uses_two_different_prompts_and_shapes(monkeypatch):
    calls = _patch(monkeypatch, {"read": A_1000, "verify": B_1000})
    await cash.read_cash([_frame()])
    by_role = {c["role"]: c for c in calls}
    assert by_role["read"]["prompt"] == cash.PROMPT_A and by_role["read"]["schema"] == cash.SCHEMA_A
    assert by_role["verify"]["prompt"] == cash.PROMPT_B and by_role["verify"]["schema"] == cash.SCHEMA_B
    assert cash.SCHEMA_A != cash.SCHEMA_B
    assert '"pieces"' in cash.SCHEMA_B and '"items"' in cash.SCHEMA_A


async def test_read_cash_picks_the_largest_newest_frame(monkeypatch):
    calls = _patch(monkeypatch, {"read": A_1000, "verify": B_1000})
    small = _frame("small", 160, 120, 5.0, b64="c21hbGw=")
    big_old = _frame("big_old", 640, 480, 1.0, b64="Ymlnb2xk")
    big_new = _frame("big_new", 640, 480, 2.0, b64="YmlnbmV3")
    await cash.read_cash([small, big_old, big_new])
    assert {c["image"] for c in calls} == {"YmlnbmV3"}  # both reads look at the same, best frame (colour kept)


async def test_read_cash_one_read_fails(monkeypatch):
    _patch(monkeypatch, {"read": A_1000, "verify": TimeoutError("slow")})
    r = await cash.read_cash([_frame()])
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.show_again"


async def test_read_cash_first_read_fails(monkeypatch):
    _patch(monkeypatch, {"read": RuntimeError("api down"), "verify": B_1000})
    r = await cash.read_cash([_frame()])
    assert r.agreed is False and r.total is None and r.hint_key == "cash.show_again"
    assert r.confidence == 0.0


async def test_read_cash_both_reads_fail(monkeypatch):
    _patch(monkeypatch, {"read": RuntimeError("a"), "verify": TimeoutError("b")})
    r = await cash.read_cash([_frame()])
    assert r == CashRead(notes=[], total=None, agreed=False, confidence=0.0, hint_key="cash.show_again")


async def test_read_cash_non_dict_reply_is_a_failed_read(monkeypatch):
    _patch(monkeypatch, {"read": A_1000, "verify": ["500", "500"]})
    r = await cash.read_cash([_frame()])
    assert r.agreed is False and r.total is None and r.hint_key == "cash.show_again"


async def test_read_cash_keeps_spread_hint_when_one_read_fails(monkeypatch):
    # Overlap seen by the read that worked is still the most useful thing to say.
    _patch(monkeypatch, {"read": dict(A_1000, overlapping=True), "verify": TimeoutError("slow")})
    r = await cash.read_cash([_frame()])
    assert r.agreed is False and r.total is None and r.hint_key == "cash.spread_notes"


async def test_read_cash_bad_image_never_raises(monkeypatch):
    _patch(monkeypatch, {"read": A_1000, "verify": B_1000})

    def broken(_b64):
        raise ValueError("not a jpeg")

    monkeypatch.setattr(cash, "image_part", broken)
    r = await cash.read_cash([_frame()])
    assert r.agreed is False and r.total is None and r.hint_key == "cash.show_again"


async def test_read_cash_no_frames():
    r = await cash.read_cash([])
    assert r.agreed is False and r.hint_key == "cash.show_again"
    assert r.total is None and r.notes == []


def test_read_cash_reexports_interface_model():
    assert cash.CashRead is CashRead
    assert CashRead.model_fields["hint_key"].default == ""


# ---------- safety: no genuineness claims; hint keys spoken in every language ----------
def test_prompts_never_ask_for_a_genuineness_verdict():
    for prompt in (cash.PROMPT_A, cash.PROMPT_B):
        low = prompt.lower()
        assert "do not judge" in low or "never comment" in low
    assert "counterfeit" not in CashRead.model_json_schema().__repr__().lower()


def test_cash_hint_keys_exist_in_every_language():
    for lang in LANGS:
        data = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        for key in CASH_KEYS:
            assert data.get(key), f"{lang} is missing {key}"


def test_cash_hints_never_claim_to_detect_fake_notes():
    en = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))
    for key in CASH_KEYS:
        low = en[key].lower()
        for word in ("fake", "counterfeit", "genuine", "forged", "real note"):
            assert word not in low, f"{key} mentions {word!r}"
