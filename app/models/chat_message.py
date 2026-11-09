from ..extensions import db
from ..utils.helpers import utcnow


class ChatMessage(db.Model):
    __tablename__ = "chat_message"
    __table_args__ = (
        db.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_message_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(
        db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
