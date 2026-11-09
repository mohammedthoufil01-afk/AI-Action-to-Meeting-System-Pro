"""Email reminders for tasks that are due soon, plus the background scheduler that runs them."""
import logging
from collections import defaultdict
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask_mailman import EmailMessage
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Meeting, Task
from ..utils.helpers import utcnow

logger = logging.getLogger(__name__)
REMINDER_WINDOW_HOURS = 24
_scheduler = None


def mail_is_configured(config):
    return bool(config.get("MAIL_SERVER") and config.get("MAIL_DEFAULT_SENDER"))


def _build_message(config, owner, tasks):
    lines = [f"Hi {owner.name},", "", "These action items are due within 24 hours or are overdue:", ""]
    for task in tasks:
        due = task.deadline.strftime("%d %b %Y") if task.deadline else "no date"
        lines.append(f"- {task.description} (meeting: {task.meeting.title}, owner: {task.assignee_name}, due {due})")
    lines += ["", "Open NEXUS to update their status."]
    return EmailMessage(
        subject=f"NEXUS reminder: {len(tasks)} action item(s) need attention",
        body="\n".join(lines),
        from_email=config["MAIL_DEFAULT_SENDER"],
        to=[owner.email],
    )


def send_due_reminders(app):
    """Email each meeting owner a digest of due/overdue tasks. Returns the number of emails sent."""
    with app.app_context():
        if not mail_is_configured(app.config):
            logger.info("Reminders skipped: mail is not configured")
            return 0
        window_end = utcnow() + timedelta(hours=REMINDER_WINDOW_HOURS)
        due_tasks = (
            Task.query.options(joinedload(Task.meeting).joinedload(Meeting.owner))
            .filter(
                Task.status != "Completed",
                Task.reminder_sent.is_(False),
                Task.deadline.isnot(None),
                Task.deadline <= window_end,
            )
            .order_by(Task.deadline)
            .all()
        )
        by_owner = defaultdict(list)
        for task in due_tasks:
            by_owner[task.meeting.owner].append(task)

        sent = 0
        for owner, tasks in by_owner.items():
            try:
                _build_message(app.config, owner, tasks).send()
            except Exception:  # noqa: BLE001 - one failing mailbox must not stop the others
                logger.exception("Could not send reminder email to user %s", owner.id)
                continue
            for task in tasks:
                task.reminder_sent = True
            try:
                db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()
                logger.exception("Reminder was sent but could not be recorded for user %s", owner.id)
                continue
            sent += 1
        return sent


def start_scheduler(app):
    """Start one background scheduler for this process (no-op if disabled or already running)."""
    global _scheduler
    if not app.config.get("SCHEDULER_ENABLED") or app.config.get("TESTING"):
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        send_due_reminders,
        "interval",
        args=[app],
        minutes=app.config["REMINDER_INTERVAL_MINUTES"],
        id="nexus-reminders",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Reminder scheduler started (every %s minutes)", app.config["REMINDER_INTERVAL_MINUTES"])
    return scheduler
