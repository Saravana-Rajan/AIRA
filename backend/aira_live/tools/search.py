"""Live tool web_search: Google Search grounding for current facts. OWNER: Saravana.

Why a function tool and not the Live built-in ``google_search``: google-adk 2.11 states that the Gemini API
does not allow a built-in search tool to be combined with function declarations in one agent; its own
``bypass_multi_tools_limit`` workaround wraps search in a sub-agent that would run on the *live* model id
through generateContent. So the Live model calls this ordinary function, which grounds a quick
``generate_content`` on ``model_for("read")`` with ``types.Tool(google_search=...)`` and returns a short answer
plus the source titles. The answer travels through the tool result, so the turn-end number check allows its
numbers like any other tool output.
"""
from __future__ import annotations

import asyncio
import logging

from google.genai import types

from aira_live.i18n import t
from aira_live.models import model_for
from aira_live.session import current_session
from aira_live.vision.gemini import client

log = logging.getLogger(__name__)

TIMEOUT_S = 12.0
MAX_SOURCES = 5

_INSTRUCTION = (
    "Use Google Search and answer the question below in at most three short sentences, in the same language as "
    "the question. Give concrete current facts (dates, numbers, names) when the search finds them. If the search "
    "does not settle it, say so briefly. No markdown, no lists.\n\nQuestion: "
)


def _lang() -> str:
    s = current_session.get(None)
    return getattr(s, "lang", "en") or "en"


def _sources(resp) -> list[str]:
    out: list[str] = []
    for cand in getattr(resp, "candidates", None) or []:
        gm = getattr(cand, "grounding_metadata", None)
        for chunk in (getattr(gm, "grounding_chunks", None) or []) if gm is not None else []:
            web = getattr(chunk, "web", None)
            title = (getattr(web, "title", None) or getattr(web, "uri", None) or "") if web is not None else ""
            title = str(title).strip()
            if title and title not in out:
                out.append(title)
            if len(out) >= MAX_SOURCES:
                return out
    return out


async def web_search(query: str) -> dict:
    """Search Google for a current or real-time fact (news, weather, prices elsewhere, dates, sports, anything
    that may have changed recently) and get a short grounded answer with its sources.

    Use it whenever the owner or a customer asks about the world outside the shop and the answer could be out
    of date. Say the 'say' text in the speaker's language; name a source only if asked.
    """
    query = (query or "").strip()
    lang = _lang()
    if not query:
        return {"ok": False, "answer": "", "sources": [], "say": t("search.empty", lang)}
    cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.2)
    try:
        resp = await asyncio.wait_for(
            client().aio.models.generate_content(model=model_for("read"), contents=[_INSTRUCTION + query],
                                                 config=cfg),
            timeout=TIMEOUT_S,
        )
        answer = (getattr(resp, "text", None) or "").strip()
    except Exception as exc:
        log.warning("web_search failed: %s", exc)
        return {"ok": False, "answer": "", "sources": [], "say": t("search.unavailable", lang)}
    if not answer:
        return {"ok": False, "answer": "", "sources": [], "say": t("search.unavailable", lang)}
    return {"ok": True, "answer": answer, "sources": _sources(resp), "say": answer}
