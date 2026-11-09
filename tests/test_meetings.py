import io
import json
from datetime import datetime

from app.extensions import db
from app.models import ChatMessage, Meeting, Task
from app.services.gemini_service import AIResponseError, AIServiceError

from .conftest import LONG_TRANSCRIPT, create_meeting, create_user, login


def post_meeting(client, **overrides):
    data = {"title": "Launch Sync", "transcript": LONG_TRANSCRIPT}
    data.update(overrides)
    return client.post("/meeting/new", data=data, content_type="multipart/form-data")


def wav_upload(name="meeting.wav", payload=None):
    body = payload if payload is not None else b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
    return (io.BytesIO(body), name)


# ---------------------------------------------------------------- creation and storage
def test_new_meeting_page_loads(auth_client):
    response = auth_client.get("/meeting/new")
    assert response.status_code == 200 and b"Meeting Intelligence" in response.data


def test_meeting_creation_stores_analysis_and_tasks(auth_client, gemini, user):
    response = post_meeting(auth_client)
    assert response.status_code == 302
    meeting = Meeting.query.one()
    assert response.headers["Location"].endswith(f"/meeting/{meeting.id}")
    assert meeting.user_id == user.id and meeting.title == "Launch Sync"
    assert meeting.summary.startswith("The team agreed")
    assert json.loads(meeting.decisions_json) == ["Launch on 15 December"]
    assert json.loads(meeting.topics_json) == ["Launch plan", "Budget"]
    assert meeting.ai_status == "completed"
    tasks = Task.query.order_by(Task.id).all()
    assert [t.description for t in tasks] == ["Prepare the launch checklist", "Review the budget"]
    assert tasks[0].assignee_name == "Rahul" and tasks[0].deadline == datetime(2030, 12, 1)
    assert tasks[1].assignee_name == "Unassigned" and tasks[1].deadline is None
    assert all(t.status == "Pending" and t.reminder_sent is False for t in tasks)
    assert gemini.calls["analysis"] == 1


def test_analysis_prompt_contains_transcript_and_reference_date(auth_client, gemini):
    post_meeting(auth_client)
    assert LONG_TRANSCRIPT in gemini.last_prompt
    assert "Reference date" in gemini.last_prompt


def test_missing_title_is_rejected(auth_client, gemini):
    response = post_meeting(auth_client, title="   ")
    assert response.status_code == 400 and b"Meeting title is required" in response.data
    assert Meeting.query.count() == 0 and gemini.calls["analysis"] == 0


def test_title_too_long_is_rejected(auth_client):
    assert post_meeting(auth_client, title="x" * 201).status_code == 400


def test_empty_transcript_is_rejected(auth_client, gemini):
    response = post_meeting(auth_client, transcript="   ")
    assert response.status_code == 400 and b"Provide a transcript or audio" in response.data
    assert Meeting.query.count() == 0 and gemini.calls["analysis"] == 0


def test_very_short_transcript_is_rejected(auth_client, gemini):
    assert post_meeting(auth_client, transcript="Hi").status_code == 400
    assert gemini.calls["analysis"] == 0


def test_oversized_transcript_is_rejected(auth_client, app):
    app.config["MAX_TRANSCRIPT_CHARS"] = 100
    assert post_meeting(auth_client, transcript="word " * 100).status_code == 400


def test_long_transcript_within_limit_is_accepted(auth_client):
    assert post_meeting(auth_client, transcript="Alice said something important. " * 6000).status_code == 302


def test_transcript_above_werkzeug_default_form_size_is_accepted(auth_client, app):
    """Werkzeug's default form limit is 500 kB; the app raises it so long transcripts work."""
    app.config["MAX_TRANSCRIPT_CHARS"] = 2_000_000
    assert post_meeting(auth_client, transcript="Alice said something important. " * 32000).status_code == 302


