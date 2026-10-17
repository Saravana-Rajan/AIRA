"""Scene facts: a short-lived, session-scoped memory of what AIRA last counted, with its age, its evidence and
whether the camera moved since. Never served as a fresh count."""
import pytest

from aira_live import scene_facts
from aira_live.scene_facts import SceneFacts


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(scene_facts, "_monotonic", c)
    return c


def _record(sf, item="milk cartons", count=3, verified=True, **kw):
    return sf.record(item=item, count=count, approx=not verified, verified=verified,
                     source=kw.pop("source", "fresh_count"), observed_at=kw.pop("observed_at", 5000.0),
                     evidence=kw.pop("evidence", {"frames": 2, "reads": 2, "agreed": verified}), **kw)


def test_a_recorded_fact_is_recalled_with_its_age_and_status(clock):
    sf = SceneFacts()
    sf.camera_moved(10.0)
    _record(sf)
    clock.t += 7
    out = sf.recall()
    assert out["found"] is True and len(out["facts"]) == 1
    f = out["facts"][0]
    assert f["item"] == "milk cartons" and f["count"] == 3 and f["verified"] is True
    assert f["age_s"] == 7 and f["camera_moved_since"] is False and f["current"] is True
    assert f["scene"] == 10.0 and f["observed_at"] == 5000.0 and f["source"] == "fresh_count"
    assert f["scope"] == "visible_then" and f["fresh"] is False
    assert "7 seconds ago" in out["say_en"] and "count again" in out["say_en"].lower()


def test_a_camera_change_invalidates_the_fact(clock):
    sf = SceneFacts()
    sf.camera_moved(10.0)
    _record(sf)
    sf.camera_moved(20.0)
    f = sf.recall()["facts"][0]
    assert f["camera_moved_since"] is True and f["current"] is False
    assert sf.previous("milk cartons")["camera_moved_since"] is True
    assert "moved" in sf.recall()["say_en"]


def test_an_older_scene_id_never_rolls_the_scene_back(clock):
    sf = SceneFacts()
    sf.camera_moved(20.0)
    sf.camera_moved(10.0)
    assert sf.scene == 20.0


def test_a_fact_expires(clock):
    sf = SceneFacts()
    _record(sf)
    clock.t += scene_facts.EXPIRY_S + 1
    out = sf.recall()
    assert out["found"] is False and out["facts"] == [] and sf.previous("milk cartons") is None


def test_a_newer_observation_of_the_same_item_supersedes_the_old_one(clock):
    sf = SceneFacts()
    _record(sf, count=3)
    clock.t += 2
    _record(sf, item="Milk Cartons", count=4, verified=False)
    facts = sf.recall()["facts"]
    assert len(facts) == 1 and facts[0]["count"] == 4 and facts[0]["verified"] is False


def test_recall_filters_by_item_and_an_unverified_fact_says_so(clock):
    sf = SceneFacts()
    _record(sf, item="milk cartons")
    _record(sf, item="packs", count=9, verified=False, source="background_precount")
    out = sf.recall("packs")
    assert [f["item"] for f in out["facts"]] == ["packs"]
    assert "not double-checked" in out["say_en"]
    assert sf.recall("bread")["found"] is False


def test_the_session_has_scene_facts(mk_session):
    s, _ = mk_session()
    assert isinstance(s.scene_facts, SceneFacts) and s.scene_facts is s.scene_facts


@pytest.mark.parametrize("video,precount_on", [(False, False), (True, False)])
async def test_a_changed_live_frame_moves_the_scene_of_the_facts(mk_session, video, precount_on):
    """The changed-frame hook (main.live_frame) invalidates the facts even with the pre-count or video off."""
    from aira_live import main
    from aira_live.frames import Frame

    s, _ = mk_session()
    _record(s.scene_facts)
    frame = Frame(id="l1", jpeg_b64="x", w=768, h=432, ts=42.0, purpose="live")
    await main.live_frame(s, None, frame, video=video, changed=False, precount_on=precount_on)
    assert s.scene_facts.scene == 0.0 and s.scene_facts.previous("milk cartons")["current"] is True
    await main.live_frame(s, None, frame, video=video, changed=True, precount_on=precount_on)
    assert s.scene_facts.scene == 42.0
    assert s.scene_facts.previous("milk cartons")["camera_moved_since"] is True


async def test_scene_recall_tool_reads_the_session_facts(mk_session, clock):
    from aira_live.session import current_session
    from aira_live.tools import TOOLS_BY_NAME, ER_TOOLS, active_tools
    from aira_live.tools.camera import scene_recall

    assert TOOLS_BY_NAME["scene_recall"] is scene_recall
    assert "scene_recall" not in ER_TOOLS and scene_recall in active_tools(er2=False)
    s, _ = mk_session()
    _record(s.scene_facts)
    token = current_session.set(s)
    try:
        out = await scene_recall()
        none = await scene_recall("bread")
    finally:
        current_session.reset(token)
    assert out["found"] is True and out["facts"][0]["count"] == 3 and out["facts"][0]["fresh"] is False
    assert none["found"] is False and none["say_en"]
