from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import ChatMessage, Meeting, Task, User

from .conftest import create_meeting, create_user


def add_chat(meeting, user, role="user", content="hi"):
    message = ChatMessage(meeting_id=meeting.id, user_id=user.id, role=role, content=content)
    db.session.add(message)
    db.session.commit()
    return message


def test_all_tables_and_indexes_are_created(app):
    inspector = inspect(db.engine)
    assert {"user", "meeting", "task", "chat_message"} <= set(inspector.get_table_names())
    indexed = {t: {c for i in inspector.get_indexes(t) for c in i["column_names"]} for t in ("meeting", "task", "chat_message", "user")}
    assert {"user_id", "created_at"} <= indexed["meeting"]
    assert {"meeting_id", "status"} <= indexed["task"]
    assert {"meeting_id", "user_id"} <= indexed["chat_message"]
    assert "email" in indexed["user"]


def test_relationships_work_both_ways(user, meeting):
    assert meeting.owner is user and meeting in user.meetings
    assert len(meeting.tasks) == 2 and meeting.tasks[0].meeting is meeting
    message = add_chat(meeting, user)
    assert message.meeting is meeting and message.author is user and meeting.chat_messages == [message]


def test_defaults_are_applied(user):
    meeting = Meeting(user_id=user.id, title="T")
    db.session.add(meeting)
    db.session.commit()
    assert meeting.created_at is not None and meeting.meeting_date is not None
    assert meeting.decisions == [] and meeting.topics == []
    assert user.created_at is not None


def test_json_properties_survive_unicode_and_bad_data(user):
    meeting = Meeting(user_id=user.id, title="T", decisions_json='["café ☕", "日本語"]', topics_json="not-json")
    assert meeting.decisions == ["café ☕", "日本語"] and meeting.topics == []
    meeting.topics_json = '{"a": 1}'
    assert meeting.topics == []


def test_deleting_meeting_removes_tasks_and_chat_messages(user, meeting):
    add_chat(meeting, user)
    keeper = create_meeting(user, "Keep me")
    add_chat(keeper, user)
    db.session.delete(meeting)
    db.session.commit()
    assert Meeting.query.count() == 1
    assert Task.query.filter_by(meeting_id=meeting.id).count() == 0
    assert ChatMessage.query.filter_by(meeting_id=meeting.id).count() == 0
    assert Task.query.count() == 2 and ChatMessage.query.count() == 1


def test_database_level_cascade_without_orm(user, meeting):
    """Rows must also disappear when deleted with plain SQL (ON DELETE CASCADE enforced by the database)."""
    add_chat(meeting, user)
    meeting_id = meeting.id
    db.session.expunge_all()
    db.session.execute(db.text("DELETE FROM meeting WHERE id = :id"), {"id": meeting_id})
    db.session.commit()
    assert Task.query.filter_by(meeting_id=meeting_id).count() == 0
    assert ChatMessage.query.filter_by(meeting_id=meeting_id).count() == 0


def test_deleting_user_removes_everything(user, meeting):
    other = create_user("Bob", "bob@example.com")
    other_meeting = create_meeting(other, "Bob's")
    add_chat(meeting, user)
    deleted_meeting_id = meeting.id
    db.session.delete(user)
    db.session.commit()
    assert User.query.count() == 1 and Meeting.query.count() == 1
    assert Task.query.filter_by(meeting_id=deleted_meeting_id).count() == 0
    assert ChatMessage.query.count() == 0
    assert Meeting.query.one().id == other_meeting.id


