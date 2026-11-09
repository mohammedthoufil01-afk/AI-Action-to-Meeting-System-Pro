"""Small helpers shared across the application."""
import json
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from flask import request

TASK_STATUSES = ("Pending", "In Progress", "Completed")
DEFAULT_ASSIGNEE = "Unassigned"


def utcnow():
    """Current UTC time as a naive datetime (the format already used by the database columns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_json_list(raw):
    """Decode a JSON list stored in a text column; return [] for anything unusable."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def is_safe_redirect(target):
    """True when `target` points to this same site (prevents open redirects)."""
    if not target:
        return False
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and host_url.netloc == redirect_url.netloc
