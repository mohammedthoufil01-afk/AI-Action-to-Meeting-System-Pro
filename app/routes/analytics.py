from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..utils.helpers import TASK_STATUSES
from ..services.task_service import task_counts_for_user

analytics_bp = Blueprint("analytics", __name__)


@analytics_bp.route("/analytics")
@login_required
def analytics():
    counts = task_counts_for_user(current_user.id)
    data = {"labels": list(TASK_STATUSES), "values": [counts[status] for status in TASK_STATUSES]}
    return render_template("analytics.html", data=data)
