import subprocess

import check_submission as cs

GOOD = "AIRA runs on Gemini, Cloud Run, Firestore and Firebase."


def test_description_ok():
    assert cs.check_description(GOOD) == []


def test_description_too_long():
    errs = cs.check_description(GOOD + " x" * 600)
    assert any("max 1024" in e for e in errs)


def test_description_missing_term():
    errs = cs.check_description("AIRA uses Gemini on Cloud Run with Firebase.")
    assert errs == ["description must mention Firestore"]


def test_cloud_run_across_line_break():
    assert cs.check_description("Gemini, Firebase, Firestore and Cloud\nRun.") == []


def test_js_length_counts_emoji_as_two():
    assert cs.js_length("₹") == 1
    assert cs.js_length("🙂") == 2


def test_description_rejects_todo():
    assert any("TODO" in e for e in cs.check_description(GOOD + " TODO"))


def test_links_ok_and_failures():
    responses = {
        "https://ok.example": (200, "https://ok.example"),
        "https://down.example": (503, "https://down.example"),
        "https://drive.example/v": (200, "https://accounts.google.com/ServiceLogin?continue=x"),
    }
    errs = cs.check_links(
        {
            "app": "https://ok.example",
            "api": "https://down.example",
            "video": "https://drive.example/v",
            "repo": "github.com/x",
        },
        fetch=lambda u: responses[u],
    )
    assert len(errs) == 3
    assert any("HTTP 503" in e for e in errs)
    assert any("login page" in e for e in errs)
    assert any("must start with https://" in e for e in errs)


def test_pdf_size_boundary(tmp_path):
    small = tmp_path / "ok.pdf"
    small.write_bytes(b"%PDF-1.7\n" + b"0" * 100)
    assert cs.check_pdf(small) == []
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.7\n" + b"0" * cs.MAX_PDF_BYTES)
    assert any("must be under" in e for e in cs.check_pdf(big))


def test_pdf_header(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"PK\x03\x04 not a pdf")
    assert cs.check_pdf(f) == ["file is not a PDF (missing %PDF- header)"]


class FakeRun:
    def __init__(self, seconds: float):
        self.seconds = seconds

    def __call__(self, cmd, **kwargs):
        assert cmd[0] == "ffprobe"
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.seconds}\n", stderr="")


def test_video_limits(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"\x00")
    errors, warnings = cs.check_video(v, 240, 179, run=FakeRun(230.0))
    assert errors == [] and len(warnings) == 1
    errors, _ = cs.check_video(v, 240, 179, run=FakeRun(240.4))
    assert errors == ["video is 240.4s (max 240s)"]
    assert cs.check_video(v, 240, 179, run=FakeRun(175.0)) == ([], [])


def test_video_without_ffprobe(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"\x00")

    def boom(cmd, **kwargs):
        raise FileNotFoundError("ffprobe")

    errors, _ = cs.check_video(v, 240, 179, run=boom)
    assert errors and "ffprobe" in errors[0]


def test_main_exit_codes(tmp_path):
    d = tmp_path / "d.txt"
    d.write_text(GOOD, encoding="utf-8")
    assert cs.main(["--description", str(d)]) == 0
    d.write_text("too short, no services", encoding="utf-8")
    assert cs.main(["--description", str(d)]) == 1


def test_links_reject_plain_http():
    errs = cs.check_links({"app": "http://plain.example"}, fetch=lambda u: (200, u))
    assert errs == ["app: URL must start with https:// (http://plain.example)"]


def test_main_missing_description_fails_cleanly(tmp_path, capsys):
    assert cs.main(["--description", str(tmp_path / "missing.txt")]) == 1
    assert "description not found" in capsys.readouterr().out
