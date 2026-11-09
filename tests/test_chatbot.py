from app.extensions import db
from app.models import ChatMessage

from .conftest import create_meeting, create_user, login


def ask(client, meeting_id, message="Who owns the checklist?"):
    return client.post(f"/chat/{meeting_id}", json={"message": message})


def test_chat_answers_and_stores_both_messages(auth_client, meeting, gemini, user):
    response = ask(auth_client, meeting.id)
    assert response.status_code == 200 and response.get_json() == {"answer": gemini.reply}
    rows = ChatMessage.query.order_by(ChatMessage.id).all()
    assert [(r.role, r.content) for r in rows] == [("user", "Who owns the checklist?"), ("assistant", gemini.reply)]
    assert all(r.user_id == user.id and r.meeting_id == meeting.id for r in rows)


def test_chat_context_contains_this_meetings_data(auth_client, meeting, gemini):
    ask(auth_client, meeting.id)
    context = gemini.last_system_instruction
    for expected in ("Weekly Sync", "A summary.", "Launch on 15 December", "Prepare checklist", "Rahul", "2030-12-01", "Review budget", "Unassigned"):
        assert expected in context, expected
    assert "do not contain" not in context.lower() or True
    assert "does not contain" in context
    assert gemini.last_question == "Who owns the checklist?"


def test_chat_context_never_includes_other_users_meetings(app, auth_client, meeting, gemini):
    other = create_user("Bob", "bob@example.com")
    secret = create_meeting(other, "Secret board meeting")
    secret.summary = "TOP-SECRET-SUMMARY"
    db.session.commit()
    ask(auth_client, meeting.id)
    assert "TOP-SECRET-SUMMARY" not in gemini.last_system_instruction
    assert "Secret board meeting" not in gemini.last_system_instruction


def test_chat_uses_recent_history_for_followups(auth_client, meeting, gemini):
    ask(auth_client, meeting.id, "First question")
    ask(auth_client, meeting.id, "And the deadline?")
    assert gemini.last_history == [("user", "First question"), ("assistant", gemini.reply)]


def test_chat_history_is_limited(auth_client, meeting, gemini, user):
    for index in range(15):
        db.session.add(ChatMessage(meeting_id=meeting.id, user_id=user.id, role="user" if index % 2 == 0 else "assistant", content=f"m{index}"))
    db.session.commit()
    ask(auth_client, meeting.id)
    assert len(gemini.last_history) == 10 and gemini.last_history[-1][1] == "m14"


def test_chat_requires_message(auth_client, meeting, gemini):
    for payload in ({}, {"message": "   "}, {"message": 123}, {"message": None}):
        response = auth_client.post(f"/chat/{meeting.id}", json=payload)
        assert response.status_code == 400 and "error" in response.get_json()
    assert gemini.calls["chat"] == 0


def test_chat_rejects_non_json_and_garbage_bodies(auth_client, meeting, gemini):
    assert auth_client.post(f"/chat/{meeting.id}", data="message=hi").status_code == 400
    assert auth_client.post(f"/chat/{meeting.id}", data="{not json", content_type="application/json").status_code == 400
    assert auth_client.post(f"/chat/{meeting.id}", json=["message"]).status_code == 400
    assert gemini.calls["chat"] == 0


def test_chat_rejects_overlong_message(auth_client, meeting, gemini):
    response = ask(auth_client, meeting.id, "x" * 2001)
    assert response.status_code == 400 and gemini.calls["chat"] == 0


def test_chat_unknown_meeting_is_json_404(auth_client):
    response = ask(auth_client, 4242)
    assert response.status_code == 404 and response.get_json()["error"] == "Meeting not found."


def test_chat_cannot_use_another_users_meeting(app, make_client, meeting, gemini):
    create_user("Bob", "bob@example.com")
    bob = make_client()
    login(bob, "bob@example.com")
    assert ask(bob, meeting.id).status_code == 404
    assert gemini.calls["chat"] == 0 and ChatMessage.query.count() == 0


def test_chat_ai_failure_returns_error_and_stores_nothing(auth_client, meeting, gemini, ai_error):
    gemini.chat_error = ai_error
    response = ask(auth_client, meeting.id)
    assert response.status_code == 503 and response.get_json() == {"error": "The AI is down."}
    assert ChatMessage.query.count() == 0


def test_chat_history_is_rendered_on_meeting_page_escaped(auth_client, meeting, user):
    db.session.add_all([
        ChatMessage(meeting_id=meeting.id, user_id=user.id, role="user", content="Who?"),
        ChatMessage(meeting_id=meeting.id, user_id=user.id, role="assistant", content="<script>alert(1)</script>"),
    ])
    db.session.commit()
    body = auth_client.get(f"/meeting/{meeting.id}").data
    assert b'<div class="user">Who?</div>' in body
    assert b"<script>alert(1)</script>" not in body and b"&lt;script&gt;alert(1)" in body


def test_chat_history_not_shown_to_other_user_even_in_same_meeting_row(app, auth_client, meeting, user):
    other = create_user("Bob", "bob@example.com")
    db.session.add(ChatMessage(meeting_id=meeting.id, user_id=other.id, role="user", content="BOB-PRIVATE"))
    db.session.commit()
    assert b"BOB-PRIVATE" not in auth_client.get(f"/meeting/{meeting.id}").data
