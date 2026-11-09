import io
import re

import pytest

from app import create_app
from config import TestingConfig

from .conftest import FakeGemini, LONG_TRANSCRIPT, create_user


class CsrfConfig(TestingConfig):
    WTF_CSRF_ENABLED = True


@pytest.fixture()
def csrf_app(tmp_path):
    class _Config(CsrfConfig):
        UPLOAD_FOLDER = str(tmp_path)

    flask_app = create_app(_Config)
    flask_app.extensions["gemini"] = FakeGemini()
    with flask_app.app_context():
        from app.extensions import db

        db.create_all()
        create_user()
        yield flask_app
        db.session.remove()
        db.drop_all()


def token_from(html):
    return re.search(rb'name="csrf_token" value="([^"]+)"', html).group(1).decode()


def test_post_without_csrf_token_is_rejected(csrf_app):
    client = csrf_app.test_client()
    response = client.post("/auth/login", data={"email": "alice@example.com", "password": "password123"})
    assert response.status_code == 400 and b"expired or the form was invalid" in response.data


def test_post_with_csrf_token_succeeds(csrf_app):
    client = csrf_app.test_client()
    token = token_from(client.get("/auth/login").data)
    response = client.post("/auth/login", data={"email": "alice@example.com", "password": "password123", "csrf_token": token})
    assert response.status_code == 302


def test_forms_contain_csrf_tokens_and_ajax_sends_header(csrf_app):
    client = csrf_app.test_client()
    token = token_from(client.get("/auth/login").data)
    client.post("/auth/login", data={"email": "alice@example.com", "password": "password123", "csrf_token": token})
    page = client.get("/meeting/new").data
    assert b'<meta name="csrf-token"' in page
    assert page.count(b'name="csrf_token"') >= 2  # new-meeting form + sign-out form
    meeting = client.post("/meeting/new", data={"title": "T", "transcript": LONG_TRANSCRIPT, "csrf_token": token_from(page)})
    assert meeting.status_code == 302


def test_chat_requires_csrf_header_when_enabled(csrf_app):
    client = csrf_app.test_client()
    token = token_from(client.get("/auth/login").data)
    client.post("/auth/login", data={"email": "alice@example.com", "password": "password123", "csrf_token": token})
    page = client.get("/meeting/new").data
    client.post("/meeting/new", data={"title": "T", "transcript": LONG_TRANSCRIPT, "csrf_token": token_from(page)})
    assert client.post("/chat/1", json={"message": "hi"}).status_code == 400
    meta = re.search(rb'<meta name="csrf-token" content="([^"]+)"', page).group(1).decode()
    ok = client.post("/chat/1", json={"message": "hi"}, headers={"X-CSRFToken": meta})
    assert ok.status_code == 200 and "answer" in ok.get_json()


def test_logout_and_task_update_require_csrf(csrf_app):
    client = csrf_app.test_client()
    assert client.post("/auth/logout").status_code == 400
    assert client.post("/task/1/status", data={"status": "Completed"}).status_code == 400


def test_security_headers_and_no_cache(auth_client):
    response = auth_client.get("/dashboard")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_session_cookie_flags(client, user):
    response = client.post("/auth/login", data={"email": "alice@example.com", "password": "password123"})
    cookies = response.headers.getlist("Set-Cookie")
    assert cookies and all("HttpOnly" in c and "SameSite=Lax" in c for c in cookies)


def test_production_requires_real_secret_key(monkeypatch, tmp_path):
    class Prod(TestingConfig):
        TESTING = False
        APP_ENV = "production"
        SECRET_KEY = ""
        UPLOAD_FOLDER = str(tmp_path)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(Prod)


def test_production_cookies_are_secure():
    import config

    assert config.Config.SESSION_COOKIE_SECURE == (config.Config.APP_ENV == "production")


def test_unexpected_errors_do_not_leak_internals(app, auth_client, monkeypatch):
    from app.routes import meetings

    def explode(*args, **kwargs):
        raise RuntimeError("SECRET-DB-PASSWORD=hunter2 /srv/app/file.py")

    monkeypatch.setattr(meetings, "analyze_meeting", explode)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = auth_client.post("/meeting/new", data={"title": "T", "transcript": LONG_TRANSCRIPT})
    assert response.status_code == 500
    assert b"hunter2" not in response.data and b"Traceback" not in response.data and b"/srv/app" not in response.data
    assert b"unexpected error" in response.data


def test_unexpected_chat_errors_return_json_without_details(auth_client, meeting, monkeypatch):
    from app.routes import chatbot

    monkeypatch.setattr(chatbot, "answer_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom-internal")))
    response = auth_client.post(f"/chat/{meeting.id}", json={"message": "hi"})
    assert response.status_code == 500 and "boom-internal" not in response.get_data(as_text=True)


def test_404_page_is_friendly(auth_client):
    response = auth_client.get("/does-not-exist")
    assert response.status_code == 404 and b"could not be found" in response.data and b"NEXUS" in response.data


def test_open_redirect_helper_rejects_external_targets(app):
    from app.utils.helpers import is_safe_redirect

    with app.test_request_context("/"):
        assert is_safe_redirect("/tasks") and is_safe_redirect("http://localhost/x")
        assert not is_safe_redirect("https://evil.example/") and not is_safe_redirect("//evil.example") and not is_safe_redirect("")


def test_api_key_and_secrets_never_appear_in_pages(auth_client, app):
    for path in ("/dashboard", "/meeting/new", "/tasks", "/analytics"):
        body = auth_client.get(path).get_data(as_text=True)
        assert app.config["GEMINI_API_KEY"] not in body and app.config["SECRET_KEY"] not in body


def test_sql_injection_attempts_are_inert(auth_client, make_client, user):
    evil = "'; DROP TABLE user; --"
    assert make_client().post("/auth/login", data={"email": evil, "password": evil}).status_code == 200
    assert auth_client.post("/meeting/new", data={"title": evil, "transcript": LONG_TRANSCRIPT}).status_code == 302
    from app.models import User, Meeting

    assert User.query.count() == 1 and Meeting.query.one().title == evil


def test_mismatched_audio_type_cannot_reach_gemini(auth_client, gemini):
    auth_client.post(
        "/meeting/new",
        data={"title": "T", "transcript": "", "audio": (io.BytesIO(b"<?php system($_GET['c']); ?>"), "shell.mp3")},
        content_type="multipart/form-data",
    )
    assert gemini.calls["audio"] == 0
