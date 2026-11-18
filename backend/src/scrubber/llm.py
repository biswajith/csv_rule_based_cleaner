"""LLM access through LangChain so providers can be swapped by config (spec §8).

`LLM_MODEL` is "<provider>:<model>". Everything is synchronous; the cleaner uses a thread
pool for concurrency, so usage accounting is lock-guarded.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
Messages = list[tuple[str, str]]


class LLMError(RuntimeError):
    pass


class LLMUsage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.failed_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add_call(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """One model invocation (repairs count too)."""
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens

    def add_failed(self) -> None:
        """One complete_json call that ended in LLMError."""
        with self._lock:
            self.failed_calls += 1

    def as_dict(self) -> dict[str, int]:
        return {"calls": self.calls, "failed_calls": self.failed_calls,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class LLM(Protocol):
    model_name: str
    usage: LLMUsage

    def complete_json(self, messages: Messages, schema: type[T]) -> tuple[T, str]:
        """Returns (parsed model, raw text of the final answer)."""
        ...


_FENCE = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> str:
    text = text.strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def _parse(text: str, schema: type[T]) -> T:
    return schema.model_validate(json.loads(extract_json(text)))


REPAIR_MESSAGE = ("That response was not valid. Error:\n{error}\n"
                  "Return only the corrected JSON object.")


def _complete(invoke: Callable[[Messages], str], messages: Messages, schema: type[T],
              usage: LLMUsage) -> tuple[T, str]:
    """Shared parse -> validate -> one repair round -> LLMError logic."""
    try:
        text = invoke(messages)
        try:
            return _parse(text, schema), text
        except (json.JSONDecodeError, ValidationError) as e:
            repair = messages + [("ai", text), ("human", REPAIR_MESSAGE.format(error=e))]
            text = invoke(repair)
            try:
                return _parse(text, schema), text
            except (json.JSONDecodeError, ValidationError) as e2:
                raise LLMError(f"LLM returned invalid JSON twice: {e2}") from e2
    except LLMError:
        usage.add_failed()
        raise
    except Exception as e:  # transport errors after the client's own retries
        usage.add_failed()
        raise LLMError(f"{type(e).__name__}: {e}") from e


class LangChainLLM:
    """JSON-in/JSON-out over a LangChain chat model."""

    def __init__(self, model: str):
        from .config import get_settings

        s = get_settings()
        self.model_name = model
        self.usage = LLMUsage()
        try:
            from langchain.chat_models import init_chat_model

            chat = init_chat_model(model, timeout=s.llm_timeout, max_retries=s.llm_max_retries,
                                   **s.llm_model_kwargs)
            if model.split(":", 1)[0] == "openai":
                chat = chat.bind(response_format={"type": "json_object"})
        except Exception as e:  # missing API key, unknown provider, missing package
            raise LLMError(f"could not initialise LLM {model!r}: {e}") from e
        self._chat: Any = chat

    def _invoke(self, messages: Messages) -> str:
        msg = self._chat.invoke(messages)
        meta = getattr(msg, "usage_metadata", None) or {}
        self.usage.add_call(int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0)))
        content = msg.content
        if isinstance(content, list):  # content blocks
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return str(content)

    def complete_json(self, messages: Messages, schema: type[T]) -> tuple[T, str]:
        return _complete(self._invoke, messages, schema, self.usage)


class FakeLLM:
    """Test double. `responses`: list of dict/str (consumed in order) or a callable
    `(messages, schema) -> dict | str`. An Exception instance in the list (or returned by
    the callable) is raised."""

    model_name = "fake"

    def __init__(self, responses: list[Any] | Callable[[Messages, type], Any]):
        self._responses = responses
        self._lock = threading.Lock()
        self.usage = LLMUsage()
        self.calls: list[tuple[Messages, str]] = []

    def _next(self, messages: Messages, schema: type) -> str:
        with self._lock:
            self.calls.append((list(messages), schema.__name__))
            if callable(self._responses):
                r = self._responses(messages, schema)
            else:
                if not self._responses:
                    raise AssertionError("FakeLLM ran out of responses")
                r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        self.usage.add_call(10, 5)
        return r if isinstance(r, str) else json.dumps(r)

    def complete_json(self, messages: Messages, schema: type[T]) -> tuple[T, str]:
        return _complete(lambda msgs: self._next(msgs, schema), messages, schema, self.usage)


def make_llm(*, planner: bool = False) -> LangChainLLM:
    from .config import get_settings

    s = get_settings()
    return LangChainLLM(s.planner_model if planner and s.planner_model else s.llm_model)
