"""(c) The invoice two-read (``read`` + ``verify`` models, merged by code) on a synthetic rendered bill.

The bill is drawn with PIL text in the test: three lines, correct arithmetic, a printed total. Both reads
must agree on the total and the merge must find no arithmetic issue. Line-level disagreements (a unit one
read dropped, say) are printed, not asserted, because the frozen contract decides those elsewhere.
"""
from __future__ import annotations

from aira_live.vision.invoice import read_invoice


async def test_two_reads_agree_on_the_total(synth, lat):
    img = synth.draw_bill()
    frame = synth.to_frame(img, "invoice")
    with lat.measure("invoice.read_invoice (two reads in parallel)"):
        result = await read_invoice([frame])
    print(f"[invoice] supplier={result.supplier!r} date={result.date!r} total={result.total} agreed={result.agreed}")
    for ln in result.lines:
        print(f"[invoice]   {ln.name}: {ln.qty} {ln.unit} x {ln.unit_price} = {ln.line_total}")
    for d in result.disagreements:
        print(f"[invoice]   disagreement: {d}")

    expected_total = synth.bill_total()
    assert result.total == expected_total, f"read total {result.total}, bill says {expected_total}"
    total_dis = [d for d in result.disagreements if d.startswith("total:")]
    assert not total_dis, f"the two reads disagree on the total: {total_dis}"
    arith = [d for d in result.disagreements if d.startswith("arith:")]
    assert not arith, f"arithmetic issues on a bill whose sums are correct by construction: {arith}"
    assert len(result.lines) == len(synth.BILL_LINES), f"expected {len(synth.BILL_LINES)} lines, read {len(result.lines)}"
    assert sum(ln.line_total for ln in result.lines) == expected_total
    assert "second read failed" not in " ".join(result.disagreements), "one of the two reads failed"
