from ..extensions import db
from ..utils.helpers import DEFAULT_ASSIGNEE, TASK_STATUSES, utcnow


class Task(db.Model):
    __tablename__ = "task"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('Pending', 'In Progress', 'Completed')", name="ck_task_status"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(
        db.Integer, db.ForeignKey("meeting.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description = db.Column(db.String(700), nullable=False)
    assignee_name = db.Column(db.String(150), default=DEFAULT_ASSIGNEE)
    deadline = db.Column(db.DateTime)
    status = db.Column(db.String(30), default=TASK_STATUSES[0], index=True)
    reminder_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)
