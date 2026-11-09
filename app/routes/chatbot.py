import logging

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import ChatMessage, Meeting
from ..services.chat_service import answer_question
from ..services.gemini_service import AIServiceError
from ..utils.validators import validate_chat_message

logger = logging.getLogger(__name__)
chatbot_bp = Blueprint("chatbot", __name__)


@chatbot_bp.route("/chat/<int:meeting_id>", methods=["POST"])
@login_required
def chat(meeting_id):
    meeting = Meeting.query.filter_by(id=meeting_id, user_id=current_user.id).first()
    if meeting is None:
        return jsonify(error="Meeting not found."), 404

    payload = request.get_json(silent=True)
    raw_message = payload.get("message") if isinstance(payload, dict) else None
    question, error = validate_chat_message(raw_message, current_app.config["MAX_CHAT_MESSAGE_CHARS"])
    if error:
        return jsonify(error=error), 400

    try:
        answer = answer_question(meeting, current_user.id, question)
    except AIServiceError as exc:
        return jsonify(error=exc.user_message), exc.status_code

    db.session.add_all(
        [
            ChatMessage(meeting_id=meeting.id, user_id=current_user.id, role="user", content=question),
            ChatMessage(meeting_id=meeting.id, user_id=current_user.id, role="assistant", content=answer),
        ]
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not store chat messages for meeting %s", meeting.id)
    return jsonify(answer=answer)
