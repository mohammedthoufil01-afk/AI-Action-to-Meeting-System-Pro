import json
import logging
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Meeting, Task
from ..services.gemini_service import AIServiceError
from ..services.meeting_analysis_service import analyze_meeting, parse_deadline
from ..services.transcription_service import transcribe_saved_audio
from ..utils.file_handler import AudioUploadError, save_audio_upload
from ..utils.helpers import DEFAULT_ASSIGNEE, utcnow
from ..utils.validators import validate_title, validate_transcript

logger = logging.getLogger(__name__)
meetings_bp = Blueprint("meetings", __name__)
CHAT_HISTORY_SHOWN = 50
DUPLICATE_WINDOW = timedelta(minutes=2)


def _form_page(form, status=200):
    return render_template("new_meeting.html", form=form), status


def _find_recent_duplicate(user_id, title, transcript):
    """Detect an accidental double submit of the same meeting."""
    return (
        Meeting.query.filter(
            Meeting.user_id == user_id,
            Meeting.title == title,
            Meeting.transcript == transcript,
            Meeting.created_at >= utcnow() - DUPLICATE_WINDOW,
        )
        .order_by(Meeting.id.desc())
        .first()
    )


@meetings_bp.route("/meeting/new", methods=["GET", "POST"])
@login_required
def new_meeting():
    if request.method == "GET":
        return _form_page({})

    config = current_app.config
    form = {"title": request.form.get("title", ""), "transcript": request.form.get("transcript", "")}
    title, error = validate_title(form["title"])
    if error:
        flash(error, "error")
        return _form_page(form, 400)

    transcript = form["transcript"]
    audio = request.files.get("audio")
    audio_name = None
    if audio and audio.filename:
        try:
            saved = save_audio_upload(audio, config["UPLOAD_FOLDER"], config["MAX_AUDIO_MB"] * 1024 * 1024)
        except AudioUploadError as exc:
            flash(str(exc), "error")
            return _form_page(form, 400)
        audio_name = saved.original_name
        try:
            transcript = transcribe_saved_audio(saved)
        except AIServiceError as exc:
            flash(f"Audio transcription failed: {exc.user_message}", "error")
            return _form_page(form, exc.status_code)
        form["transcript"] = transcript

    transcript, error = validate_transcript(
        transcript, config["MIN_TRANSCRIPT_CHARS"], config["MAX_TRANSCRIPT_CHARS"]
    )
    if error:
        flash(error, "error")
        return _form_page(form, 400)

    existing = _find_recent_duplicate(current_user.id, title, transcript)
    if existing:
        flash("This meeting was already analyzed a moment ago.", "info")
        return redirect(url_for("meetings.meeting_detail", meeting_id=existing.id))

    try:
        result = analyze_meeting(transcript)
    except AIServiceError as exc:
        message = f"AI analysis failed: {exc.user_message}"
        if audio_name:
            message += " The transcript is filled in below so you can retry without uploading again."
        flash(message, "error")
        return _form_page(form, exc.status_code)

    meeting = Meeting(
        user_id=current_user.id,
        title=title,
        transcript=transcript,
        summary=result["summary"],
        decisions_json=json.dumps(result["decisions"], ensure_ascii=False),
        topics_json=json.dumps(result["topics"], ensure_ascii=False),
        audio_filename=audio_name,
        ai_status="completed",
    )
    for item in result["action_items"]:
        meeting.tasks.append(
            Task(
                description=item["description"],
                assignee_name=item["assignee"] or DEFAULT_ASSIGNEE,
                deadline=parse_deadline(item["deadline"]),
            )
        )
    db.session.add(meeting)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not save meeting for user %s", current_user.id)
        flash("The analysis finished but the meeting could not be saved. Please try again.", "error")
        return _form_page(form, 500)
    return redirect(url_for("meetings.meeting_detail", meeting_id=meeting.id))


@meetings_bp.route("/meeting/<int:meeting_id>")
@login_required
def meeting_detail(meeting_id):
    meeting = Meeting.query.filter_by(id=meeting_id, user_id=current_user.id).first_or_404()
    history = [m for m in meeting.chat_messages if m.user_id == current_user.id][-CHAT_HISTORY_SHOWN:]
    return render_template(
        "meeting_detail.html",
        meeting=meeting,
        decisions=meeting.decisions,
        topics=meeting.topics,
        chat_history=history,
    )
