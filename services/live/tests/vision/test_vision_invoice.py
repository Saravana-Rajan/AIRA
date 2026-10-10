"""read_invoice(): two concurrent, independent reads (original + enhanced image), merged in code.

Fully offline: `generate_json` and `image_part` are monkeypatched on `aira_live.vision.invoice`.
"""
import base64
import io

from PIL import Image

from aira_live.frames import Frame
from aira_live.vision import invoice
from aira_live.vision.imaging import enhance_for_reading

LINES = [{"name": "Sakthi Curd 1L", "qty": "30 L", "unit": "L", "unit_price": "33", "line_total": "990"}]
RAW = {"supplier": "Sakthi Dairy", "date": "2026-10-10", "handwritten": False, "lines": LINES, "total": "990"}


def jpeg_b64(w=320, h=200) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 230, 220)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def mk_frame(w=320, h=200) -> Frame:
    return Frame(id="f1", jpeg_b64=jpeg_b64(w, h), w=w, h=h, ts=1.0, purpose="invoice")


def patch(monkeypatch, read_a, read_b, fail_b=False):
    calls = []

    async def fake_generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        calls.append({"role": role, "prompt": parts[0], "image": parts[1], "schema": schema_hint})
        if role == "read":
            return read_a
        if fail_b:
            raise TimeoutError("verify timed out")
        return read_b

    monkeypatch.setattr(invoice, "generate_json", fake_generate_json)
    monkeypatch.setattr(invoice, "image_part", lambda b64: b64)
    return calls


def test_enhance_for_reading_returns_larger_grayscale_jpeg():
    out = enhance_for_reading(jpeg_b64(320, 200))
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert img.mode == "L"
    assert img.width >= 480


def test_enhance_for_reading_keeps_large_images_at_size():
    out = enhance_for_reading(jpeg_b64(1600, 1000))
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert img.mode == "L"
    assert (img.width, img.height) == (1600, 1000)


def test_reexports_interface_models():
    from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead

    assert invoice.InvoiceLine is InvoiceLine and invoice.InvoiceRead is InvoiceRead


async def test_two_independent_reads_agree(monkeypatch):
    calls = patch(monkeypatch, RAW, RAW)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is True and r.total == 990
    assert sorted(c["role"] for c in calls) == ["read", "verify"]
    images = {c["role"]: c["image"] for c in calls}
    assert images["read"] != images["verify"], "read B must use the enhanced image"
    prompts = {c["role"]: c["prompt"] for c in calls}
    assert prompts["read"] == invoice.PROMPT_A and prompts["verify"] == invoice.PROMPT_B
    assert all(c["schema"] == invoice.INVOICE_SCHEMA_HINT for c in calls)


async def test_extra_model_key_from_generate_json_is_ignored(monkeypatch):
    # The real generate_json adds "_model" to every reply; it must not affect the merge.
    patch(monkeypatch, dict(RAW, _model="read-model"), dict(RAW, _model="verify-model"))
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is True and r.disagreements == []


async def test_second_read_failure_never_agrees(monkeypatch):
    patch(monkeypatch, RAW, None, fail_b=True)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is False
    assert r.disagreements[0].startswith("second read failed (TimeoutError)")
    assert r.total == 990  # read A values still returned for read-back


async def test_first_read_failure_never_agrees(monkeypatch):
    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        if role == "read":
            raise RuntimeError("read down")
        return RAW

    monkeypatch.setattr(invoice, "generate_json", fake)
    monkeypatch.setattr(invoice, "image_part", lambda b64: b64)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is False
    assert r.disagreements[0].startswith("second read failed (RuntimeError)")
    assert r.total == 990  # read B values returned for read-back


async def test_both_reads_fail(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(invoice, "generate_json", boom)
    monkeypatch.setattr(invoice, "image_part", lambda b64: b64)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is False and r.lines == [] and r.total is None
    assert r.disagreements == ["could not read the bill: hold it flat and retake"]


async def test_no_frames():
    r = await invoice.read_invoice([])
    assert r.agreed is False
    assert r.disagreements == ["no image: hold the bill in front of the camera"]


async def test_uses_largest_frame(monkeypatch):
    calls = patch(monkeypatch, RAW, RAW)
    small, big = mk_frame(160, 100), mk_frame(640, 400)
    await invoice.read_invoice([small, big])
    read_a = next(c for c in calls if c["role"] == "read")
    assert read_a["image"] == big.jpeg_b64


async def test_reads_run_concurrently(monkeypatch):
    import asyncio

    started: list[str] = []
    both_started = asyncio.Event()

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        started.append(role)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1.0)  # a sequential caller would time out here
        return RAW

    monkeypatch.setattr(invoice, "generate_json", fake)
    monkeypatch.setattr(invoice, "image_part", lambda b64: b64)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is True
    assert sorted(started) == ["read", "verify"]


async def test_corrupt_image_never_raises(monkeypatch):
    calls = patch(monkeypatch, RAW, RAW)
    bad = Frame(id="f2", jpeg_b64=base64.b64encode(b"not a jpeg").decode(), w=320, h=200, ts=1.0, purpose="invoice")
    r = await invoice.read_invoice([bad])
    assert sorted(c["role"] for c in calls) == ["read", "verify"]
    assert r.total == 990  # enhancement failed, read B fell back to the original image


def test_invoice_module_imports_without_pillow():
    # The live service must still boot (tools import read_invoice) even if Pillow is missing.
    import os
    import subprocess
    import sys
    from pathlib import Path

    live_dir = Path(__file__).resolve().parents[2]
    code = (
        "import sys; sys.modules['PIL'] = None\n"
        "import aira_live.vision.invoice as m\n"
        "assert m.enhance_for_reading is None, m.enhance_for_reading\n"
    )
    env = {**os.environ, "PYTHONPATH": str(live_dir)}
    done = subprocess.run([sys.executable, "-c", code], cwd=live_dir, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


async def test_without_pillow_read_b_uses_original_image(monkeypatch):
    # If Pillow is not installed, invoice.py still imports and both reads still run (read B on the original).
    calls = patch(monkeypatch, RAW, RAW)
    monkeypatch.setattr(invoice, "enhance_for_reading", None)
    f = mk_frame()
    r = await invoice.read_invoice([f])
    assert r.agreed is True
    assert {c["role"]: c["image"] for c in calls} == {"read": f.jpeg_b64, "verify": f.jpeg_b64}


async def test_malformed_model_output_never_raises(monkeypatch):
    patch(monkeypatch, RAW, ["not", "a", "dict"])
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is False
    assert r.disagreements[0].startswith("second read failed (InvalidResponse)")


async def test_unparseable_lines_never_agree(monkeypatch):
    junk = dict(RAW, lines=["Sakthi Curd 30 L 990"])
    patch(monkeypatch, junk, junk)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is False
    assert r.lines == []
    assert "lines: none read" in r.disagreements


async def test_merge_crash_never_escapes(monkeypatch):
    patch(monkeypatch, RAW, RAW)

    def explode(a, b):
        raise ValueError("bad model output")

    monkeypatch.setattr(invoice, "merge_reads", explode)
    r = await invoice.read_invoice([mk_frame()])
    assert r.agreed is False and r.lines == [] and r.total is None
    assert r.disagreements == ["could not read the bill: hold it flat and retake"]
