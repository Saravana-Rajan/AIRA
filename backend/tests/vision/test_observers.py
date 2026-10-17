"""vision.observers: tools that analyse a frame hand their objects to subscribers (the visual memory, describe
mode); nothing is registered by default and a bad subscriber is harmless."""
import pytest

from aira_live.vision import observers


@pytest.fixture(autouse=True)
def clean():
    saved = list(observers.OBSERVERS)
    observers.OBSERVERS.clear()
    yield
    observers.OBSERVERS[:] = saved


def test_nothing_registered_by_default_and_a_call_is_harmless():
    assert observers.OBSERVERS == [] and observers.observers() == ()


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
    assert observers.OBSERVERS == [cb] and observers.observers() == (cb,)
    objs = [{"label": "keys", "point": [500, 100], "distance": "near"}]
    ctx = {"source": "camera_ask", "frame_id": "f1"}
    assert await observers.on_vision_objects("s", objs, ctx) == 1
    assert got[0][0] == "s" and got[0][1][0] == objs[0] and got[0][2]["source"] == "camera_ask"
    assert got[0][2]["purpose"] == "ask"
    assert objs == [{"label": "keys", "point": [500, 100], "distance": "near"}] and "mutated" not in ctx
    off()
    off()
    assert observers.OBSERVERS == []
    assert await observers.on_vision_objects("s", objs, ctx) == 0


async def test_sync_observers_module_unregister_and_fan_out_order():
    seen = []

    async def a(session, objects, context):
        seen.append(("a", objects, context["purpose"]))

    def b(session, objects, context):
        seen.append(("b", objects, context["purpose"]))

    observers.register(a)
    observers.register(a)
    observers.register(b)
    assert observers.observers() == (a, b)
    assert await observers.on_vision_objects("sess", [{"label": "keys"}], {"purpose": "ask"}) == 2
    assert seen == [("a", [{"label": "keys"}], "ask"), ("b", [{"label": "keys"}], "ask")]
    observers.unregister(a)
    observers.unregister(a)
    assert observers.observers() == (b,)


async def test_a_string_context_names_the_source_and_purpose():
    got = []

    async def cb(session, objects, context):
        got.append(context)

    observers.register(cb)
    await observers.on_vision_objects("s", [{"label": "water bottle"}], "product_find")
    await observers.on_vision_objects("s", [{"label": "cup"}], None)
    assert got[0] == {"source": "product_find", "purpose": "find"}
    assert got[1] == {}


async def test_a_failing_observer_never_breaks_the_caller_or_the_next_observer():
    seen = []

    async def bad(session, objects, context):
        raise RuntimeError("boom")

    async def good(session, objects, context):
        seen.append((context.get("source"), objects))

    observers.register(bad)
    observers.register(good)
    assert await observers.on_vision_objects("s", [], {"source": "describe"}) == 1   # empty results still reach them
    assert seen == [("describe", [])]
    assert await observers.on_vision_objects("sess", [{"label": "bag"}]) == 1
    assert seen[1] == (None, [{"label": "bag"}])
