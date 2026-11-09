import os
from datetime import datetime

import pytest

from flask import g  # noqa: F401

from app import create_app
from app.extensions import db
from app.models import Meeting, Task, User
from app.services.gemini_service import AIServiceError
from config import TestingConfig, build_engine_options, normalize_database_url

ANALYSIS_OK = {
    "summary": "The team agreed on the launch plan and assigned follow-ups.",
    "decisions": ["Launch on 15 December"],
    "topics": ["Launch plan", "Budget"],
    "action_items": [
        {"description": "Prepare the launch checklist", "assignee": "Rahul", "deadline": "2030-12-01"},
        {"description": "Review the budget", "assignee": "Unassigned", "deadline": ""},
    ],
}
LONG_TRANSCRIPT = (
    "Alice: We agreed to launch on 15 December. Rahul will prepare the launch checklist by 1 December. "
    "Someone should review the budget but nobody volunteered."
)


class FakeGemini:
    """Stands in for GeminiService so tests never call the real API."""

    def __init__(self):
        self.analysis = dict(ANALYSIS_OK)
        self.reply = "Rahul is preparing the launch checklist."
        self.transcript = "Speaker 1: Rahul will send the report on Friday. We agreed to ship on Monday."
        self.analysis_error = None
        self.chat_error = None
        self.audio_error = None
        self.calls = {"analysis": 0, "chat": 0, "audio": 0}
        self.last_prompt = None
        self.last_system_instruction = None
        self.last_history = None
        self.last_question = None

    def generate_json(self, prompt, schema, system_instruction=None):
        self.calls["analysis"] += 1
        self.last_prompt = prompt
        if self.analysis_error:
            raise self.analysis_error
        return self.analysis

    def generate_chat_reply(self, system_instruction, history, question):
        self.calls["chat"] += 1
        self.last_system_instruction = system_instruction
        self.last_history = history
        self.last_question = question
        if self.chat_error:
            raise self.chat_error
        return self.reply

    def transcribe_audio(self, path, mime_type):
        self.calls["audio"] += 1
        assert os.path.exists(path), "audio file must exist while it is being transcribed"
        if self.audio_error:
            raise self.audio_error
        return self.transcript


def make_test_config(upload_folder):
    database_url = normalize_database_url(os.getenv("TEST_DATABASE_URL"))

    class _Config(TestingConfig):
        UPLOAD_FOLDER = str(upload_folder)

    if database_url:
        _Config.SQLALCHEMY_DATABASE_URI = database_url
        _Config.SQLALCHEMY_ENGINE_OPTIONS = build_engine_options(database_url)
    return _Config


@pytest.fixture()
def app(tmp_path):
    flask_app = create_app(make_test_config(tmp_path / "uploads"))
    flask_app.extensions["gemini"] = FakeGemini()

    @flask_app.before_request
    def _fresh_request_globals():
        # The fixture keeps one app context open, so `g` would leak between requests (it does not in production).
        from flask import g

        g.pop("_login_user", None)

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def gemini(app):
    return app.extensions["gemini"]


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_client(app):
    """Create additional independent browsers (cookie jars)."""
    return lambda: app.test_client()


def create_user(name="Alice", email="alice@example.com", password="password123"):
    user = User(name=name, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def login(client, email="alice@example.com", password="password123"):
    return client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=False)


@pytest.fixture()
def user(app):
    return create_user()


@pytest.fixture()
def auth_client(client, user):
    login(client)
    return client


def create_meeting(user, title="Weekly Sync", with_tasks=True):
    meeting = Meeting(
        user_id=user.id,
        title=title,
        transcript=LONG_TRANSCRIPT,
        summary="A summary.",
        decisions_json='["Launch on 15 December"]',
        topics_json='["Launch"]',
        ai_status="completed",
    )
    if with_tasks:
        meeting.tasks.append(Task(description="Prepare checklist", assignee_name="Rahul", deadline=datetime(2030, 12, 1)))
        meeting.tasks.append(Task(description="Review budget"))
    db.session.add(meeting)
    db.session.commit()
    return meeting


@pytest.fixture()
def meeting(user):
    return create_meeting(user)


@pytest.fixture()
def ai_error():
    return AIServiceError("The AI is down.", 503)