def test_orphan_rows_are_rejected_by_foreign_keys(user):
    db.session.add(Task(meeting_id=99999, description="orphan"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    db.session.add(Meeting(user_id=99999, title="orphan"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_not_null_constraints(user):
    for bad in (Meeting(user_id=user.id, title=None), User(name=None, email="a@b.co", password_hash="x")):
        db.session.add(bad)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_chat_role_constraint(user, meeting):
    db.session.add(ChatMessage(meeting_id=meeting.id, user_id=user.id, role="system", content="x"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_failed_transaction_rolls_back_all_changes(user):
    meeting = Meeting(user_id=user.id, title="Half-saved")
    meeting.tasks.append(Task(description="ok task"))
    meeting.tasks.append(Task(description=None))  # violates NOT NULL
    db.session.add(meeting)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert Meeting.query.count() == 0 and Task.query.count() == 0
    db.session.add(Meeting(user_id=user.id, title="After rollback works"))
    db.session.commit()
    assert Meeting.query.count() == 1


def test_unicode_and_long_text_round_trip(user):
    text = "Ünïcödé ✓ " * 5000
    meeting = Meeting(user_id=user.id, title="Ω", transcript=text)
    db.session.add(meeting)
    db.session.commit()
    db.session.expire_all()
    assert db.session.get(Meeting, meeting.id).transcript == text


# ---------------------------------------------------------------- SQLite -> PostgreSQL copy script
def test_sqlite_to_postgres_copy_script(tmp_path, monkeypatch):
    """The copy script keeps ids/hashes, repairs invalid rows, skips orphans and refuses non-empty targets."""
    import runpy
    import sqlite3
    import sys

    from flask import Flask
    from flask_migrate import upgrade

    old = tmp_path / "old.db"
    connection = sqlite3.connect(old)
    connection.executescript(
        """
        CREATE TABLE user (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
                           password_hash VARCHAR(255) NOT NULL, created_at DATETIME);
        CREATE TABLE meeting (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title VARCHAR(200) NOT NULL, meeting_date DATETIME,
                              audio_filename VARCHAR(255), transcript TEXT, summary TEXT, decisions_json TEXT, topics_json TEXT,
                              ai_status VARCHAR(40), created_at DATETIME);
        CREATE TABLE task (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL, description VARCHAR(700) NOT NULL,
                           assignee_name VARCHAR(150), deadline DATETIME, status VARCHAR(30), reminder_sent BOOLEAN, created_at DATETIME);
        CREATE TABLE chat_message (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                                   role VARCHAR(20) NOT NULL, content TEXT NOT NULL, created_at DATETIME);
        INSERT INTO user VALUES (7, 'Old User', 'old@example.com', 'scrypt:32768:8:1$salt$hash', '2026-01-01 10:00:00');
        INSERT INTO meeting VALUES (3, 7, 'Old meeting', '2026-01-02 09:00:00', NULL, 'transcript', 'summary', '["d"]', '["t"]', 'completed', '2026-01-02 09:00:00');
        INSERT INTO meeting VALUES (4, 99, 'Orphan meeting', NULL, NULL, '', '', '[]', '[]', 'completed', NULL);
        INSERT INTO task VALUES (1, 3, 'Fine task', 'Rahul', '2030-01-01 00:00:00', 'Completed', 1, NULL);
        INSERT INTO task VALUES (2, 3, 'Odd status', NULL, NULL, 'Done!', NULL, NULL);
        INSERT INTO task VALUES (3, 4, 'Orphan task', 'x', NULL, 'Pending', 0, NULL);
        INSERT INTO chat_message VALUES (1, 3, 7, 'user', 'hello', NULL);
        INSERT INTO chat_message VALUES (2, 3, 7, 'robot', 'bad role', NULL);
        """
    )
    connection.commit()
    connection.close()

    target = tmp_path / "target.db"
    flask_app = Flask("migration-target")
    flask_app.config.update(SQLALCHEMY_DATABASE_URI=f"sqlite:///{target}", SQLALCHEMY_TRACK_MODIFICATIONS=False)
    from app.extensions import db as ext_db, migrate as ext_migrate

    ext_db.init_app(flask_app)
    ext_migrate.init_app(flask_app, ext_db, directory=str(Path(__file__).resolve().parent.parent / "migrations"))
    with flask_app.app_context():
        upgrade()

    script = str(Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py")
    monkeypatch.setattr(sys, "argv", [script, "--sqlite", str(old), "--target", f"sqlite:///{target}"])
    runpy.run_path(script, run_name="__main__")

    import sqlite3 as s3

    copied = s3.connect(target)
    assert copied.execute("SELECT id, email, password_hash FROM user").fetchall() == [(7, "old@example.com", "scrypt:32768:8:1$salt$hash")]
    assert copied.execute("SELECT id FROM meeting").fetchall() == [(3,)]
    assert copied.execute("SELECT id, status, reminder_sent FROM task ORDER BY id").fetchall() == [(1, "Completed", 1), (2, "Pending", 0)]
    assert copied.execute("SELECT id, role FROM chat_message").fetchall() == [(1, "user")]
    with pytest.raises(SystemExit, match="non-empty"):
        runpy.run_path(script, run_name="__main__")
    assert s3.connect(old).execute("SELECT COUNT(*) FROM task").fetchone()[0] == 3  # source untouched
