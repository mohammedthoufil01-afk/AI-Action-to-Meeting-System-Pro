"""GeminiService tests. The SDK client is replaced by stubs, so no API quota is used."""
import json
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.services import meeting_analysis_service as analysis
from app.services.gemini_service import (
    AIConfigurationError,
    AIResponseError,
    AIServiceError,
    GeminiService,
    parse_json_object,
)


class StubModels:
    def __init__(self, owner):
        self.owner = owner

    def generate_content(self, **kwargs):
        self.owner.requests.append(kwargs)
        outcome = self.owner.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class StubFiles:
    def __init__(self, owner):
        self.owner = owner

    def upload(self, file, config):
        self.owner.uploads.append((file, config))
        return self.owner.uploaded

    def get(self, name):
        self.owner.polls += 1
        return self.owner.after_poll

    def delete(self, name):
        self.owner.deleted.append(name)


class StubInteractions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        self.owner.interaction_requests.append(kwargs)
        return SimpleNamespace(output_text=self.owner.interaction_text)


class StubClient:
    def __init__(self, outcomes=(), uploaded=None, after_poll=None):
        self.outcomes = list(outcomes)
        self.requests, self.uploads, self.deleted, self.interaction_requests = [], [], [], []
        self.polls = 0
        self.uploaded = uploaded
        self.after_poll = after_poll
        self.interaction_text = "interaction transcript"
        self.models, self.files, self.interactions = StubModels(self), StubFiles(self), StubInteractions(self)


def response(text="hello", finish="STOP", block=None):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=finish)],
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
    )


def service(client, **overrides):
    options = dict(api_key="key", text_model="text-model", audio_model="audio-model", client_factory=lambda timeout: client, sleep=lambda s: None)
    options.update(overrides)
    return GeminiService(**options)


def client_error(code, message="boom"):
    return genai_errors.ClientError(code, {"error": {"code": code, "message": message, "status": "X"}})


# ---------------------------------------------------------------- configuration
def test_from_config_reads_models_and_key(app):
    svc = GeminiService.from_config(app.config)
    assert (svc.api_key, svc.text_model, svc.audio_model) == ("test-key", "test-text-model", "test-audio-model")


def test_missing_api_key_gives_configuration_error():
    with pytest.raises(AIConfigurationError, match="GEMINI_API_KEY"):
        service(StubClient(), api_key="").generate_json("p", {})


def test_missing_model_names_give_configuration_errors():
    with pytest.raises(AIConfigurationError, match="GEMINI_TEXT_MODEL"):
        service(StubClient(), text_model="").generate_json("p", {})
    with pytest.raises(AIConfigurationError, match="GEMINI_AUDIO_MODEL"):
        service(StubClient(), audio_model="").transcribe_audio("x.wav", "audio/wav")


def test_real_sdk_client_is_built_with_key_and_timeout(monkeypatch):
    svc = GeminiService("abc", "m", "m", timeout_seconds=7)
    client = svc._client(7)
    assert client._api_client.api_key == "abc"
    assert client._api_client._http_options.timeout == 7000


def test_default_models_are_not_hardcoded_in_code_paths(app):
    app.config["GEMINI_TEXT_MODEL"] = "custom-model-x"
    svc = GeminiService.from_config(app.config)
    stub = StubClient([response("{}")])
    svc._client_factory = lambda t: stub
    svc.generate_json("p", {"type": "object"})
    assert stub.requests[0]["model"] == "custom-model-x"


# ---------------------------------------------------------------- structured JSON
def test_generate_json_sends_schema_and_returns_dict():
    stub = StubClient([response('{"summary": "ok"}')])
    result = service(stub).generate_json("prompt text", {"type": "object"}, "be careful")
    assert result == {"summary": "ok"}
    request = stub.requests[0]
    assert request["model"] == "text-model" and request["contents"] == "prompt text"
    config = request["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == {"type": "object"}
    assert config.system_instruction == "be careful"


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} hope it helps', ' \n{"a": 1}\n '],
)
def test_parse_json_object_tolerates_wrappers(raw):
    assert parse_json_object(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["", "   ", "not json", "[1, 2]", "42", '{"a": ', None])
def test_parse_json_object_rejects_bad_output(raw):
    with pytest.raises(AIResponseError):
        parse_json_object(raw)


def test_malformed_json_from_gemini_becomes_ai_response_error():
    with pytest.raises(AIResponseError, match="unreadable"):
        service(StubClient([response("definitely not json")])).generate_json("p", {})


