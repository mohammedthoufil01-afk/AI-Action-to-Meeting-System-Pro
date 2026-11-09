"""Safe handling of uploaded audio files."""
import logging
import os
import uuid
from dataclasses import dataclass

from werkzeug.utils import secure_filename

ALLOWED_AUDIO_EXTENSIONS = {
    "mp3", "wav", "m4a", "aac", "ogg", "oga", "opus", "flac", "webm", "mp4", "mpeg", "mpga", "aif", "aiff",
}
logger = logging.getLogger(__name__)

SUPPORTED_FORMATS_TEXT = "mp3, wav, m4a, aac, ogg, flac, aiff, webm, mp4"


class AudioUploadError(Exception):
    """Raised when an uploaded audio file is rejected. The message is safe to show to the user."""


@dataclass
class SavedAudio:
    path: str
    original_name: str
    mime_type: str
    size_bytes: int


def detect_audio_mime(header, extension):
    """Identify the audio container from its first bytes. Returns a Gemini MIME type or None."""
    if header[:3] == b"ID3":
        return "audio/mp3"
    if len(header) > 1 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0:
        is_adts_aac = (header[1] & 0xF6) == 0xF0  # layer bits 00 means AAC in ADTS framing
        return "audio/aac" if is_adts_aac else "audio/mp3"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    if header[:4] == b"OggS":
        return "audio/ogg"
    if header[:4] == b"fLaC":
        return "audio/flac"
    if header[:4] == b"FORM" and header[8:12] in (b"AIFF", b"AIFC"):
        return "audio/aiff"
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if header[4:8] == b"ftyp":
        return "video/mp4" if extension == "mp4" else "audio/m4a"
    return None


def save_audio_upload(file_storage, upload_folder, max_bytes):
    """Validate an uploaded audio file and store it under a random name. Caller must delete it."""
    original_name = secure_filename(file_storage.filename or "")
    if not original_name or "." not in original_name:
        raise AudioUploadError("The audio file needs a valid name and extension.")
    extension = original_name.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise AudioUploadError(f"Unsupported audio format. Supported formats: {SUPPORTED_FORMATS_TEXT}.")

    declared = (file_storage.mimetype or "").lower()
    if declared and not (
        declared.startswith(("audio/", "video/mp4", "video/webm")) or declared == "application/octet-stream"
    ):
        raise AudioUploadError("The uploaded file is not an audio recording.")

    header = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    mime_type = detect_audio_mime(header, extension)
    if mime_type is None:
        raise AudioUploadError("The file does not look like a valid audio recording.")

    os.makedirs(upload_folder, exist_ok=True)
    path = os.path.join(upload_folder, f"{uuid.uuid4().hex}.{extension}")
    try:
        file_storage.save(path)
        size = os.path.getsize(path)
    except OSError as exc:
        remove_file(path)
        raise AudioUploadError("The audio file could not be saved. Please try again.") from exc
    if size == 0:
        remove_file(path)
        raise AudioUploadError("The uploaded audio file is empty.")
    if size > max_bytes:
        remove_file(path)
        raise AudioUploadError(f"The audio file is too large (maximum {max_bytes // (1024 * 1024)} MB).")
    return SavedAudio(path=path, original_name=original_name[:255], mime_type=mime_type, size_bytes=size)


def remove_file(path):
    """Delete a file, ignoring the case where it is already gone."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Could not delete temporary file %s", path)
