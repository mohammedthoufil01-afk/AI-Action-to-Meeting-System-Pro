"""Database queries for tasks, always scoped to the signed-in user."""
from sqlalchemy import func

from ..extensions import db
from ..models import Meeting, Task
from ..utils.helpers import TASK_STATUSES


def user_tasks_query(user_id):
    """All tasks belonging to meetings owned by `user_id`."""
    return Task.query.join(Meeting).filter(Meeting.user_id == user_id)


def task_counts_for_user(user_id):
    """Return {status: count} for all of a user's tasks (always contains every status)."""
    rows = (
        db.session.query(Task.status, func.count(Task.id))
        .join(Meeting)
        .filter(Meeting.user_id == user_id)
        .group_by(Task.status)
        .all()
    )
    counts = {status: 0 for status in TASK_STATUSES}
    for status, total in rows:
        if status in counts:
            counts[status] = total
    return counts
