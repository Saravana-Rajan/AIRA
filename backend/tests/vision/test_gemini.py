import pytest

from aira_live.models import fallback_for, model_for
from aira_live.vision.gemini import ToolReply, VisionError, extract_json, generate_json, image_part


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! [{"point": [1, 2]}] done.') == [{"point": [1, 2]}]
    assert extract_json('x {"s": "a } b"} y') == {"s": "a } b"}
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json("")
    with pytest.raises(ValueError):
        extract_json(None)


async def test_generate_json_wraps_list_and_reports_model(fake_genai):
    client = fake_genai('[{"point": [10, 20]}]')
    out = await generate_json("count", ["prompt"], "list", thinking="high")
    assert out["items"] == [{"point": [10, 20]}]
    assert out["_model"] == model_for("count")
    assert client.aio.models.calls[0]["model"] == model_for("count")
    assert client.aio.models.calls[0]["config"] is not None


async def test_config_is_json_mode_with_thinking_level(fake_genai):
    client = fake_genai('{"ok": 1}')
    await generate_json("count", ["prompt"], "{ok}", thinking="high")
    cfg = client.aio.models.calls[0]["config"]
    assert cfg.response_mime_type == "application/json"
    assert str(cfg.thinking_config.thinking_level.value).lower() == "high"


async def test_retry_once_then_fallback(fake_genai):
    client = fake_genai("not json", RuntimeError("503"), '{"ok": true}')
    out = await generate_json("point", ["prompt"], "{ok}")
    assert out["ok"] is True
    assert [c["model"] for c in client.aio.models.calls] == [
        model_for("point"), model_for("point"), fallback_for("point")]


async def test_timeout_falls_back(fake_genai):
    fake_genai(0.5, 0.5, '{"ok": 1}')
    out = await generate_json("count", ["prompt"], "{ok}", timeout_s=0.05)
    assert out["_model"] == fallback_for("count")


async def test_all_fail_raises(fake_genai):
    fake_genai("x", "y", "z")
    with pytest.raises(VisionError):
        await generate_json("count", ["prompt"], "{}")


async def test_no_fallback_role_tries_primary_twice(fake_genai):
    client = fake_genai("bad", "still bad")
    with pytest.raises(VisionError):
        await generate_json("read", ["prompt"], "{}")
    assert [c["model"] for c in client.aio.models.calls] == [model_for("read")] * 2


async def test_env_override_picks_the_model(fake_genai, monkeypatch):
    client = fake_genai('{"ok": 1}')
    monkeypatch.setenv("AIRA_MODEL_POINT", fallback_for("point"))
    out = await generate_json("point", ["prompt"], "{ok}")
    assert out["_model"] == fallback_for("point")
    assert client.aio.models.calls[0]["model"] == fallback_for("point")


async def test_scalar_json_reply_is_rejected(fake_genai):
    fake_genai("42", '"text"', '{"ok": 1}')
    out = await generate_json("count", ["prompt"], "{ok}")
    assert out == {"ok": 1, "_model": fallback_for("count")}


async def test_prompt_ends_with_schema_hint(fake_genai):
    client = fake_genai('{"a": 1}')
    await generate_json("point", ["describe", "img"], "{a:int}")
    contents = client.aio.models.calls[0]["contents"]
    assert contents[:2] == ["describe", "img"]
    assert "{a:int}" in contents[-1]


def test_image_part_decodes_base64(frame):
    part = image_part(frame.jpeg_b64)
    assert part.inline_data.mime_type == "image/jpeg"
    assert part.inline_data.data.startswith(b"\xff\xd8")


def test_toolreply_defaults():
    assert ToolReply(say_first="hi").to_dict() == {
        "say_first": "hi", "detail": "", "confidence": 0.0, "needs_retake": False, "aim_hint": ""}


# --- the count call: real JSON schema, big output cap, no Flash fallback, truncation is "not sure" --------------


def test_config_leaves_temperature_at_the_model_default():
    from aira_live.vision import gemini

    assert gemini._config("low").temperature is None


async def test_count_call_uses_a_json_schema_and_a_big_output_cap(fake_genai):
    from aira_live.vision import gemini

    client = fake_genai('{"items": [{"point": [10, 20], "label": "curd"}]}')
    out = await gemini.generate_count_json(["prompt"], timeout_s=1.0)
    assert out["items"] == [{"point": [10, 20], "label": "curd"}] and out["_model"] == model_for("count")
    cfg = client.aio.models.calls[0]["config"]
    assert cfg.max_output_tokens == gemini.COUNT_MAX_OUTPUT_TOKENS >= 16384
    assert cfg.response_json_schema == gemini.COUNT_JSON_SCHEMA
    assert cfg.temperature is None


async def test_count_call_retries_er_once_and_never_falls_back_to_flash(fake_genai):
    from aira_live.vision import gemini

    client = fake_genai("x", "y", "z")
    with pytest.raises(VisionError):
        await gemini.generate_count_json(["prompt"], timeout_s=1.0)
    assert [c["model"] for c in client.aio.models.calls] == [model_for("count")] * 2


async def test_count_call_never_salvages_a_truncated_reply(fake_genai):
    from aira_live.vision import gemini

    fake_genai('{"items": [{"point": [10, 20], "label": "a"}, {"point": [3')
    with pytest.raises(gemini.TruncatedReply):
        await gemini.generate_count_json(["prompt"], timeout_s=1.0)


async def test_count_call_at_max_tokens_is_truncated(fake_genai):
    from types import SimpleNamespace

    from aira_live.vision import gemini

    client = fake_genai('{"items": []}')

    async def capped(model, contents, config=None):
        return SimpleNamespace(text='{"items": []}', candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")])

    client.aio.models.generate_content = capped
    with pytest.raises(gemini.TruncatedReply):
        await gemini.generate_count_json(["prompt"], timeout_s=1.0)
