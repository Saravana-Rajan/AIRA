"""One notice, once: identical notices to a session are dropped within NOTICE_DEDUPE_S, a notice AIRA already
spoke is never replayed after a Live reconnect, and a resumed phone session keeps its sent-notice ledger."""
import asyncio

from live_fakes import FakeBackend, wait_until

from aira_live import live_bridge, session as session_mod, sessions
from aira_live.live_bridge import LiveBridge, LiveEvent
from aira_live.session import NOTICE_DEDUPE_S, Session


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(session_mod, "_monotonic", clock)
    monkeypatch.setattr(live_bridge, "_monotonic", clock)
    return clock


def _bridge(s, *backends):
    it = iter(backends)
    bridge = LiveBridge(s, lambda: next(it), "prompt", [])
    bridge.BACKOFF_BASE_S = 0
    return bridge


# ---------- Session ledger ----------
def test_session_ledger_marks_a_notice_as_repeat_within_the_window(mk_session, monkeypatch):
    clock = _clock(monkeypatch)
    s, _ = mk_session()
    assert NOTICE_DEDUPE_S == 10.0
    assert s.notice_is_repeat("Aavin vendor replied: coming at 7.") is False
    assert s.notice_is_repeat("  aavin vendor replied: coming at 7  ") is True  # same words, same notice
    assert s.notice_is_repeat("Low stock: Aavin milk") is False
    clock.t += NOTICE_DEDUPE_S + 0.1
    assert s.notice_is_repeat("Aavin vendor replied: coming at 7.") is False
    assert s.notice_is_repeat("") is True  # nothing to say is never spoken


# ---------- sessions.notify ----------
async def test_notify_drops_an_identical_notice_within_ten_seconds(mk_session, monkeypatch):
    clock = _clock(monkeypatch)
    s, rec = mk_session(shop="dd_shop")
    spoken = []

    class Bridge:
        async def speak_notice(self, text):
            spoken.append(text)

    sessions.register(s, Bridge())
    try:
        assert await sessions.notify("dd_shop", "Sakthi vendor confirmed: delivery at 6 pm.") == 1
        assert await sessions.notify("dd_shop", "Sakthi vendor confirmed: delivery at 6 pm.") == 0
        assert await sessions.notify("dd_shop", "Sakthi vendor confirmed: delivery at 6 pm") == 0
        assert len(rec.msgs) == 2 and spoken == ["Sakthi vendor confirmed: delivery at 6 pm."]
        assert await sessions.notify("dd_shop", "Low stock: Aavin milk", cue="warn") == 1
        clock.t += NOTICE_DEDUPE_S + 0.1
        assert await sessions.notify("dd_shop", "Sakthi vendor confirmed: delivery at 6 pm.") == 1
        assert len(spoken) == 3
    finally:
        sessions.unregister(s)


async def test_a_resumed_session_keeps_its_notice_ledger(mk_session, monkeypatch):
    _clock(monkeypatch)
    s1, r1 = mk_session(shop="rs_shop")
    sessions.register(s1)
    try:
        assert await sessions.notify("rs_shop", "Aavin vendor replied: coming at 7.") == 1
    finally:
        sessions.unregister(s1)
        sessions.remember_ended(s1.session_id, "h1")
    found, handle = sessions.take_ended(s1.session_id)
    assert found and handle == "h1"
    r2 = []
    s2 = Session("u1", "rs_shop", "en", "short", lambda m: _append(r2, m), session_id=s1.session_id)
    sessions.register(s2)
    try:
        assert await sessions.notify("rs_shop", "Aavin vendor replied: coming at 7.") == 0
        assert r2 == []
        assert await sessions.notify("rs_shop", "Sakthi vendor replied: tomorrow.") == 1
    finally:
        sessions.unregister(s2)


async def _append(lst, m):
    lst.append(m)


# ---------- LiveBridge.speak_notice ----------
async def test_speak_notice_sends_an_identical_notice_once_per_window(mk_session, monkeypatch):
    clock = _clock(monkeypatch)
    s, _ = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.speak_notice("Aavin vendor replied: coming at 7.")
    await bridge.speak_notice("Aavin vendor replied: coming at 7.")
    await bridge.speak_notice("aavin vendor replied: coming at 7")
    assert len(b.sent_text) == 1 and "coming at 7" in b.sent_text[0]
    await bridge.send_text("hello")  # typed text is never deduped
    await bridge.send_text("hello")
    assert len(b.sent_text) == 3
    clock.t += NOTICE_DEDUPE_S + 0.1
    await bridge.speak_notice("Aavin vendor replied: coming at 7.")
    assert len(b.sent_text) == 4
    await bridge.close()


async def test_a_notice_already_spoken_is_not_replayed_after_a_reconnect(mk_session, monkeypatch):
    _clock(monkeypatch)
    s, rec = mk_session()
    b1, b2 = FakeBackend(), FakeBackend()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    await bridge.speak_notice("Low stock: Aavin milk")
    assert len(b1.sent_text) == 1
    b1.push(LiveEvent("go_away"))
    await wait_until(lambda: b2.started_with)
    for _ in range(20):
        await asyncio.sleep(0)
    assert b2.sent_text == []
    await bridge.close()


async def test_a_notice_queued_while_reconnecting_is_dropped_once_stale(mk_session, monkeypatch):
    clock = _clock(monkeypatch)
    s, rec = mk_session()
    gate = asyncio.Event()

    class Slow(FakeBackend):
        async def start(self, *a):
            await gate.wait()
            await super().start(*a)

    b1, b2 = FakeBackend(), Slow()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    b1.push(LiveEvent("go_away"))
    await wait_until(lambda: any(m.t == "cue" and m.cue == "reconnect" for m in rec.msgs))
    await bridge.speak_notice("Aavin vendor replied: coming at 7.")
    await bridge.send_text("what is the stock?")  # the owner's own words are still delivered
    clock.t += NOTICE_DEDUPE_S + 0.1
    gate.set()
    await wait_until(lambda: b2.sent_text)
    for _ in range(20):
        await asyncio.sleep(0)
    assert b2.sent_text == ["what is the stock?"]
    await bridge.close()