def test_blocked_prompt_and_empty_and_truncated_responses():
    with pytest.raises(AIResponseError, match="declined"):
        service(StubClient([response("x", block="SAFETY")])).generate_json("p", {})
    with pytest.raises(AIResponseError, match="empty"):
        service(StubClient([response(None)])).generate_json("p", {})
    with pytest.raises(AIResponseError, match="cut off"):
        service(StubClient([response("{", finish="FinishReason.MAX_TOKENS")])).generate_json("p", {})


# ---------------------------------------------------------------- meeting analysis
def test_analysis_schema_matches_required_output_shape():
    schema = analysis.ANALYSIS_SCHEMA
    assert set(schema["required"]) == {"summary", "decisions", "topics", "action_items"}
    item = schema["properties"]["action_items"]["items"]
    assert set(item["required"]) == {"description", "assignee", "deadline"}


def test_analyze_meeting_normalizes_gemini_output(app, gemini):
    gemini.analysis = {
        "summary": "  Summary  ",
        "decisions": ["A"],
        "topics": ["T"],
        "action_items": [{"description": "Do it", "assignee": "", "deadline": "2031-01-02"}],
    }
    with app.app_context():
        result = analysis.analyze_meeting("Some transcript text that is long enough")
    assert result == {
        "summary": "Summary",
        "decisions": ["A"],
        "topics": ["T"],
        "action_items": [{"description": "Do it", "assignee": "Unassigned", "deadline": "2031-01-02"}],
    }


@pytest.mark.parametrize("bad", [None, [], "text", {}, {"summary": ""}, {"summary": 5}])
def test_normalize_analysis_rejects_unusable_data(bad):
    with pytest.raises(AIResponseError):
        analysis.normalize_analysis(bad)


def test_system_instruction_forbids_inventing_owners_and_deadlines():
    text = analysis.ANALYSIS_SYSTEM_INSTRUCTION
    assert "Never invent" in text and '"Unassigned"' in text and "empty string" in text


@pytest.mark.parametrize(
    "value,expected",
    [("2030-12-01", (2030, 12, 1)), ("01/12/2030", (2030, 12, 1)), ("01-12-2030", (2030, 12, 1)), ("", None), (None, None), ("tomorrow", None), ("2030-13-45", None), (20301201, None)],
)
def test_parse_deadline(value, expected):
    parsed = analysis.parse_deadline(value)
    assert (None if parsed is None else (parsed.year, parsed.month, parsed.day)) == expected


def test_overlong_fields_are_truncated_to_database_limits():
    result = analysis.normalize_analysis(
        {"summary": "s", "decisions": [], "topics": [], "action_items": [{"description": "d" * 2000, "assignee": "a" * 500, "deadline": ""}]}
    )
    item = result["action_items"][0]
    assert len(item["description"]) == 700 and len(item["assignee"]) == 150


# ---------------------------------------------------------------- chat
def test_chat_reply_builds_roles_and_system_instruction():
    stub = StubClient([response("The answer")])
    reply = service(stub).generate_chat_reply("SYSTEM", [("user", "q1"), ("assistant", "a1")], "q2")
    assert reply == "The answer"
    request = stub.requests[0]
    roles = [c.role for c in request["contents"]]
    assert roles == ["user", "model", "user"]
    assert request["contents"][-1].parts[0].text == "q2"
    assert request["config"].system_instruction == "SYSTEM"
    assert request["config"].response_mime_type is None


# ---------------------------------------------------------------- error translation
@pytest.mark.parametrize(
    "exc,status,needle",
    [
        (client_error(429), 429, "quota"),
        (client_error(403), 503, "API key"),
        (client_error(400, "API key not valid. Please pass a valid API key."), 503, "API key"),
        (client_error(404), 503, "model was not found"),
        (client_error(413), 413, "too large"),
        (client_error(400, "bad request"), 502, "could not process"),
        (genai_errors.ServerError(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}}), 503, "temporarily unavailable"),
        (TimeoutError("slow"), 504, "timed out"),
        (ConnectionError("down"), 503, "Could not reach"),
        (RuntimeError("surprise"), 502, "unexpected"),
    ],
)
def test_sdk_errors_become_friendly_service_errors(exc, status, needle):
    with pytest.raises(AIServiceError) as caught:
        service(StubClient([exc])).generate_json("p", {})
    assert caught.value.status_code == status and needle in caught.value.user_message
    assert "key=" not in caught.value.user_message.lower()


def test_timeout_class_names_from_http_libraries_are_recognised():
    class ReadTimeout(Exception):
        pass

    assert GeminiService.translate_error(ReadTimeout()).status_code == 504


# ---------------------------------------------------------------- audio
def make_audio(tmp_path, size=100, name="a.wav"):
    path = tmp_path / name
    path.write_bytes(b"R" * size)
    return str(path)


