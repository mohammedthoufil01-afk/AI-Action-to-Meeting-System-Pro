import io

import pytest
from werkzeug.datastructures import FileStorage

from app.utils.file_handler import AudioUploadError, detect_audio_mime, remove_file, save_audio_upload

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


def upload(name, data, mimetype="audio/wav"):
    return FileStorage(stream=io.BytesIO(data), filename=name, content_type=mimetype)


@pytest.mark.parametrize(
    "header,ext,mime",
    [
        (b"ID3\x04" + b"\0" * 12, "mp3", "audio/mp3"),
        (bytes([0xFF, 0xFB]) + b"\0" * 14, "mp3", "audio/mp3"),
        (bytes([0xFF, 0xF1]) + b"\0" * 14, "aac", "audio/aac"),
        (b"RIFF\0\0\0\0WAVE" + b"\0" * 4, "wav", "audio/wav"),
        (b"OggS" + b"\0" * 12, "ogg", "audio/ogg"),
        (b"fLaC" + b"\0" * 12, "flac", "audio/flac"),
        (b"FORM\0\0\0\0AIFF" + b"\0" * 4, "aiff", "audio/aiff"),
        (b"\x1a\x45\xdf\xa3" + b"\0" * 12, "webm", "audio/webm"),
        (b"\0\0\0\x20ftypM4A " + b"\0" * 4, "m4a", "audio/m4a"),
        (b"\0\0\0\x20ftypisom" + b"\0" * 4, "mp4", "video/mp4"),
        (b"MZ\x90\0" + b"\0" * 12, "mp3", None),
        (b"", "mp3", None),
    ],
)
def test_detect_audio_mime(header, ext, mime):
    assert detect_audio_mime(header, ext) == mime


def test_valid_upload_is_saved_with_random_name(tmp_path):
    saved = save_audio_upload(upload("My Meeting!.WAV", WAV), str(tmp_path), 10_000)
    assert saved.original_name == "My_Meeting.WAV" and saved.mime_type == "audio/wav" and saved.size_bytes == len(WAV)
    assert saved.path.startswith(str(tmp_path)) and "Meeting" not in saved.path
    remove_file(saved.path)
    remove_file(saved.path)  # deleting twice is harmless
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["", "noextension", "virus.exe", "script.py", "archive.zip"])
def test_bad_names_are_rejected(tmp_path, name):
    with pytest.raises(AudioUploadError):
        save_audio_upload(upload(name, WAV), str(tmp_path), 10_000)


def test_non_audio_mimetype_is_rejected(tmp_path):
    with pytest.raises(AudioUploadError, match="not an audio"):
        save_audio_upload(upload("a.wav", WAV, "text/html"), str(tmp_path), 10_000)


def test_octet_stream_is_accepted_when_content_is_audio(tmp_path):
    saved = save_audio_upload(upload("a.wav", WAV, "application/octet-stream"), str(tmp_path), 10_000)
    assert saved.mime_type == "audio/wav"


def test_size_limit_removes_file(tmp_path):
    with pytest.raises(AudioUploadError, match="too large"):
        save_audio_upload(upload("a.wav", WAV), str(tmp_path), 10)
    assert list(tmp_path.iterdir()) == []
