"""Input validation helpers. Each returns (clean_value, error_message)."""
from email_validator import EmailNotValidError, validate_email

from .helpers import TASK_STATUSES

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_NAME_LENGTH = 120
MAX_TITLE_LENGTH = 200


def validate_name(raw):
    name = " ".join((raw or "").split())
    if not name:
        return None, "Please enter your full name."
    if len(name) > MAX_NAME_LENGTH:
        return None, f"Name must be at most {MAX_NAME_LENGTH} characters."
    return name, None


def validate_email_address(raw):
    candidate = (raw or "").strip().lower()
    if not candidate:
        return None, "Please enter your email address."
    try:
        result = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return None, "Please enter a valid email address."
    email = result.normalized.lower()
    if len(email) > 255:
        return None, "Email address is too long."
    return email, None


def validate_password(raw):
    password = raw or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        return None, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return None, f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    return password, None


def validate_title(raw):
    title = " ".join((raw or "").split())
    if not title:
        return None, "Meeting title is required."
    if len(title) > MAX_TITLE_LENGTH:
        return None, f"Meeting title must be at most {MAX_TITLE_LENGTH} characters."
    return title, None


def validate_transcript(raw, min_chars, max_chars):
    transcript = (raw or "").strip()
    if not transcript:
        return None, "Provide a transcript or audio recording."
    if len(transcript) < min_chars:
        return None, f"The transcript is too short to analyze (minimum {min_chars} characters)."
    if len(transcript) > max_chars:
        return None, f"The transcript is too long (maximum {max_chars:,} characters)."
    return transcript, None


def validate_task_status(raw):
    status = (raw or "").strip()
    if status not in TASK_STATUSES:
        return None, "Invalid task status."
    return status, None


def validate_chat_message(raw, max_chars):
    message = (raw or "").strip() if isinstance(raw, str) else ""
    if not message:
        return None, "Message is required."
    if len(message) > max_chars:
        return None, f"Message is too long (maximum {max_chars} characters)."
    return message, None
