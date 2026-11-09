"""All communication with Google Gemini lives in this module (google-genai SDK)."""
import json
import logging
import os
import re
import time

from flask import current_app
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

logger = logging.getLogger(__name__)

TRANSCRIBE_MODEL_MIME_TYPES = {
    "audio/wav", "audio/mp3", "audio/mpeg", "audio/aiff", "audio/aac", "audio/ogg",
    "audio/flac", "audio/m4a", "audio/opus", "audio/webm",
}

TRANSCRIPTION_PROMPT = (
    "Transcribe this meeting recording word for word, in the language(s) that are spoken. "
    "Write one paragraph per speaker turn. When you can clearly tell speakers apart, prefix turns "
    "with 'Speaker 1:', 'Speaker 2:', and so on; never guess real names unless a person is "
    "addressed by name in the audio. Do not summarize, translate or add commentary. "
    "If the recording contains no intelligible speech, reply with exactly: [NO_SPEECH]"
)


class AIServiceError(Exception):
    """An AI operation failed. `user_message` is safe to show to the person using the app."""

    def __init__(self, user_message, status_code=502):
        super().__init__(user_message)
        self.user_message = user_message
        self.status_code = status_code


class AIConfigurationError(AIServiceError):
    def __init__(self, user_message):
        super().__init__(user_message, status_code=503)


class AIResponseError(AIServiceError):
    """Gemini answered, but not with something the application can use."""


