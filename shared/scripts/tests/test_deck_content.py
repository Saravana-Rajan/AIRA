"""Keyword and honesty guard for the 16-slide deck text and its two diagrams.

The AI pre-screen reads the deck PDF text, so a slide or a rubric phrase lost while
editing costs points. The build overrides (shared/docs/superpowers/plans/2026-10-10-00-build-overrides.md)
removed several services, and some spec ideas are not built yet. The deck may name those only on
the roadmap slide (13), never as part of the prototype.
"""
import re
from pathlib import Path

# One rule list for the markdown (here) and the exported PDF (shared/scripts/check_deck_pdf.py).
from check_deck_pdf import (
    BANNED_WORDS,
    COUNTERFEIT_CLAIMS,
    NOT_IN_PROTOTYPE,
    ROADMAP_SLIDE,
    RUBRIC,
    project_id_from_env,
)

ROOT = Path(__file__).resolve().parents[3]
SUBMISSION = ROOT / "shared" / "docs" / "submission"
DECK = SUBMISSION / "deck-content.md"
DIAGRAMS = SUBMISSION / "diagrams"
ARCHITECTURE = DIAGRAMS / "architecture.mmd"
PROCESS_FLOW = DIAGRAMS / "process-flow.mmd"

FINAL_MODELS = ["gemini-3.8-live", "gemini-robotics-er-2-preview", "gemini-3.8-flash", "gemini-3.6-flash"]
RETIRED_MODELS = [
    "gemini-robotics-er-1.6", "gemini-3.8-live-extended-thinking", "gemini-3.1-pro",
    "gemini-2.5-computer-use",
]

SLIDE_RE = re.compile(r"^## Slide (\d+):", flags=re.MULTILINE)
H2_RE = re.compile(r"^## ", flags=re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"<fill: [^>]+>")


def deck_text() -> str:
    return DECK.read_text(encoding="utf-8")


def slides() -> dict[int, str]:
    """Slide number -> its text. A slide ends at the next level-2 heading (slide or not)."""
    text = deck_text()
    heads = [m.start() for m in H2_RE.finditer(text)]
    out: dict[int, str] = {}
    for m in SLIDE_RE.finditer(text):
        end = next((h for h in heads if h > m.start()), len(text))
        out[int(m.group(1))] = text[m.start():end]
    return out


def all_files() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in (DECK, ARCHITECTURE, PROCESS_FLOW)}


def test_deck_has_16_slides():
    numbers = [int(n) for n in SLIDE_RE.findall(deck_text())]
    assert numbers == list(range(1, 17))


def test_every_slide_has_content():
    empty = [n for n, body in slides().items() if len(body.splitlines()) < 2 or len(body.split()) < 12]
    assert empty == []


def test_deck_covers_rubric_keywords():
    text = deck_text().lower()
    missing = [k for k in RUBRIC if k.lower() not in text]
    assert missing == []


def test_deck_has_no_dropped_features():
    text = deck_text().lower()
    for banned in COUNTERFEIT_CLAIMS:
        assert banned not in text


def test_unbuilt_services_only_on_roadmap_slide():
    offenders = []
    for number, body in slides().items():
        if number == ROADMAP_SLIDE:
            continue
        offenders += [f"slide {number}: {s}" for s in NOT_IN_PROTOTYPE if s.lower() in body.lower()]
    files = all_files()
    for name in (ARCHITECTURE.name, PROCESS_FLOW.name):
        offenders += [f"{name}: {s}" for s in NOT_IN_PROTOTYPE if s.lower() in files[name].lower()]
    assert offenders == []


def test_deck_names_final_models_only():
    text = deck_text()
    assert [m for m in FINAL_MODELS if m not in text] == []
    assert [m for m in RETIRED_MODELS if m in text] == []


def test_deck_models_match_models_py():
    models_py = (ROOT / "backend" / "aira_live" / "models.py").read_text(encoding="utf-8")
    assert [m for m in FINAL_MODELS if f'"{m}"' not in models_py] == []


def test_diagrams_are_mermaid_flowcharts():
    for path in (ARCHITECTURE, PROCESS_FLOW):
        first = path.read_text(encoding="utf-8").lstrip().splitlines()[0]
        assert first.startswith("flowchart "), path.name


