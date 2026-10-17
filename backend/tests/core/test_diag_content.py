"""User content in diagnostic logs is reduced to its length unless AIRA_DIAG_CONTENT=1."""
import json

from aira_live.diag import diag


def _last(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_content_hidden_by_default(capsys, monkeypatch):
    monkeypatch.delenv("AIRA_DIAG", raising=False)
    monkeypatch.delenv("AIRA_DIAG_CONTENT", raising=False)
    diag("transcript", text="order 20 litres milk", lang="en")
    rec = _last(capsys)
    assert rec["text"] == "<20 chars>" and rec["lang"] == "en"


def test_content_shown_when_enabled(capsys, monkeypatch):
    monkeypatch.delenv("AIRA_DIAG", raising=False)
    monkeypatch.setenv("AIRA_DIAG_CONTENT", "1")
    diag("transcript", text="order 20 litres milk")
    assert _last(capsys)["text"] == "order 20 litres milk"