def parse_json_object(text):
    """Parse a JSON object from model output, tolerating Markdown code fences."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise AIResponseError("The AI returned an empty response. Please try again.")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    logger.warning("Gemini returned JSON that could not be parsed (length=%d)", len(cleaned))
    raise AIResponseError("The AI returned an unreadable response. Please try again.")


class GeminiService:
    def __init__(
        self,
        api_key,
        text_model,
        audio_model,
        timeout_seconds=120,
        audio_timeout_seconds=600,
        inline_audio_max_bytes=15 * 1024 * 1024,
        client_factory=None,
        sleep=time.sleep,
        file_wait_seconds=180,
    ):
        self.api_key = (api_key or "").strip()
        self.text_model = text_model
        self.audio_model = audio_model
        self.timeout_seconds = timeout_seconds
        self.audio_timeout_seconds = audio_timeout_seconds
        self.inline_audio_max_bytes = inline_audio_max_bytes
        self._client_factory = client_factory
        self._clients = {}
        self._sleep = sleep
        self._file_wait_seconds = file_wait_seconds

    @classmethod
    def from_config(cls, config):
        return cls(
            api_key=config.get("GEMINI_API_KEY"),
            text_model=config.get("GEMINI_TEXT_MODEL"),
            audio_model=config.get("GEMINI_AUDIO_MODEL"),
            timeout_seconds=config.get("GEMINI_TIMEOUT_SECONDS", 120),
            audio_timeout_seconds=config.get("GEMINI_AUDIO_TIMEOUT_SECONDS", 600),
            inline_audio_max_bytes=config.get("GEMINI_INLINE_AUDIO_MAX_MB", 15) * 1024 * 1024,
        )

    # ------------------------------------------------------------------ client handling
    def ensure_configured(self, model_name, model_setting):
        if not self.api_key:
            raise AIConfigurationError("Gemini is not configured. Add GEMINI_API_KEY to the .env file.")
        if not model_name:
            raise AIConfigurationError(f"No Gemini model is configured. Set {model_setting} in the .env file.")

    def _client(self, timeout_seconds):
        if timeout_seconds not in self._clients:
            if self._client_factory is not None:
                self._clients[timeout_seconds] = self._client_factory(timeout_seconds)
            else:
                options = types.HttpOptions(
                    timeout=int(timeout_seconds * 1000),
                    base_url=os.getenv("GEMINI_BASE_URL") or None,
                    retry_options=types.HttpRetryOptions(
                        attempts=3, initial_delay=1.0, max_delay=8.0, http_status_codes=[500, 502, 503, 504]
                    ),
                )
                self._clients[timeout_seconds] = genai.Client(api_key=self.api_key, http_options=options)
        return self._clients[timeout_seconds]

    # ------------------------------------------------------------------ error handling
    @staticmethod
    def translate_error(exc):
        """Convert an SDK/network exception into an AIServiceError with a friendly message."""
        if isinstance(exc, AIServiceError):
            return exc
        if isinstance(exc, genai_errors.ClientError):
            code = getattr(exc, "code", None)
            detail = str(getattr(exc, "message", "") or exc)
            logger.warning("Gemini client error %s: %s", code, detail[:300])
            if code == 429:
                return AIServiceError(
                    "The Gemini free-tier quota or rate limit was reached. Wait a minute and try again.", 429
                )
            if code in (401, 403) or "api key" in detail.lower():
                return AIConfigurationError(
                    "Gemini rejected the API key or this model is not allowed for it. Check GEMINI_API_KEY."
                )
            if code == 404:
                return AIConfigurationError(
                    "The configured Gemini model was not found. Update GEMINI_TEXT_MODEL / GEMINI_AUDIO_MODEL in .env."
                )
            if code == 413:
                return AIServiceError("The audio file is too large for Gemini to process.", 413)
            return AIServiceError("Gemini could not process this request. Please try again.", 502)
        if isinstance(exc, genai_errors.ServerError):
            logger.warning("Gemini server error %s", getattr(exc, "code", None))
            return AIServiceError("Gemini is temporarily unavailable. Please try again shortly.", 503)
        names = {cls.__name__ for cls in type(exc).__mro__}  # the SDK's HTTP library may be httpx or a fork
        if isinstance(exc, TimeoutError) or any("Timeout" in name for name in names):
            logger.warning("Gemini request timed out")
            return AIServiceError("The AI request timed out. Please try again.", 504)
        if isinstance(exc, ConnectionError) or names & {"TransportError", "ConnectError", "NetworkError"}:
            logger.warning("Could not reach Gemini: %s", type(exc).__name__)
            return AIServiceError("Could not reach the Gemini service. Check your internet connection.", 503)
        logger.exception("Unexpected Gemini failure")
        return AIServiceError("An unexpected AI error occurred. Please try again.", 502)

    @staticmethod
    def _response_text(response):
        """Return the text of a generate_content response or raise a clear AIResponseError."""
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            logger.warning("Gemini blocked the prompt: %s", feedback.block_reason)
            raise AIResponseError("The AI declined to process this content.")
        candidates = getattr(response, "candidates", None) or []
        finish = str(getattr(candidates[0], "finish_reason", "") or "") if candidates else ""
        text = (getattr(response, "text", None) or "").strip()
        if "MAX_TOKENS" in finish:
            raise AIResponseError("The AI response was cut off because the content is too long.")
        if not text:
            raise AIResponseError("The AI returned an empty response. Please try again.")
        return text

    # ------------------------------------------------------------------ text operations
    def generate_json(self, prompt, schema, system_instruction=None):
        """Ask the text model for a JSON object that follows `schema`."""
        self.ensure_configured(self.text_model, "GEMINI_TEXT_MODEL")
        try:
            response = self._client(self.timeout_seconds).models.generate_content(
                model=self.text_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                ),
            )
            return parse_json_object(self._response_text(response))
        except Exception as exc:  # noqa: BLE001 - translated into a user-safe error below
            raise self.translate_error(exc) from exc

    def generate_chat_reply(self, system_instruction, history, question):
        """Answer `question` using earlier `history` [(role, text), ...] and a system instruction."""
        self.ensure_configured(self.text_model, "GEMINI_TEXT_MODEL")
        contents = [
            types.Content(role="model" if role == "assistant" else "user", parts=[types.Part(text=text)])
            for role, text in history
        ]
        contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
        try:
            response = self._client(self.timeout_seconds).models.generate_content(
                model=self.text_model,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
            return self._response_text(response)
        except Exception as exc:  # noqa: BLE001
            raise self.translate_error(exc) from exc

    # ------------------------------------------------------------------ audio transcription
    def transcribe_audio(self, path, mime_type):
        """Transcribe an audio file. Small files are sent inline, larger ones through the Files API."""
        self.ensure_configured(self.audio_model, "GEMINI_AUDIO_MODEL")
        uses_transcribe_model = "transcribe" in self.audio_model.lower()
        if uses_transcribe_model and mime_type not in TRANSCRIBE_MODEL_MIME_TYPES:
            raise AIServiceError(
                "The configured transcription model only accepts audio files. "
                "Convert this file to mp3 or wav, or set GEMINI_AUDIO_MODEL to a general Gemini model.",
                415,
            )
        client = self._client(self.audio_timeout_seconds)
        uploaded = None
        try:
            size = os.path.getsize(path)
            if uses_transcribe_model:
                uploaded = self._upload_and_wait(client, path, mime_type)
                text = self._transcribe_with_interactions(client, uploaded)
            else:
                if size <= self.inline_audio_max_bytes:
                    with open(path, "rb") as handle:
                        media = types.Part.from_bytes(data=handle.read(), mime_type=mime_type)
                else:
                    uploaded = self._upload_and_wait(client, path, mime_type)
                    media = uploaded
                response = client.models.generate_content(
                    model=self.audio_model,
                    contents=[TRANSCRIPTION_PROMPT, media],
                )
                text = self._response_text(response)
        except Exception as exc:  # noqa: BLE001
            raise self.translate_error(exc) from exc
        finally:
            if uploaded is not None:
                self._delete_remote_file(client, uploaded)
        text = (text or "").strip()
        if not text or text == "[NO_SPEECH]":
            raise AIResponseError("No speech could be detected in this recording.")
        return text

    def _upload_and_wait(self, client, path, mime_type):
        """Upload through the Files API and wait until Gemini has processed the file."""
        uploaded = client.files.upload(file=path, config=types.UploadFileConfig(mime_type=mime_type))
        try:
            waited = 0
            while self._state_name(uploaded) == "PROCESSING":
                if waited >= self._file_wait_seconds:
                    raise AIServiceError("Gemini took too long to process the audio file. Please try again.", 504)
                self._sleep(2)
                waited += 2
                uploaded = client.files.get(name=uploaded.name)
            if self._state_name(uploaded) == "FAILED":
                raise AIServiceError("Gemini could not process this audio file. Try converting it to mp3 or wav.", 422)
        except Exception:
            self._delete_remote_file(client, uploaded)  # do not leave the file behind on Google's side
            raise
        return uploaded

    @staticmethod
    def _state_name(uploaded):
        state = getattr(uploaded, "state", None)
        return str(getattr(state, "name", state) or "").split(".")[-1].upper()

    def _transcribe_with_interactions(self, client, uploaded):
        """Dedicated speech-to-text models (for example gemini-3.5-transcribe) use the Interactions API."""
        interaction = client.interactions.create(
            model=self.audio_model,
            input=[{"type": "audio", "uri": uploaded.uri, "mime_type": uploaded.mime_type}],
        )
        return getattr(interaction, "output_text", None)

    @staticmethod
    def _delete_remote_file(client, uploaded):
        try:
            client.files.delete(name=uploaded.name)
        except Exception:  # noqa: BLE001 - cleanup must never hide the real result
            logger.warning("Could not delete the temporary Gemini file %s", getattr(uploaded, "name", "?"))


def get_gemini_service():
    """Return the application's shared GeminiService (tests replace it via app.extensions)."""
    if "gemini" not in current_app.extensions:
        current_app.extensions["gemini"] = GeminiService.from_config(current_app.config)
    return current_app.extensions["gemini"]
