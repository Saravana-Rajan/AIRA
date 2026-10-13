"""Request/frame correlation: request ids, stale and late frames, duplicates, capture-age freshness."""
import asyncio

from aira_live import frames as frames_mod
from aira_live import session as session_mod
from aira_live.contracts import RequestFrameMsg
from aira_live.frames import Frame, FrameStore
from aira_live.session import Session


def fr(i, purpose="count", rid=None, ts=10_000.0, capture_ts=None, w=640):
    return Frame(id=f"f{i}", jpeg_b64="x", w=w, h=w, ts=ts, purpose=purpose, request_id=rid, capture_ts=capture_ts)


def mk_session(reply=None):
    """A session whose phone answers each request with ``reply(msg)`` frames (after a tick), echoing nothing
    by itself: the reply decides what the 'phone' sends."""
    sent: list = []
    holder: dict = {}

    async def send(m):
        sent.append(m)
        if reply is not None and isinstance(m, RequestFrameMsg):
            async def later():
                await asyncio.sleep(0.01)
                for f in reply(m):
                    holder["s"].frames.add(f)
            asyncio.get_running_loop().create_task(later())

    s = Session(uid="u1", shop_id="sunrise_dairy", lang="en", verbosity="short", send=send)
    holder["s"] = s
    return s, sent


def capture_diag(monkeypatch):
    logged: list[dict] = []
    sink = lambda event, **kw: logged.append({"event": event, **kw})  # noqa: E731
    monkeypatch.setattr(session_mod, "diag", sink)
    monkeypatch.setattr(frames_mod, "diag", sink)
    return logged


async def test_request_carries_a_unique_id_and_a_ttl():
    s, sent = mk_session()
    await s.request_frames("ask", timeout_s=0.02)
    await s.request_frames("ask", timeout_s=0.02)
    assert all(isinstance(m.request_id, str) and m.request_id for m in sent)
    assert sent[0].request_id != sent[1].request_id
    assert sent[0].ttl_ms == 20


async def test_phone_echoing_the_id_satisfies_its_request():
    n = iter(range(100))
    s, sent = mk_session(lambda m: [fr(next(n), m.purpose, m.request_id) for _ in range(m.burst.count if m.burst else 1)])
    got = await s.request_frames("count", count=3, timeout_s=1)
    assert len(got) == 3 and {f.request_id for f in got} == {sent[0].request_id}
    assert s.frames.open_requests() == 0


async def test_a_fresh_request_never_takes_a_late_frame_of_a_timed_out_one(monkeypatch):
    logged = capture_diag(monkeypatch)
    s, sent = mk_session()
    assert await s.request_frames("count", timeout_s=0.02) == []
    old_rid = sent[0].request_id

    async def phone():
        await asyncio.sleep(0.01)
        assert s.frames.add(fr(1, "count", old_rid)) == "stale"  # the first request's frame, too late
        await asyncio.sleep(0.02)
        s.frames.add(fr(2, "count", sent[1].request_id))

    task = asyncio.create_task(phone())
    got = await s.request_frames("count", timeout_s=1)
    await task
    assert [f.id for f in got] == ["f2"]
    stale = [e for e in logged if e["event"] == "stale_frame"]
    assert stale and stale[0]["reason"] == "expired" and stale[0]["request_id"] == old_rid
    assert "jpeg" not in stale[0] and "jpeg_b64" not in stale[0]
    late = [e for e in logged if e["event"] == "late_frame"]
    assert len(late) == 1 and late[0]["request_id"] == old_rid and late[0]["got_in_time"] == 0


async def test_a_frame_for_an_unknown_request_is_ignored_but_kept_for_fresh_reuse(monkeypatch):
    logged = capture_diag(monkeypatch)
    store = FrameStore()
    assert store.add(fr(1, "ask", "nope")) == "stale"
    assert store.latest(5) == []  # never in the tool buffer
    assert store.recent(1.0).id == "f1"  # reusable while fresh
    assert [e["reason"] for e in logged if e["event"] == "stale_frame"] == ["unknown"]


async def test_concurrent_same_purpose_requests_get_their_own_frames():
    n = iter(range(100))
    s, sent = mk_session(lambda m: [fr(next(n), m.purpose, m.request_id)])
    a, b = await asyncio.gather(s.request_frames("count", timeout_s=1), s.request_frames("count", timeout_s=1))
    assert len(a) == 1 and len(b) == 1 and a[0].id != b[0].id
    assert {a[0].request_id, b[0].request_id} == {m.request_id for m in sent}


async def test_concurrent_requests_on_an_old_phone_never_share_a_frame():
    n = iter(range(100))
    s, sent = mk_session(lambda m: [fr(next(n), m.purpose)])  # no request id: an old phone
    a, b = await asyncio.gather(s.request_frames("count", timeout_s=1), s.request_frames("count", timeout_s=1))
    assert len(a) == 1 and len(b) == 1 and a[0].id != b[0].id


async def test_old_phone_without_request_ids_still_works_and_old_frames_do_not_count():
    s, sent = mk_session(lambda m: [fr(9, m.purpose, ts=500.0)])  # phone clock far behind: no ts comparison
    s.frames.add(fr(1, "cash", ts=1_000.0))  # stored before the request: must not satisfy it
    got = await s.request_frames("cash", timeout_s=1)
    assert [f.id for f in got] == ["f9"]