def test_small_audio_is_sent_inline_with_transcription_prompt(tmp_path):
    stub = StubClient([response("Speaker 1: hello")])
    text = service(stub).transcribe_audio(make_audio(tmp_path), "audio/wav")
    assert text == "Speaker 1: hello"
    contents = stub.requests[0]["contents"]
    assert "Transcribe this meeting" in contents[0]
    assert contents[1].inline_data.mime_type == "audio/wav" and contents[1].inline_data.data == b"R" * 100
    assert stub.uploads == [] and stub.deleted == []


def test_large_audio_uses_files_api_waits_and_cleans_up(tmp_path):
    uploaded = SimpleNamespace(name="files/abc", uri="u", mime_type="audio/wav", state=SimpleNamespace(name="PROCESSING"))
    ready = SimpleNamespace(name="files/abc", uri="u", mime_type="audio/wav", state=SimpleNamespace(name="ACTIVE"))
    stub = StubClient([response("long transcript")], uploaded=uploaded, after_poll=ready)
    svc = service(stub, inline_audio_max_bytes=10)
    assert svc.transcribe_audio(make_audio(tmp_path, 100), "audio/wav") == "long transcript"
    assert stub.polls == 1 and stub.deleted == ["files/abc"]
    assert stub.requests[0]["contents"][1] is ready
    assert stub.uploads[0][1].mime_type == "audio/wav"


def test_remote_file_is_deleted_even_when_transcription_fails(tmp_path):
    uploaded = SimpleNamespace(name="files/x", uri="u", mime_type="audio/wav", state=SimpleNamespace(name="ACTIVE"))
    stub = StubClient([client_error(429)], uploaded=uploaded)
    with pytest.raises(AIServiceError):
        service(stub, inline_audio_max_bytes=1).transcribe_audio(make_audio(tmp_path), "audio/wav")
    assert stub.deleted == ["files/x"]


def test_failed_remote_processing_is_reported(tmp_path):
    uploaded = SimpleNamespace(name="files/x", uri="u", mime_type="audio/wav", state=SimpleNamespace(name="FAILED"))
    stub = StubClient(uploaded=uploaded)
    with pytest.raises(AIServiceError, match="could not process this audio"):
        service(stub, inline_audio_max_bytes=1).transcribe_audio(make_audio(tmp_path), "audio/wav")
    assert stub.deleted == ["files/x"]


def test_remote_processing_timeout_is_reported(tmp_path):
    processing = SimpleNamespace(name="files/x", uri="u", mime_type="audio/wav", state=SimpleNamespace(name="PROCESSING"))
    stub = StubClient(uploaded=processing, after_poll=processing)
    with pytest.raises(AIServiceError, match="took too long"):
        service(stub, inline_audio_max_bytes=1, file_wait_seconds=4).transcribe_audio(make_audio(tmp_path), "audio/wav")


def test_no_speech_marker_and_blank_results_are_errors(tmp_path):
    for text in ("[NO_SPEECH]", "   "):
        with pytest.raises(AIServiceError, match="No speech|empty"):
            service(StubClient([response(text)])).transcribe_audio(make_audio(tmp_path), "audio/wav")


def test_audio_api_error_is_translated(tmp_path):
    with pytest.raises(AIServiceError) as caught:
        service(StubClient([client_error(429)])).transcribe_audio(make_audio(tmp_path), "audio/wav")
    assert caught.value.status_code == 429


def test_truncated_transcript_is_not_silently_accepted(tmp_path):
    with pytest.raises(AIResponseError, match="cut off"):
        service(StubClient([response("partial", finish="MAX_TOKENS")])).transcribe_audio(make_audio(tmp_path), "audio/wav")


def test_dedicated_transcribe_model_uses_interactions_api(tmp_path):
    uploaded = SimpleNamespace(name="files/t", uri="gs://u", mime_type="audio/mp3", state=SimpleNamespace(name="ACTIVE"))
    stub = StubClient(uploaded=uploaded)
    svc = service(stub, audio_model="gemini-3.5-transcribe")
    assert svc.transcribe_audio(make_audio(tmp_path, name="a.mp3"), "audio/mp3") == "interaction transcript"
    request = stub.interaction_requests[0]
    assert request["model"] == "gemini-3.5-transcribe"
    assert request["input"] == [{"type": "audio", "uri": "gs://u", "mime_type": "audio/mp3"}]
    assert stub.deleted == ["files/t"] and stub.requests == []


def test_dedicated_transcribe_model_rejects_video_containers(tmp_path):
    svc = service(StubClient(), audio_model="gemini-3.5-transcribe")
    with pytest.raises(AIServiceError, match="only accepts audio"):
        svc.transcribe_audio(make_audio(tmp_path), "video/mp4")
