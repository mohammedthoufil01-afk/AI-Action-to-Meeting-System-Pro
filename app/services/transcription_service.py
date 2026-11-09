"""Audio upload -> transcript."""
import logging

from ..utils.file_handler import remove_file
from .gemini_service import get_gemini_service

logger = logging.getLogger(__name__)


def transcribe_saved_audio(saved_audio):
    """Transcribe a SavedAudio with Gemini and always delete the temporary local file afterwards."""
    try:
        logger.info("Transcribing audio (%s, %d bytes)", saved_audio.mime_type, saved_audio.size_bytes)
        return get_gemini_service().transcribe_audio(saved_audio.path, saved_audio.mime_type)
    finally:
        remove_file(saved_audio.path)
