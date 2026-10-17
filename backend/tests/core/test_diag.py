"""diag: one JSON line per event on stdout, secrets redacted, sizes capped, switchable with AIRA_DIAG."""
import json

import pytest

from aira_live import diag as d


@pytest.fixture(autouse=True)
def diag_on(monkeypatch):
    monkeypatch.delenv("AIRA_DIAG", raising=False)


def line(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out
    return json.loads(out[0])


def test_one_json_line_with_severity_event_and_fields(capsys):
    d.diag("tool", name="stock_query", ms=12, ok=True)
    rec = line(capsys)
    assert rec["severity"] == "INFO" and rec["aira"] == "tool"
    assert rec["name"] == "stock_query" and rec["ms"] == 12 and rec["ok"] is True
    assert isinstance(rec["ts"], float)


def test_severity_can_be_raised(capsys):
    d.diag("vision_error", severity="WARNING", role="point")
    assert line(capsys)["severity"] == "WARNING"


def test_disabled_with_env(capsys, monkeypatch):
    monkeypatch.setenv("AIRA_DIAG", "0")
    d.diag("tool", name="x")
    assert capsys.readouterr().out == ""


def test_suppressed_block_writes_nothing_and_restores(capsys):
    with d.suppressed():
        assert d.enabled() is False
        d.diag("tool", name="x")
    assert capsys.readouterr().out == "" and d.enabled() is True
    d.diag("tool", name="y")
    assert line(capsys)["name"] == "y"


def test_secret_keys_are_redacted_at_any_depth(capsys):
    d.diag("frame", jpeg="/9j/" * 100, nested={"idToken": "abc", "pcm16": "AAAA", "ok": "fine"},
           items=[{"key": "k"}])
    rec = line(capsys)
    raw = json.dumps(rec)
    assert rec["jpeg"] == "<redacted 400>"
    assert rec["nested"]["idToken"] == "<redacted 3>" and rec["nested"]["pcm16"] == "<redacted 4>"
    assert rec["nested"]["ok"] == "fine" and rec["items"][0]["key"] == "<redacted 1>"
    assert "/9j/" not in raw and "abc" not in raw and "AAAA" not in raw


def test_redaction_is_idempotent_so_a_summarized_value_keeps_its_size(capsys):
    d.diag("tool", args=d.summarize({"frames": [{"jpeg": "/9j/" * 100}]}))
    assert line(capsys)["args"]["frames"][0]["jpeg"] == "<redacted 400>"


def test_never_raises_on_an_unserializable_value(capsys):
    class Odd:
        def __repr__(self):
            return "<odd thing>"

    d.diag("x", obj=Odd(), data=b"\x00\x01")
    rec = line(capsys)
    assert rec["obj"] == "<odd thing>" and rec["data"] == "<2 bytes>"


def test_trunc_cuts_at_80_with_a_marker():
    assert d.trunc("a" * 80) == "a" * 80
    assert d.trunc("a" * 100) == "a" * 80 + "…(+20)"
    assert d.trunc(12345, 3) == "123…(+2)"


def test_summarize_truncates_strings_redacts_and_caps_lists():
    s = d.summarize({"question": "q" * 200, "jpeg_b64": "x" * 9, "n": 3, "items": list(range(20)),
                     "flag": True, "none": None, "deep": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}})
    assert s["question"] == "q" * 80 + "…(+120)"
    assert s["jpeg_b64"] == "<redacted 9>" and s["n"] == 3 and s["flag"] is True and s["none"] is None
    assert len(s["items"]) == 11 and s["items"][-1] == "…(+10)"
    assert s["deep"]["a"]["b"]["c"]["d"] == "…"


def test_cap_json_keeps_small_data_and_caps_big_data():
    small = {"beta": 131.2, "reason": "speak"}
    assert d.cap_json(small) is small
    capped = d.cap_json({"blob": "z" * 5000})
    assert capped["_truncated"] is True and capped["size"] > d.MAX_CLIENT_DATA
    assert len(json.dumps(capped)) <= d.MAX_CLIENT_DATA


def test_rate_limiter_allows_20_per_second_then_drops_until_the_next_second():
    rl = d.RateLimiter(20)
    assert all(rl.allow(now=100.0 + i * 0.01) for i in range(20))
    assert rl.allow(now=100.5) is False and rl.dropped == 1
    assert rl.allow(now=101.0) is True
