from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from ..models import Meeting
from ..utils.helpers import TASK_STATUSES
from ..services.task_service import task_counts_for_user

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    return redirect(url_for("auth.login"))


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    meetings = (
        Meeting.query.options(selectinload(Meeting.tasks))
        .filter_by(user_id=current_user.id)
        .order_by(Meeting.created_at.desc())
        .limit(8)
        .all()
    )
    counts = task_counts_for_user(current_user.id)
    stats = {
        "meetings": Meeting.query.filter_by(user_id=current_user.id).count(),
        "tasks": sum(counts.values()),
        "pending": counts[TASK_STATUSES[0]],
        "progress": counts[TASK_STATUSES[1]],
        "completed": counts[TASK_STATUSES[2]],
    }
    return render_template("dashboard.html", meetings=meetings, stats=stats)