def test_form_values_survive_validation_errors(auth_client):
    response = post_meeting(auth_client, title="Keep me", transcript="too short")
    assert b'value="Keep me"' in response.data and b"too short" in response.data


def test_double_submit_does_not_create_two_meetings_or_ai_calls(auth_client, gemini):
    first = post_meeting(auth_client)
    second = post_meeting(auth_client)
    assert first.status_code == 302 and second.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    assert Meeting.query.count() == 1 and gemini.calls["analysis"] == 1


# ---------------------------------------------------------------- AI failures
def test_ai_failure_creates_nothing_and_keeps_input(auth_client, gemini, ai_error):
    gemini.analysis_error = ai_error
    response = post_meeting(auth_client)
    assert response.status_code == 503
    assert b"AI analysis failed" in response.data and b"The AI is down." in response.data
    assert LONG_TRANSCRIPT.encode() in response.data
    assert Meeting.query.count() == 0 and Task.query.count() == 0


def test_malformed_gemini_response_does_not_crash(auth_client, gemini):
    gemini.analysis_error = AIResponseError("The AI returned an unreadable response. Please try again.")
    response = post_meeting(auth_client)
    assert response.status_code == 502 and b"unreadable response" in response.data
    assert Meeting.query.count() == 0


def test_gemini_returning_wrong_shape_is_rejected_gracefully(auth_client, gemini):
    gemini.analysis = {"summary": "", "decisions": "nope", "action_items": None}
    response = post_meeting(auth_client)
    assert response.status_code == 502 and Meeting.query.count() == 0


def test_partially_valid_gemini_data_is_normalized(auth_client, gemini):
    gemini.analysis = {
        "summary": "Summary",
        "decisions": ["Same", "same", "", 5, "Other"],
        "topics": None,
        "action_items": [
            {"description": "Valid task"},
            {"description": "", "assignee": "Bob"},
            "garbage",
            {"description": "Bad date", "assignee": "  ", "deadline": "next tuesday-ish"},
            {"description": "Valid task"},
            {"description": "Slash date", "assignee": "Bob", "deadline": "05/03/2031"},
        ],
    }
    assert post_meeting(auth_client).status_code == 302
    meeting = Meeting.query.one()
    assert json.loads(meeting.decisions_json) == ["Same", "Other"]
    assert json.loads(meeting.topics_json) == []
    tasks = Task.query.order_by(Task.id).all()
    assert [t.description for t in tasks] == ["Valid task", "Bad date", "Slash date"]
    assert tasks[0].assignee_name == "Unassigned" and tasks[0].deadline is None
    assert tasks[1].assignee_name == "Unassigned" and tasks[1].deadline is None
    assert tasks[2].deadline == datetime(2031, 3, 5)


