"""tools/_speak: one session, or the shop owner's sessions, hear a line; judges never hear the shop's business."""
from aira_live import sessions
from aira_live.contracts import CueMsg, TranscriptMsg
from aira_live.session import Session
from aira_live.tools import _speak

SHOP = "sunrise_dairy"


class _Phone:
    def __init__(self):
        self.msgs: list = []

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)

    def said(self) -> list[str]:
        return [m.text for m in self.msgs if isinstance(m, TranscriptMsg)]


def _session(uid: str) -> tuple[Session, _Phone]:
    phone = _Phone()
    s = Session(uid, SHOP, "en", "short", phone)
    sessions.register(s)
    return s, phone


def _cleanup(*ss: Session) -> None:
    for s in ss:
        sessions.unregister(s)


async def test_speak_to_reaches_only_that_session():
    a, pa = _session("judge-aaa")
    b, pb = _session("owner1")
    try:
        assert await _speak.speak_to(a, "Give back 30 rupees.", "warn") is True
    finally:
        _cleanup(a, b)
    assert pa.said() == ["Give back 30 rupees."] and [m.cue for m in pa.msgs if isinstance(m, CueMsg)] == ["warn"]
    assert pb.msgs == []


async def test_speak_to_a_session_that_left_is_false_not_an_error():
    async def gone(msg):
        raise ConnectionError("closed")

    s = Session("owner1", SHOP, "en", "short", gone)
    assert await _speak.speak_to(s, "hello") is False


async def test_vendor_replies_reach_the_owner_and_never_judges():
    owner, po = _session("owner1")
    j1, pj1 = _session("judge-111")
    j2, pj2 = _session("judge-222")
    try:
        n = await _speak.speak_to_owners(SHOP, "Sakthi curd vendor replied: delivery at 6 pm.")
    finally:
        _cleanup(owner, j1, j2)
    assert n == 1 and po.said() == ["Sakthi curd vendor replied: delivery at 6 pm."]
    assert pj1.msgs == [] and pj2.msgs == []


async def test_the_owner_hears_a_repeated_line_once():
    owner, po = _session("owner1")
    try:
        await _speak.speak_to_owners(SHOP, "Same line.")
        second = await _speak.speak_to_owners(SHOP, "Same line.")
    finally:
        _cleanup(owner)
    assert second == 0 and po.said() == ["Same line."]


def test_judges_are_told_apart_by_the_judge_page_uid():
    assert _speak.is_judge(Session("judge-k3x9", SHOP, "en", "short", _Phone()))
    assert not _speak.is_judge(Session("owner-7f2a", SHOP, "en", "short", _Phone()))
