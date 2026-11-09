from ..extensions import db
from ..utils.helpers import load_json_list, utcnow


class Meeting(db.Model):
    __tablename__ = "meeting"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = db.Column(db.String(200), nullable=False)
    meeting_date = db.Column(db.DateTime, default=utcnow)
    audio_filename = db.Column(db.String(255))
    transcript = db.Column(db.Text, default="")
    summary = db.Column(db.Text, default="")
    decisions_json = db.Column(db.Text, default="[]")
    topics_json = db.Column(db.Text, default="[]")
    ai_status = db.Column(db.String(40), default="pending")
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    tasks = db.relationship(
        "Task",
        backref="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Task.id",
    )
    chat_messages = db.relationship(
        "ChatMessage",
        backref="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.id",
    )

    @property
    def decisions(self):
        return load_json_list(self.decisions_json)

    @property
    def topics(self):
        return load_json_list(self.topics_json)
