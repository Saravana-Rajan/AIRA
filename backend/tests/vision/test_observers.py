"""vision.observers: tools that analyse a frame hand their objects to subscribers; a bad subscriber is harmless."""
import pytest

from aira_live.vision import observers


@pytest.fixture
def clean():
    before = observers.observers()
    for cb in before:
        observers.unregister(cb)
    yield
    for cb in observers.observers():
        observers.unregister(cb)
    for cb in before:
        observers.register(cb)


async def test_register_once_and_fan_out(clean):
    seen = []

    async def a(session, objects, context):
        seen.append(("a", objects, context))

    def b(session, objects, context):
        seen.append(("b", objects, context))

    observers.register(a)
    observers.register(a)
    observers.register(b)
    assert observers.observers() == (a, b)
    await observers.on_vision_objects("sess", [{"label": "keys"}], {"purpose": "ask"})
    assert seen == [("a", [{"label": "keys"}], {"purpose": "ask"}), ("b", [{"label": "keys"}], {"purpose": "ask"})]


async def test_empty_objects_and_failing_observer(clean):
    seen = []

    async def bad(session, objects, context):
        raise RuntimeError("boom")

    async def good(session, objects, context):
        seen.append(objects)

    observers.register(bad)
    observers.register(good)
    await observers.on_vision_objects("sess", [], {"purpose": "ask"})
    assert seen == []
    await observers.on_vision_objects("sess", [{"label": "bag"}])
    assert seen == [[{"label": "bag"}]]
    observers.unregister(bad)
    assert observers.observers() == (good,)
