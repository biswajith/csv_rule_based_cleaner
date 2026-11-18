from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from scrubber import llm as llm_mod
from scrubber.config import get_settings
from scrubber.llm import FakeLLM, LangChainLLM, LLMError, extract_json, make_llm


class Out(BaseModel):
    x: int


MSGS = [("system", "s"), ("human", "h")]


def test_extract_json_fences():
    assert extract_json('```json\n{"a":1}\n```') == '{"a":1}'
    assert extract_json('```\n{"a":1}\n```') == '{"a":1}'
    assert extract_json('  {"a":1} ') == '{"a":1}'


def test_fake_returns_parsed_and_raw_and_records():
    f = FakeLLM([{"x": 1}])
    out, raw = f.complete_json(MSGS, Out)
    assert out.x == 1 and json.loads(raw) == {"x": 1}
    assert f.calls == [(MSGS, "Out")] and f.usage.calls == 1 and f.model_name == "fake"


def test_fake_callable_and_exceptions():
    f = FakeLLM(lambda m, s: {"x": len(m)})
    assert f.complete_json(MSGS, Out)[0].x == 2
    f = FakeLLM([RuntimeError("boom")])
    with pytest.raises(LLMError, match="boom"):
        f.complete_json(MSGS, Out)
    assert f.usage.failed_calls == 1


def test_one_repair_round_on_invalid_json_then_schema_error():
    f = FakeLLM(["not json", {"x": 2}])
    out, raw = f.complete_json(MSGS, Out)
    assert out.x == 2 and f.usage.calls == 2 and f.usage.failed_calls == 0
    repair = f.calls[1][0]
    assert repair[-2] == ("ai", "not json") and "not valid" in repair[-1][1]
    f = FakeLLM([{"x": "nope"}, {"x": 3}])
    assert f.complete_json(MSGS, Out)[0].x == 3


def test_second_failure_raises_and_counts_failed_call():
    f = FakeLLM(["bad", "still bad"])
    with pytest.raises(LLMError, match="invalid JSON twice"):
        f.complete_json(MSGS, Out)
    assert f.usage.failed_calls == 1 and f.usage.calls == 2


class _Msg:
    def __init__(self, content, meta=None):
        self.content, self.usage_metadata = content, meta


class StubChat:
    def __init__(self):
        self.bound = None
        self.replies = [_Msg([{"type": "text", "text": '{"x": 7}'}], {"input_tokens": 11, "output_tokens": 4})]
        self.invoked = []

    def bind(self, **kw):
        self.bound = kw
        return self

    def invoke(self, messages):
        self.invoked.append(messages)
        return self.replies.pop(0)


def test_langchain_binds_json_mode_and_never_passes_temperature(monkeypatch):
    seen = {}
    stub = StubChat()

    def fake_init(model, **kw):
        seen["model"], seen["kw"] = model, kw
        return stub

    import langchain.chat_models as cm
    monkeypatch.setattr(cm, "init_chat_model", fake_init)
    monkeypatch.setenv("LLM_MODEL_KWARGS", '{"reasoning_effort": "low"}')
    get_settings.cache_clear()
    llm = LangChainLLM("openai:gpt-5-mini")
    assert stub.bound == {"response_format": {"type": "json_object"}}
    assert seen["model"] == "openai:gpt-5-mini"
    assert seen["kw"] == {"timeout": 180, "max_retries": 2, "reasoning_effort": "low"}
    for banned in ("temperature", "top_p", "max_tokens", "max_completion_tokens"):
        assert banned not in seen["kw"]
    out, _ = llm.complete_json(MSGS, Out)
    assert out.x == 7  # content blocks joined
    assert (llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens) == (1, 11, 4)


def test_langchain_no_json_mode_for_other_providers(monkeypatch):
    stub = StubChat()
    import langchain.chat_models as cm
    monkeypatch.setattr(cm, "init_chat_model", lambda model, **kw: stub)
    LangChainLLM("anthropic:some-model")
    assert stub.bound is None


def test_langchain_construction_error_is_llm_error(monkeypatch):
    import langchain.chat_models as cm

    def boom(model, **kw):
        raise ValueError("missing key")

    monkeypatch.setattr(cm, "init_chat_model", boom)
    with pytest.raises(LLMError, match="missing key"):
        LangChainLLM("openai:gpt-5-mini")


def test_langchain_transport_error_counts_failed(monkeypatch):
    stub = StubChat()
    stub.replies = []  # invoke raises IndexError
    import langchain.chat_models as cm
    monkeypatch.setattr(cm, "init_chat_model", lambda model, **kw: stub)
    llm = LangChainLLM("openai:x")
    with pytest.raises(LLMError):
        llm.complete_json(MSGS, Out)
    assert llm.usage.failed_calls == 1


def test_make_llm_picks_planner_model(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_mod, "LangChainLLM", lambda m: seen.append(m) or object())
    monkeypatch.setenv("LLM_MODEL", "openai:a")
    monkeypatch.setenv("PLANNER_MODEL", "openai:b")
    get_settings.cache_clear()
    make_llm()
    make_llm(planner=True)
    monkeypatch.delenv("PLANNER_MODEL")
    get_settings.cache_clear()
    make_llm(planner=True)  # default planner model
    monkeypatch.setenv("PLANNER_MODEL", "")
    get_settings.cache_clear()
    make_llm(planner=True)  # empty: same model as the cleaner
    get_settings.cache_clear()
    assert seen == ["openai:a", "openai:b", "openai:gpt-6-luna", "openai:a"]


def test_missing_api_key_surfaces_as_llm_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(LLMError):
        LangChainLLM("openai:gpt-5-mini")