def test_database_failure_rolls_back_and_reports(auth_client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def broken_commit():
        raise OperationalError("COMMIT", {}, Exception("db down"))

    monkeypatch.setattr(db.session, "commit", broken_commit)
    response = post_meeting(auth_client)
    monkeypatch.undo()
    assert response.status_code == 500 and b"could not be saved" in response.data
    assert Meeting.query.count() == 0 and Task.query.count() == 0


# ---------------------------------------------------------------- audio
def test_audio_upload_is_transcribed_analyzed_and_deleted(auth_client, gemini, app):
    response = post_meeting(auth_client, transcript="", audio=wav_upload())
    assert response.status_code == 302
    meeting = Meeting.query.one()
    assert meeting.transcript == gemini.transcript
    assert meeting.audio_filename == "meeting.wav"
    assert gemini.calls["audio"] == 1 and gemini.calls["analysis"] == 1
    import os
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []


def test_audio_transcription_failure_reports_and_cleans_up(auth_client, gemini, app, ai_error):
    gemini.audio_error = ai_error
    response = post_meeting(auth_client, transcript="", audio=wav_upload())
    assert response.status_code == 503 and b"Audio transcription failed" in response.data
    assert Meeting.query.count() == 0 and gemini.calls["analysis"] == 0
    import os
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []


def test_analysis_failure_after_transcription_keeps_transcript_for_retry(auth_client, gemini, ai_error):
    gemini.analysis_error = ai_error
    response = post_meeting(auth_client, transcript="", audio=wav_upload())
    assert response.status_code == 503
    assert gemini.transcript.encode() in response.data
    assert b"without uploading again" in response.data


def test_unsupported_audio_extension_is_rejected(auth_client, gemini):
    response = post_meeting(auth_client, transcript="", audio=wav_upload("notes.exe"))
    assert response.status_code == 400 and b"Unsupported audio format" in response.data
    assert gemini.calls["audio"] == 0


def test_file_with_audio_extension_but_wrong_content_is_rejected(auth_client, gemini):
    response = post_meeting(auth_client, transcript="", audio=wav_upload("fake.mp3", b"MZ this is an executable"))
    assert response.status_code == 400 and b"does not look like a valid audio" in response.data
    assert gemini.calls["audio"] == 0


def test_empty_audio_file_is_rejected(auth_client):
    response = post_meeting(auth_client, transcript="", audio=wav_upload(payload=b""))
    assert response.status_code == 400


def test_audio_larger_than_configured_limit_is_rejected_with_message(auth_client, app, gemini):
    app.config["MAX_AUDIO_MB"] = 0
    response = post_meeting(auth_client, transcript="", audio=wav_upload())
    assert response.status_code == 400 and b"too large" in response.data
    assert gemini.calls["audio"] == 0


def test_request_over_hard_limit_gets_friendly_redirect(auth_client, app):
    app.config["MAX_CONTENT_LENGTH"] = 2000
    response = post_meeting(auth_client, transcript="", audio=wav_upload(payload=b"RIFF\x24\x00\x00\x00WAVE" + b"0" * 5000))
    assert response.status_code == 302 and response.headers["Location"].endswith("/meeting/new")
    assert b"too large" in auth_client.get("/meeting/new").data


def test_filename_is_sanitized_and_never_used_as_path(auth_client, gemini, app):
    post_meeting(auth_client, transcript="", audio=wav_upload("../../etc/pass wd.wav"))
    assert Meeting.query.one().audio_filename == "etc_pass_wd.wav"


# ---------------------------------------------------------------- detail page and isolation
def test_meeting_detail_loads(auth_client, meeting):
    response = auth_client.get(f"/meeting/{meeting.id}")
    assert response.status_code == 200
    for expected in (b"Weekly Sync", b"A summary.", b"Launch on 15 December", b"Prepare checklist", b"Rahul", b"01 Dec 2030"):
        assert expected in response.data


def test_missing_meeting_returns_404(auth_client):
    assert auth_client.get("/meeting/9999").status_code == 404


def test_user_cannot_open_another_users_meeting(app, make_client, meeting):
    create_user("Bob", "bob@example.com", "password123")
    bob = make_client()
    login(bob, "bob@example.com")
    assert bob.get(f"/meeting/{meeting.id}").status_code == 404


def test_meeting_detail_escapes_html_in_ai_content(auth_client, user):
    evil = create_meeting(user, title="<script>alert(1)</script>")
    evil.summary = "<img src=x onerror=alert(1)>"
    db.session.commit()
    body = auth_client.get(f"/meeting/{evil.id}").data
    assert b"<script>alert(1)</script>" not in body and b"<img src=x" not in body
    assert b"&lt;script&gt;" in body


def test_dashboard_lists_only_own_meetings_with_stats(app, auth_client, user, make_client):
    create_meeting(user, "Mine")
    other = create_user("Bob", "bob@example.com")
    create_meeting(other, "Not mine")
    body = auth_client.get("/dashboard").data
    assert b"Mine" in body and b"Not mine" not in body
    assert b"2 actions" in body
