import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..extensions import db
from ..models import User
from ..utils.helpers import is_safe_redirect
from ..utils.validators import validate_email_address, validate_name, validate_password

logger = logging.getLogger(__name__)
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first() if email else None
        if user and user.check_password(password):
            login_user(user, remember=True)
            target = request.args.get("next")
            return redirect(target if is_safe_redirect(target) else url_for("dashboard.dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))
    if request.method == "POST":
        name, name_error = validate_name(request.form.get("name"))
        email, email_error = validate_email_address(request.form.get("email"))
        password, password_error = validate_password(request.form.get("password"))
        error = name_error or email_error or password_error
        if error:
            flash(error, "error")
            return render_template("signup.html", form=request.form), 400
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("signup.html", form=request.form), 409
        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An account with that email already exists.", "error")
            return render_template("signup.html", form=request.form), 409
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not create user account")
            flash("We could not create your account right now. Please try again.", "error")
            return render_template("signup.html", form=request.form), 500
        login_user(user)
        return redirect(url_for("dashboard.dashboard"))
    return render_template("signup.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
