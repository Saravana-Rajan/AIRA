"""vision.observers: the hook every ER 2 object result goes through (the visual memory subscribes later)."""
import pytest

from aira_live.vision import observers


@pytest.fixture(autouse=True)
def clean():
    saved = list(observers.OBSERVERS)
    observers.OBSERVERS.clear()
    yield
    observers.OBSERVERS[:] = saved


def test_nothing_registered_by_default_and_a_call_is_harmless():
    assert observers.OBSERVERS == []


async def test_no_observer_returns_zero():
    assert await observers.on_vision_objects("session", [{"label": "table"}], {"source": "x"}) == 0


async def test_register_calls_back_with_copies_and_unregister_stops_it():
    got = []

    async def cb(session, objects, context):
        got.append((session, objects, context))
        objects.append("mutated")
        context["mutated"] = True

    off = observers.register(cb)
    observers.register(cb)                                           # twice is once
    assert observers.OBSERVERS == [cb]
    objs = [{"label": "keys", "point": [500, 100], "distance": "near"}]
    ctx = {"source": "camera_ask", "frame_id": "f1"}
    assert await observers.on_vision_objects("s", objs, ctx) == 1
    assert got[0][0] == "s" and got[0][1][0] == objs[0] and got[0][2]["source"] == "camera_ask"
    assert objs == [{"label": "keys", "point": [500, 100], "distance": "near"}] and "mutated" not in ctx
    off()
    off()
    assert observers.OBSERVERS == []
    assert await observers.on_vision_objects("s", objs, ctx) == 0


async def test_a_failing_observer_never_breaks_the_caller_or_the_next_observer():
    seen = []

    async def bad(session, objects, context):
        raise RuntimeError("boom")

    async def good(session, objects, context):
        seen.append(context.get("source"))

    observers.register(bad)
    observers.register(good)
    assert await observers.on_vision_objects("s", [], {"source": "describe"}) == 1
    assert seen == ["describe"]
