import pytest

from app.config import Settings
from app.llm import BaseProvider, LLMResponse
from app.seed import build_database


class ScriptedProvider(BaseProvider):
    """Deterministic fake LLM for tests: returns queued responses in order."""
    name = "scripted"

    def __init__(self, responses):
        super().__init__("scripted-model", 1.0, 5.0, None)
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, tools):
        self.calls.append(messages)
        return self.responses.pop(0)


def tool_call(name, call_id="c1", **arguments):
    return LLMResponse(text=None, tool_calls=[{"id": call_id, "name": name, "arguments": arguments}],
                       input_tokens=100, output_tokens=20)


def text(reply):
    return LLMResponse(text=reply, input_tokens=120, output_tokens=30)


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.db_path = tmp_path / "shop.db"
    s.outputs_dir = tmp_path / "outputs"
    build_database(s.db_path)
    return s
