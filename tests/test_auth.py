from app.extensions import db
from app.models import User

from .conftest import create_user, login

PROTECTED_GET = ["/dashboard", "/meeting/new", "/tasks", "/analytics", "/meeting/1"]


def signup(client, **overrides):
    data = {"name": "Alice", "email": "alice@example.com", "password": "password123"}
    data.update(overrides)
    return client.post("/auth/signup", data=data)


def test_signup_creates_user_and_logs_in(client):
    response = signup(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    user = User.query.filter_by(email="alice@example.com").one()
    assert user.password_hash != "password123"
    assert user.check_password("password123")


def test_signup_normalizes_email_case(client):
    signup(client, email="  Alice@Example.COM ")
    assert User.query.filter_by(email="alice@example.com").count() == 1


def test_duplicate_email_is_rejected(client, make_client):
    signup(client)
    response = signup(make_client(), name="Other", email="ALICE@example.com")
    assert response.status_code == 409
    assert b"already exists" in response.data
    assert User.query.count() == 1


def test_signup_validation_errors(client):
    assert signup(client, name="").status_code == 400
    assert signup(client, email="not-an-email").status_code == 400
    response = signup(client, password="short")
    assert response.status_code == 400 and b"at least 8" in response.data
    assert User.query.count() == 0


def test_signup_keeps_entered_values_on_error(client):
    response = signup(client, name="Zed", email="zed@example.com", password="x")
    assert b'value="Zed"' in response.data and b'value="zed@example.com"' in response.data


def test_login_with_correct_password(client, user):
    response = login(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200


def test_login_with_wrong_password_or_unknown_email(client, user):
    assert b"Invalid email or password" in login(client, password="wrong-password").data
    assert b"Invalid email or password" in login(client, email="nobody@example.com").data
    assert client.get("/dashboard").status_code == 302


def test_login_ignores_empty_submission(client):
    assert client.post("/auth/login", data={}).status_code == 200


def test_login_redirects_only_to_local_next_url(client, user):
    external = client.post("/auth/login?next=https://evil.example/", data={"email": "alice@example.com", "password": "password123"})
    assert external.headers["Location"].endswith("/dashboard")
    client.post("/auth/logout")
    local = client.post("/auth/login?next=/tasks", data={"email": "alice@example.com", "password": "password123"})
    assert local.headers["Location"].endswith("/tasks")


def test_logout_ends_session(auth_client):
    response = auth_client.post("/auth/logout")
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    assert auth_client.get("/dashboard").status_code == 302


def test_logout_requires_post(auth_client):
    assert auth_client.get("/auth/logout").status_code == 405
    assert auth_client.get("/dashboard").status_code == 200


def test_protected_routes_require_authentication(client):
    for path in PROTECTED_GET:
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/auth/login" in response.headers["Location"], path


def test_protected_posts_require_authentication(client):
    assert client.post("/meeting/new", data={"title": "x"}).status_code == 302
    assert client.post("/task/1/status", data={"status": "Completed"}).status_code == 302
    chat = client.post("/chat/1", json={"message": "hi"})
    assert chat.status_code in (302, 401)


def test_authenticated_user_is_redirected_away_from_login_and_signup(auth_client):
    assert auth_client.get("/auth/login").status_code == 302
    assert auth_client.get("/auth/signup").status_code == 302


def test_home_redirects_by_authentication_state(client, user):
    assert "/auth/login" in client.get("/").headers["Location"]
    login(client)
    assert client.get("/").headers["Location"].endswith("/dashboard")


def test_email_unique_constraint_enforced_by_database(app, user):
    import pytest
    from sqlalchemy.exc import IntegrityError

    db.session.add(User(name="Dup", email="alice@example.com", password_hash="x"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