async def test_untagged_continuous_frames_are_not_claimed_once_the_phone_tags():
    store = FrameStore()
    store.add(fr(1, "identify", "someone-else"))  # the phone echoes ids: it is a current phone
    rid = store.open_request("identify", 1)
    assert store.add(fr(2, "identify")) == "stored"  # a continuous identify frame
    assert await store.wait_request(rid, 0.02) == []
    store.close_request(rid)


async def test_a_frame_with_capture_ts_marks_a_current_phone():
    store = FrameStore()
    rid = store.open_request("guide", 1)
    assert store.add(fr(1, "guide", capture_ts=9_990.0)) == "stored"  # continuous guide frame, untagged
    assert store.latest(1, "guide")[0].id == "f1"  # still there for the guidance loop
    assert await store.wait_request(rid, 0.02) == []
    store.close_request(rid)


def test_arrival_time_is_recorded_for_every_kept_frame():
    store = FrameStore()
    a, b, c = fr(1, "walk"), fr(2, "ask", "nope"), fr(3, "count")
    store.add(a)
    store.add(b)  # stale (unknown request): still kept for fresh reuse, so its arrival is known
    assert store.arrival_monotonic(a) is not None and store.arrival_monotonic(b) is not None
    assert store.arrival_monotonic(c) is None  # never arrived
    assert store.arrival_monotonic(fr(1, "walk")) is None  # an equal but different object


async def test_duplicate_frames_count_once():
    store = FrameStore()
    rid = store.open_request("count", 2)
    f = fr(1, "count", rid)
    assert store.add(f) == "request"
    assert store.add(f) == "duplicate"
    assert await store.wait_request(rid, 0.02) == []  # one real frame, not two
    store.add(fr(2, "count", rid, ts=10_001.0))
    assert [x.id for x in await store.wait_request(rid, 0.5)] == ["f1", "f2"]
    assert len(store.latest(10)) == 2


async def test_out_of_order_burst_frames_come_back_in_capture_order():
    store = FrameStore()
    rid = store.open_request("count", 3)
    store.add(fr(3, "count", rid, ts=10_300.0, capture_ts=10_250.0))
    store.add(fr(1, "count", rid, ts=10_100.0, capture_ts=10_050.0))
    store.add(fr(2, "count", rid, ts=10_200.0, capture_ts=10_150.0))
    assert [f.id for f in await store.wait_request(rid, 0.5)] == ["f1", "f2", "f3"]


async def test_a_frame_too_old_for_its_purpose_is_refused(monkeypatch):
    logged = capture_diag(monkeypatch)
    s, sent = mk_session(lambda m: [fr(1, m.purpose, m.request_id, ts=20_000.0, capture_ts=17_000.0)])  # 3 s
    assert await s.request_frames("ask", timeout_s=0.1) == []  # ask allows 2 s
    assert any(e["event"] == "stale_frame" and e["reason"] == "too_old" for e in logged)
    s2, _ = mk_session(lambda m: [fr(1, m.purpose, m.request_id, ts=20_000.0, capture_ts=17_500.0)])  # 2.5 s
    assert len(await s2.request_frames("count", timeout_s=1)) == 1  # count allows 3 s
    assert frames_mod.max_capture_age_ms("walk") == 1500 and frames_mod.max_capture_age_ms("invoice") == 5000


async def test_look_frame_reuse_counts_the_phones_own_capture_age():
    s, sent = mk_session()
    s.frames.add(fr(1, "ask", ts=20_000.0, capture_ts=19_100.0))  # grabbed 0.9 s before it was sent
    assert [f.id for f in await s.look_frame("ask", timeout_s=0.02)] == ["f1"] and sent == []
    s2, sent2 = mk_session()
    s2.frames.add(fr(2, "ask", ts=20_000.0, capture_ts=18_500.0))  # 1.5 s old already: no reuse
    assert await s2.look_frame("ask", timeout_s=0.02) == [] and len(sent2) == 1


async def test_expiring_requests_never_drops_an_open_requests_frames(monkeypatch):
    store = FrameStore()
    keep = store.open_request("count", 2)
    store.add(fr(0, "count", keep))
    for i in range(frames_mod.MAX_EXPIRED + 10):  # many short requests come and go
        rid = store.open_request("count", 1)
        store.close_request(rid)
    real = frames_mod.time.monotonic
    monkeypatch.setattr(frames_mod.time, "monotonic", lambda: real() + frames_mod.EXPIRED_KEEP_S + 1)
    store.add(fr(1, "count", "gone"))  # prunes every expired id
    store.add(fr(2, "count", keep, ts=10_001.0))
    assert [f.id for f in await store.wait_request(keep, 0.5)] == ["f0", "f2"]


async def test_a_cancelled_request_is_closed():
    s, _ = mk_session()
    task = asyncio.create_task(s.request_frames("count", timeout_s=5))
    await asyncio.sleep(0.01)
    assert s.frames.open_requests() == 1
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert s.frames.open_requests() == 0


async def test_partial_returns_only_this_requests_own_frames_on_timeout():
    s, sent = mk_session(lambda m: [fr(1, m.purpose, m.request_id)])  # the burst wanted 2; only 1 arrives
    s.frames.add(fr(7, "count"))  # an untagged frame (e.g. another reader's) must never be handed over
    got = await s.request_frames("count", count=2, timeout_s=0.1, partial=True)
    assert [f.id for f in got] == ["f1"] and got[0].request_id == sent[0].request_id
    assert await s.request_frames("count", count=2, timeout_s=0.05) == []  # default: all-or-nothing as before
