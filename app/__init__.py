"""NEXUS AI application factory."""
import logging
import os

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from config import Config
from .extensions import csrf, db, login_manager, mail, migrate

WEAK_SECRET_KEYS = {"", "dev-change-me", "change-this-to-a-long-random-secret"}


def _configure_logging(app):
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.DEBUG if app.debug else logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


def _check_secret_key(app):
    if app.config["SECRET_KEY"] in WEAK_SECRET_KEYS:
        if app.config["APP_ENV"] == "production":
            raise RuntimeError("SECRET_KEY must be set to a long random value in production.")
        app.logger.warning("SECRET_KEY is not set; using an insecure development key.")
        app.config["SECRET_KEY"] = "insecure-development-key"


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    """SQLite ignores foreign keys (and ON DELETE CASCADE) unless this is switched on."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_app(config_object=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    _configure_logging(app)
    _check_secret_key(app)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    from .routes import ALL_BLUEPRINTS

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    _register_error_handlers(app)
    _register_response_headers(app)
    _register_cli(app)

    _prepare_local_database(app)
    return app


def _prepare_local_database(app):
    """Tests build tables directly; the SQLite fallback applies migrations on startup.

    PostgreSQL/Supabase is never changed automatically: run `flask db upgrade` yourself.
    """
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if app.config["TESTING"]:
        with app.app_context():
            db.create_all()
    elif uri.startswith("sqlite"):
        from flask_migrate import upgrade

        try:
            with app.app_context():
                upgrade()
        except Exception:  # noqa: BLE001 - startup should explain the problem instead of a stack trace
            app.logger.exception(
                "Could not apply database migrations to the SQLite file. If it was created by the original "
                "NEXUS version, move it away (instance/nexus.db) or follow the README migration steps."
            )
            raise


def _wants_json():
    return request.is_json or request.path.startswith("/chat/")


def _register_error_handlers(app):
    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        limit = app.config["MAX_AUDIO_MB"]
        message = f"The upload is too large. The maximum audio size is {limit} MB."
        if _wants_json():
            return jsonify(error=message), 413
        flash(message, "error")
        if current_user.is_authenticated:
            return redirect(url_for("meetings.new_meeting"))
        return redirect(url_for("auth.login"))

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code == 400 and "CSRF" in (error.description or ""):
            message = "Your session expired or the form was invalid. Please reload the page and try again."
        else:
            message = {
                403: "You do not have permission to view this page.",
                404: "That page could not be found.",
                405: "That action is not allowed here.",
            }.get(error.code, error.description or "Something went wrong.")
        if _wants_json():
            return jsonify(error=message), error.code
        return render_template("error.html", code=error.code, message=message), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Unhandled error: %s", error)
        db.session.rollback()
        if _wants_json():
            return jsonify(error="An unexpected error occurred. Please try again."), 500
        return render_template("error.html", code=500, message="An unexpected error occurred."), 500


def _register_response_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.endpoint and request.endpoint != "static":
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _register_cli(app):
    @app.cli.command("send-reminders")
    def send_reminders_command():
        """Send due-task reminder emails once (useful for cron / Task Scheduler)."""
        from .services.reminder_service import send_due_reminders

        print(f"Reminder emails sent: {send_due_reminders(app)}")
