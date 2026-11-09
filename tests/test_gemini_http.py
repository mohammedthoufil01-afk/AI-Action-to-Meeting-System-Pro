"""Runs the real google-genai SDK against a local fake Gemini HTTP server.

This verifies that the requests built by GeminiService are valid for the SDK and that real SDK
response objects are parsed correctly. It never contacts Google and uses no API quota.
"""
import base64
import json
import threading

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from app.services.gemini_service import AIResponseError, AIServiceError, GeminiService


class FakeGeminiServer:
    def __init__(self):
        self.app = Flask("fake-gemini")
        self.requests = []
        self.generate_responses = []
        self.deleted_files = []
        self.file_state = "ACTIVE"
        self._register()
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/"

    def reply_text(self, text, finish="STOP"):
        self.generate_responses.append(
            (200, {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]})
        )

    def reply_error(self, code, status, message="problem"):
        self.generate_responses.append((code, {"error": {"code": code, "message": message, "status": status}}))

    def _file_json(self):
        return {
            "name": "files/fake123",
            "mimeType": "audio/wav",
            "uri": f"{self.base_url}v1/files/fake123",
            "state": self.file_state,
            "sizeBytes": "100",
        }

    def _register(self):
        app = self.app

        @app.post("/<version>/models/<path:model_action>")
        def generate(version, model_action):
            self.requests.append({"model_action": model_action, "body": request.get_json(), "key": request.headers.get("x-goog-api-key")})
            status, body = self.generate_responses.pop(0)
            return jsonify(body), status

        @app.post("/upload/<version>/files")
        def upload_start_or_finish(version):
            command = request.headers.get("X-Goog-Upload-Command", "")
            if "start" in command:
                self.requests.append({"model_action": "files.start", "body": request.get_json(silent=True)})
                response = jsonify({})
                response.headers["x-goog-upload-url"] = f"{self.base_url}upload/v1/files/session1"
                response.headers["x-goog-upload-status"] = "active"
                return response
            return "", 400

        @app.post("/upload/<version>/files/session1")
        def upload_finish(version):
            self.requests.append({"model_action": "files.finalize", "size": len(request.get_data())})
            response = jsonify({"file": self._file_json()})
            response.headers["x-goog-upload-status"] = "final"
            return response

        @app.get("/<version>/files/<name>")
        def get_file(version, name):
            return jsonify(self._file_json())

        @app.delete("/<version>/files/<name>")
        def delete_file(version, name):
            self.deleted_files.append(name)
            return jsonify({})


@pytest.fixture()
def fake_server(monkeypatch):
    server = FakeGeminiServer()
    server.thread.start()
    monkeypatch.setenv("GEMINI_BASE_URL", server.base_url)
    yield server
    server.server.shutdown()


@pytest.fixture()
def svc():
    return GeminiService("fake-key", "fake-text", "fake-audio", timeout_seconds=10, audio_timeout_seconds=10)


def test_structured_json_request_is_valid_for_the_sdk(fake_server, svc):
    fake_server.reply_text(json.dumps({"summary": "S", "decisions": [], "topics": [], "action_items": []}))
    result = svc.generate_json("analyze this", {"type": "object", "properties": {"summary": {"type": "string"}}}, "SYSTEM")
    assert result["summary"] == "S"
    sent = fake_server.requests[0]
    assert sent["model_action"] == "fake-text:generateContent" and sent["key"] == "fake-key"
    body = sent["body"]
    assert body["contents"][0]["parts"][0]["text"] == "analyze this"
    assert body["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["type"] == "object"


def test_chat_request_roles_over_the_wire(fake_server, svc):
    fake_server.reply_text("Because Rahul volunteered.")
    assert svc.generate_chat_reply("SYS", [("user", "q1"), ("assistant", "a1")], "q2") == "Because Rahul volunteered."
    body = fake_server.requests[0]["body"]
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]


def test_inline_audio_is_base64_encoded(fake_server, svc, tmp_path):
    audio = tmp_path / "m.wav"
    audio.write_bytes(b"RIFFdata-bytes")
    fake_server.reply_text("Speaker 1: hi there")
    assert svc.transcribe_audio(str(audio), "audio/wav") == "Speaker 1: hi there"
    parts = fake_server.requests[0]["body"]["contents"][0]["parts"]
    inline = next(p["inlineData"] for p in parts if "inlineData" in p)
    assert (inline.get("mimeType") or inline.get("mime_type")) == "audio/wav"
    assert base64.b64decode(inline["data"]) == b"RIFFdata-bytes"


def test_large_audio_goes_through_files_api_and_is_deleted(fake_server, tmp_path):
    svc = GeminiService("fake-key", "fake-text", "fake-audio", inline_audio_max_bytes=5, audio_timeout_seconds=10)
    audio = tmp_path / "big.wav"
    audio.write_bytes(b"RIFF" + b"0" * 100)
    fake_server.reply_text("Transcript from uploaded file")
    assert svc.transcribe_audio(str(audio), "audio/wav") == "Transcript from uploaded file"
    kinds = [r["model_action"] for r in fake_server.requests]
    assert kinds == ["files.start", "files.finalize", "fake-audio:generateContent"]
    parts = fake_server.requests[-1]["body"]["contents"][0]["parts"]
    file_parts = [p.get("fileData") or p.get("file_data") for p in parts if p.get("fileData") or p.get("file_data")]
    assert file_parts and (file_parts[0].get("fileUri") or file_parts[0].get("file_uri")).endswith("files/fake123")
    assert fake_server.deleted_files == ["fake123"]


@pytest.mark.parametrize(
    "code,status,expected_status",
    [(429, "RESOURCE_EXHAUSTED", 429), (404, "NOT_FOUND", 503), (403, "PERMISSION_DENIED", 503), (400, "INVALID_ARGUMENT", 502)],
)
def test_http_errors_are_translated(fake_server, svc, code, status, expected_status):
    fake_server.reply_error(code, status)
    with pytest.raises(AIServiceError) as caught:
        svc.generate_json("p", {"type": "object"})
    assert caught.value.status_code == expected_status


def test_invalid_api_key_error_is_translated(fake_server, svc):
    fake_server.reply_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")
    with pytest.raises(AIServiceError, match="API key"):
        svc.generate_json("p", {"type": "object"})


def test_server_error_is_retried_then_succeeds(fake_server, svc):
    fake_server.reply_error(503, "UNAVAILABLE")
    fake_server.reply_text('{"ok": true}')
    assert svc.generate_json("p", {"type": "object"}) == {"ok": True}
    assert len(fake_server.requests) == 2


def test_persistent_server_error_is_reported(fake_server, svc):
    for _ in range(3):
        fake_server.reply_error(503, "UNAVAILABLE")
    with pytest.raises(AIServiceError, match="temporarily unavailable"):
        svc.generate_json("p", {"type": "object"})


def test_empty_and_malformed_responses_over_the_wire(fake_server, svc):
    fake_server.reply_text("not json at all")
    with pytest.raises(AIResponseError):
        svc.generate_json("p", {"type": "object"})
    fake_server.generate_responses.append((200, {"candidates": []}))
    with pytest.raises(AIResponseError):
        svc.generate_json("p", {"type": "object"})


def test_unreachable_server_is_reported(monkeypatch):
    monkeypatch.setenv("GEMINI_BASE_URL", "http://127.0.0.1:9/")
    svc = GeminiService("k", "m", "m", timeout_seconds=3)
    with pytest.raises(AIServiceError) as caught:
        svc.generate_json("p", {"type": "object"})
    assert caught.value.status_code in (503, 504, 502)
