"""Guard for the exported deck PDF (saravana/03 Task 6).

The AI pre-screen reads the PDF text, not deck-content.md, so the same rules are checked again on
the exported file: 16 pages, every rubric phrase, no leftover editing notes, nothing claimed that the
prototype does not have.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

import check_deck_pdf as d

KEYWORD_PAGE = (
    "Generative AI with Gemini on a Google Cloud architecture: Cloud Run, Firestore and Firebase. "
    "Scalable, sustainable, measurable impact. Original, accessible user experience. "
    "A functional prototype."
)


def good_pages() -> list[str]:
    pages = [f"Slide {n} of the AIRA deck for Sunrise Dairy, with enough words to read" for n in range(1, 17)]
    pages[2] = KEYWORD_PAGE
    return pages


def test_good_deck_passes():
    errors, warnings = d.check_pages(good_pages())
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize("count", [15, 17])
def test_page_count_must_be_16(count):
    pages = good_pages()
    pages = pages[:count] if count < 16 else pages + ["An extra slide that breaks the template"]
    errors, _ = d.check_pages(pages)
    assert any("16" in e and str(count) in e for e in errors)


def test_missing_rubric_keyword_fails():
    pages = good_pages()
    pages[2] = KEYWORD_PAGE.replace("Firestore", "the database")
    errors, _ = d.check_pages(pages)
    assert any("Firestore" in e for e in errors)


def test_keywords_survive_line_breaks_and_odd_spaces():
    pages = good_pages()
    pages[2] = KEYWORD_PAGE.replace("Google Cloud architecture", "Google Cloud\narchitecture").replace(
        "user experience", "user experience"
    )
    errors, _ = d.check_pages(pages)
    assert errors == []


def test_unbuilt_service_fails_outside_roadmap_slide():
    pages = good_pages()
    pages[7] += " Events flow through Pub/Sub into BigQuery."
    errors, _ = d.check_pages(pages)
    assert any("page 8" in e and "Pub/Sub" in e for e in errors)
    assert any("page 8" in e and "BigQuery" in e for e in errors)


def test_unbuilt_service_allowed_on_roadmap_slide():
    pages = good_pages()
    pages[d.ROADMAP_SLIDE - 1] += " Later: sales history in BigQuery."
    errors, _ = d.check_pages(pages)
    assert errors == []


@pytest.mark.parametrize(
    "leftover", ["<fill: cost_per_sale.py line 1>", "Insert shared/docs/submission/diagrams/architecture.png", "python3 shared/scripts/bench_table.py"]
)
def test_leftover_editing_notes_fail(leftover):
    pages = good_pages()
    pages[9] += " " + leftover
    errors, _ = d.check_pages(pages)
    assert any("page 10" in e for e in errors)


@pytest.mark.parametrize(
    "text", ["AIRA does counterfeit detection", "udhaar book", "Kaggle notebook", "AIRA Business", "Generated with Claude"]
)
def test_banned_claims_and_words_fail(text):
    pages = good_pages()
    pages[4] += " " + text
    errors, _ = d.check_pages(pages)
    assert errors


def test_project_id_and_api_key_fail():
    pages = good_pages()
    pages[13] += " console project shop-demo-123456"
    errors, _ = d.check_pages(pages, project_id="shop-demo-123456")
    assert any("project" in e.lower() for e in errors)
    pages = good_pages()
    pages[13] += " key AIzaSyA1234567890abcdefghijklmnop"
    errors, _ = d.check_pages(pages)
    assert any("API key" in e for e in errors)


def test_image_only_and_pending_pages_warn():
    pages = good_pages()
    pages[5] = ""
    pages[11] += " count_accuracy pending"
    errors, warnings = d.check_pages(pages)
    assert errors == []
    assert any("page 6" in w for w in warnings)
    assert any("page 12" in w and "pending" in w for w in warnings)


def test_split_pages_keeps_empty_pages_and_drops_trailing_feed():
    assert d.split_pages("a\fb\f") == ["a", "b"]
    assert d.split_pages("a\f\f") == ["a", ""]
    assert d.split_pages("a") == ["a"]


def test_extract_pages_runs_pdftotext():
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="one\ftwo\f", stderr="")

    assert d.extract_pages(Path("deck.pdf"), run=fake_run) == ["one", "two"]
    assert calls[0][0] == "pdftotext"
    assert calls[0][-2:] == ["deck.pdf", "-"]


def test_extract_pages_raises_on_pdftotext_error():
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Syntax Error: broken")

    with pytest.raises(RuntimeError, match="broken"):
        d.extract_pages(Path("deck.pdf"), run=fake_run)


def test_main_without_pdftotext_returns_2(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(d.shutil, "which", lambda name: None)
    assert d.main([str(pdf)]) == 2
    assert "pdftotext" in capsys.readouterr().out


def test_main_missing_file_returns_2(tmp_path, capsys):
    assert d.main([str(tmp_path / "nope.pdf")]) == 2
    assert "not found" in capsys.readouterr().out


def test_rules_match_the_markdown_guard():
    """One source of truth: the markdown guard uses the same lists."""
    import test_deck_content as md

    assert md.RUBRIC is d.RUBRIC
    assert md.NOT_IN_PROTOTYPE is d.NOT_IN_PROTOTYPE
    assert md.ROADMAP_SLIDE == d.ROADMAP_SLIDE


# --- end to end on a real PDF (needs pdftotext from poppler) -------------------------------------

def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, pages: list[str]) -> None:
    """Minimal text-only PDF, one string per page (ASCII only)."""
    objs: list[bytes] = []
    n = len(pages)
    page_ids = [4 + 2 * i for i in range(n)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        lines = [text[j:j + 80] for j in range(0, len(text), 80)] or [""]
        body = "BT /F1 10 Tf 12 TL 40 760 Td " + " ".join(f"({_pdf_escape(line)}) Tj T*" for line in lines) + " ET"
        stream = body.encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_ids[i] + 1} 0 R >>".encode()
        )
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


needs_pdftotext = pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext (poppler) not installed")


@needs_pdftotext
def test_end_to_end_good_pdf(tmp_path, capsys):
    pdf = tmp_path / "AIRA-deck.pdf"
    write_pdf(pdf, good_pages())
    assert d.main([str(pdf), "--project-id", ""]) == 0
    assert "OK" in capsys.readouterr().out


@needs_pdftotext
def test_end_to_end_short_pdf_with_leftover_fails(tmp_path, capsys):
    pdf = tmp_path / "AIRA-deck.pdf"
    pages = good_pages()[:15]
    pages[9] += " <fill: cost_per_sale.py line 1>"
    write_pdf(pdf, pages)
    assert d.main([str(pdf), "--project-id", ""]) == 1
    out = capsys.readouterr().out
    assert "15 pages" in out
    assert "page 10" in out
