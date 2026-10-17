import json

from pr_triage.classifier import engine
from pr_triage.classifier.engine import (
    OpenRouterClassifier,
    classification_schema,
    list_openrouter_models,
)
from pr_triage.models import ClassificationDecision, Verdict


class Immediate:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        if False:
            yield None
        return self.value


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as complete:
        return complete.value
    raise AssertionError("Mocked coroutine unexpectedly suspended.")


def test_openrouter_classifier_sends_strict_schema_and_parses_result(monkeypatch, evidence_packet):
    captured = {}
    decision = ClassificationDecision(
        verdict=Verdict.SUBSTANTIVE,
        confidence=0.92,
        reasoning="The PR adds a focused regression test.",
        signals_cited=["tests.tests_changed"],
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": decision.model_dump_json()}}]}

    class Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        def __aenter__(self):
            return Immediate(self)

        def __aexit__(self, *args):
            return Immediate(None)

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Immediate(Response())

    monkeypatch.setattr(engine.httpx, "AsyncClient", Client)
    result = run_immediate(
        OpenRouterClassifier("secret-key", "meta-llama/test-model").classify(evidence_packet)
    )

    assert result.verdict is Verdict.SUBSTANTIVE
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in json.dumps(captured["json"])
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["json"]["provider"]["require_parameters"] is True


def test_classification_schema_requires_every_property():
    schema = classification_schema()["schema"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert schema["properties"]["verdict"]["enum"] == [item.value for item in Verdict]


def test_model_discovery_uses_structured_text_filter(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "z/model", "name": "Zulu"},
                    {"id": "a/model", "name": "Alpha"},
                ]
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        def __aenter__(self):
            return Immediate(self)

        def __aexit__(self, *args):
            return Immediate(None)

        def get(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Immediate(Response())

    monkeypatch.setattr(engine.httpx, "AsyncClient", Client)
    result = run_immediate(list_openrouter_models("secret-key"))

    assert [model["id"] for model in result] == ["a/model", "z/model"]
    assert captured["params"] == {
        "output_modalities": "text",
        "supported_parameters": "structured_outputs",
    }
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
