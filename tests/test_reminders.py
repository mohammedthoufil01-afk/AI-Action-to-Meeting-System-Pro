from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Task
from app.services import reminder_service
from app.utils.helpers import utcnow

from .conftest import create_meeting, create_user


@pytest.fixture()
def mail_config(app):
    app.config.update(MAIL_SERVER="smtp.test", MAIL_DEFAULT_SENDER="nexus@example.com")
    return app


@pytest.fixture()
def outbox(app, monkeypatch):
    sent = []

    def fake_send(self):
        sent.append(self)
        return 1

    monkeypatch.setattr(reminder_service.EmailMessage, "send", fake_send)
    return sent


def make_task(meeting, hours_from_now, **fields):
    task = Task(description=f"Task due in {hours_from_now}h", deadline=utcnow() + timedelta(hours=hours_from_now), **fields)
    meeting.tasks.append(task)
    db.session.commit()
    return task


def test_due_and_overdue_tasks_are_emailed_once(mail_config, outbox, user):
    meeting = create_meeting(user, with_tasks=False)
    soon, overdue = make_task(meeting, 5), make_task(meeting, -48)
    far, done = make_task(meeting, 200), make_task(meeting, 2, status="Completed")
    assert reminder_service.send_due_reminders(mail_config) == 1
    assert len(outbox) == 1
    message = outbox[0]
    assert message.to == ["alice@example.com"] and "2 action item" in message.subject
    assert soon.description in message.body and overdue.description in message.body
    assert far.description not in message.body and done.description not in message.body
    db.session.expire_all()
    assert soon.reminder_sent and overdue.reminder_sent and not far.reminder_sent and not done.reminder_sent
    assert reminder_service.send_due_reminders(mail_config) == 0 and len(outbox) == 1


def test_one_email_per_owner(mail_config, outbox, user):
    bob = create_user("Bob", "bob@example.com")
    make_task(create_meeting(user, with_tasks=False), 3)
    make_task(create_meeting(bob, "B", with_tasks=False), 3)
    assert reminder_service.send_due_reminders(mail_config) == 2
    assert sorted(m.to[0] for m in outbox) == ["alice@example.com", "bob@example.com"]


def test_nothing_is_sent_when_mail_is_not_configured(app, outbox, user):
    make_task(create_meeting(user, with_tasks=False), 3)
    assert reminder_service.send_due_reminders(app) == 0 and outbox == []


def test_failed_send_keeps_task_pending_for_retry(mail_config, user, monkeypatch):
    task = make_task(create_meeting(user, with_tasks=False), 3)

    def failing_send(self):
        raise OSError("smtp down")

    monkeypatch.setattr(reminder_service.EmailMessage, "send", failing_send)
    assert reminder_service.send_due_reminders(mail_config) == 0
    db.session.expire_all()
    assert task.reminder_sent is False


def test_tasks_without_deadline_are_ignored(mail_config, outbox, meeting):
    assert reminder_service.send_due_reminders(mail_config) == 0 and outbox == []


def test_cli_command_runs(mail_config, outbox, user):
    make_task(create_meeting(user, with_tasks=False), 3)
    result = mail_config.test_cli_runner().invoke(args=["send-reminders"])
    assert "Reminder emails sent: 1" in result.output


def test_scheduler_is_disabled_in_testing_and_when_turned_off(app):
    assert reminder_service.start_scheduler(app) is None
    app.config.update(TESTING=False, SCHEDULER_ENABLED=False)
    assert reminder_service.start_scheduler(app) is None


def test_scheduler_starts_once(app):
    app.config.update(TESTING=False, SCHEDULER_ENABLED=True, REMINDER_INTERVAL_MINUTES=60)
    first = reminder_service.start_scheduler(app)
    try:
        assert first is not None and first.running
        assert reminder_service.start_scheduler(app) is first
        assert len(first.get_jobs()) == 1
    finally:
        first.shutdown(wait=False)
        reminder_service._scheduler = None