def test_architecture_names_mandatory_services():
    text = ARCHITECTURE.read_text(encoding="utf-8")
    for term in ("Cloud Run", "Firestore", "Firebase Hosting", "Gemini", "aira-live", "database aira"):
        assert term in text, term


def test_process_flow_covers_owner_loop():
    text = PROCESS_FLOW.read_text(encoding="utf-8").lower()
    for step in ("order", "delivery", "invoice", "pay vendor", "shelf", "sale", "upi", "low stock", "yes"):
        assert step in text, step


def test_deck_references_existing_diagrams():
    text = deck_text()
    for path in (ARCHITECTURE, PROCESS_FLOW):
        assert path.exists(), path
        assert f"shared/docs/submission/diagrams/{path.name}" in text


def test_placeholders_name_their_source():
    """Numbers and links not known yet are explicit <fill: ...> markers that say where they come from."""
    fills = [f for f in PLACEHOLDER_RE.findall(deck_text()) if f != "<fill: ...>"]  # the intro's example
    assert fills, "expected <fill: ...> markers for unmeasured numbers and links"
    sources = ("cost_per_sale.py", "bench-table.md", "links.json", "user_study.csv", "data-sheet.csv", "phone model")
    unsourced = [f for f in fills if not any(s in f for s in sources)]
    assert unsourced == []


def test_slide_12_never_states_a_number_before_it_is_measured():
    body = slides()[12]
    # Any percentage, latency or rupee amount on the benchmark slide must come from bench-table.md.
    assert not re.search(r"\d+(\.\d+)?\s*(%|ms\b|₹)|₹\s*\d", body), "benchmark slide states an unmeasured number"


def test_slide_10_costs_come_from_cost_per_sale():
    """Every rupee figure on the cost slide must be printed by cost_per_sale.py run with the slide's own command."""
    import subprocess
    import sys
    from pathlib import Path

    body = slides()[10]
    if not re.search(r"₹\s*\d|\d\s*₹|\$\s*\d", body):
        return  # still a placeholder
    m = re.search(r"cost_per_sale\.py --rate (\d+(?:\.\d+)?) --sales-per-day (\d+) --deliveries-per-day (\d+)", body)
    assert m, "cost slide shows numbers but not the exact cost_per_sale.py command that produced them"
    script = Path(__file__).resolve().parents[1] / "cost_per_sale.py"
    out = subprocess.run(
        [sys.executable, str(script), "--rate", m.group(1), "--sales-per-day", m.group(2),
         "--deliveries-per-day", m.group(3)],
        capture_output=True, text=True, check=True,
    ).stdout
    printed = {s.replace(",", "") for s in re.findall(r"₹([\d,]+(?:\.\d+)?)", out)}
    for amount in re.findall(r"₹([\d,]+(?:\.\d+)?)", body):
        assert amount.replace(",", "") in printed, f"₹{amount} on the cost slide is not printed by cost_per_sale.py"
    assert "$" not in body


def test_no_project_id_secrets_or_personal_contacts():
    project = project_id_from_env()
    for name, body in all_files().items():
        if project:
            assert project not in body, f"{name} contains the GCP project id"
        assert not re.search(r"AIza[0-9A-Za-z_\-]{20,}", body), f"{name} contains an API key"
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", body), f"{name} contains an email address"
        assert not re.search(r"\+91[\s-]?\d{5}", body), f"{name} contains a phone number"
        assert ".iam.gserviceaccount.com" not in body, f"{name} contains a service-account address"


def test_no_banned_words_or_ai_attribution():
    for name, body in all_files().items():
        lowered = body.lower()
        for word in BANNED_WORDS:
            assert word not in lowered, f"{name}: {word}"
        for marker in ("co-authored-by", "generated with claude", "claude-session", "claude code"):
            assert marker not in lowered, f"{name}: {marker}"


def test_rendered_diagrams_if_present_are_small_pngs():
    for path in (ARCHITECTURE, PROCESS_FLOW):
        png = path.with_suffix(".png")
        if not png.exists():
            continue
        data = png.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", png.name
        assert len(data) < 1_000_000, f"{png.name} is {len(data)} bytes"
