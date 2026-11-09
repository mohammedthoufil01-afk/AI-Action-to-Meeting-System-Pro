import logging

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Task
from ..services.task_service import user_tasks_query
from ..utils.helpers import is_safe_redirect
from ..utils.validators import validate_task_status

logger = logging.getLogger(__name__)
tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/tasks")
@login_required
def list_tasks():
    page = request.args.get("page", 1, type=int)
    pagination = (
        user_tasks_query(current_user.id)
        .options(joinedload(Task.meeting))
        .order_by(Task.created_at.desc(), Task.id.desc())
        .paginate(page=max(page, 1), per_page=current_app.config["TASKS_PER_PAGE"], error_out=False)
    )
    return render_template("tasks.html", tasks=pagination.items, pagination=pagination)


@tasks_bp.route("/task/<int:task_id>/status", methods=["POST"])
@login_required
def update_task(task_id):
    task = user_tasks_query(current_user.id).filter(Task.id == task_id).first_or_404()
    status, error = validate_task_status(request.form.get("status"))
    if error:
        flash(error, "error")
    else:
        task.status = status
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not update task %s", task_id)
            flash("The task could not be updated. Please try again.", "error")
    target = request.referrer
    return redirect(target if is_safe_redirect(target) else url_for("dashboard.dashboard"))
