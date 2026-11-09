from datetime import datetime

from app.extensions import db
from app.models import Task

from .conftest import create_meeting, create_user, login


def test_tasks_page_lists_only_own_tasks(app, auth_client, user, meeting):
    other = create_user("Bob", "bob@example.com")
    create_meeting(other, "Bob meeting")
    body = auth_client.get("/tasks").data
    assert b"Prepare checklist" in body and b"Weekly Sync" in body
    assert b"Bob meeting" not in body


def test_tasks_default_status_and_assignee(meeting):
    task = Task.query.filter_by(description="Review budget").one()
    assert task.status == "Pending" and task.assignee_name == "Unassigned" and task.deadline is None


def test_status_update_works(auth_client, meeting):
    task = meeting.tasks[0]
    for status in ("In Progress", "Completed", "Pending"):
        response = auth_client.post(f"/task/{task.id}/status", data={"status": status})
        assert response.status_code == 302
        db.session.expire_all()
        assert db.session.get(Task, task.id).status == status


def test_status_update_redirects_back_to_same_site_referrer(auth_client, meeting):
    response = auth_client.post(
        f"/task/{meeting.tasks[0].id}/status", data={"status": "Completed"}, headers={"Referer": "http://localhost/tasks"}
    )
    assert response.headers["Location"] == "http://localhost/tasks"


def test_status_update_ignores_external_referrer(auth_client, meeting):
    response = auth_client.post(
        f"/task/{meeting.tasks[0].id}/status", data={"status": "Completed"}, headers={"Referer": "https://evil.example/x"}
    )
    assert response.headers["Location"].endswith("/dashboard")


def test_invalid_status_is_rejected_and_task_unchanged(auth_client, meeting):
    task = meeting.tasks[0]
    for bad in ("Done", "", "completed", "<b>x</b>", "Pending; DROP TABLE task"):
        auth_client.post(f"/task/{task.id}/status", data={"status": bad})
    auth_client.post(f"/task/{task.id}/status", data={})
    db.session.expire_all()
    assert db.session.get(Task, task.id).status == "Pending"


def test_invalid_status_shows_error(auth_client, meeting):
    auth_client.post(f"/task/{meeting.tasks[0].id}/status", data={"status": "Bogus"})
    assert b"Invalid task status" in auth_client.get("/dashboard").data


def test_user_cannot_update_another_users_task(app, make_client, meeting):
    create_user("Bob", "bob@example.com")
    bob = make_client()
    login(bob, "bob@example.com")
    task_id = meeting.tasks[0].id
    assert bob.post(f"/task/{task_id}/status", data={"status": "Completed"}).status_code == 404
    db.session.expire_all()
    assert db.session.get(Task, task_id).status == "Pending"


def test_updating_missing_task_returns_404(auth_client):
    assert auth_client.post("/task/12345/status", data={"status": "Completed"}).status_code == 404


def test_status_update_requires_post(auth_client, meeting):
    assert auth_client.get(f"/task/{meeting.tasks[0].id}/status").status_code == 405


def test_database_rejects_invalid_status(app, meeting):
    import pytest
    from sqlalchemy.exc import IntegrityError

    meeting.tasks[0].status = "Bogus"
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_dashboard_and_analytics_counts(auth_client, user, meeting):
    first, second = meeting.tasks
    first.status = "Completed"
    second.status = "In Progress"
    db.session.commit()
    dashboard = auth_client.get("/dashboard").data.decode()
    assert "<small>ACTION ITEMS</small><b>2</b>" in dashboard
    assert "<small>IN PROGRESS</small><b>1</b>" in dashboard
    assert "<small>COMPLETED</small><b>1</b>" in dashboard
    analytics = auth_client.get("/analytics").data.decode()
    assert '[0, 1, 1]' in analytics


def test_analytics_with_no_tasks(auth_client):
    response = auth_client.get("/analytics")
    assert response.status_code == 200 and b"[0, 0, 0]" in response.data


def test_tasks_pagination(app, auth_client, user):
    app.config["TASKS_PER_PAGE"] = 3
    meeting = create_meeting(user, with_tasks=False)
    for index in range(7):
        meeting.tasks.append(Task(description=f"Task number {index}"))
    db.session.commit()
    first = auth_client.get("/tasks").data
    assert b"Page 1 of 3" in first and b"Older" in first
    last = auth_client.get("/tasks?page=3").data
    assert b"Page 3 of 3" in last and b"Newer" in last
    assert auth_client.get("/tasks?page=99").status_code == 200
    assert auth_client.get("/tasks?page=abc").status_code == 200
