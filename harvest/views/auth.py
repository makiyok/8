from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash

from harvest.extensions import db
from harvest.forms import LoginForm, ProducerRegisterForm, RegisterForm
from harvest.models import Role, User
from harvest.security import clear_failures, is_locked, record_failure, safe_next

bp = Blueprint("auth", __name__)

# 存在しないメールアドレスでも同じだけ時間をかけ、応答時間で登録の有無を判別させない
_DUMMY_HASH: str | None = None


def _dummy_check(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        u = User()
        u.set_password("dummy-password-for-timing")
        _DUMMY_HASH = u.password_hash
    check_password_hash(_DUMMY_HASH, password)


def _create_user(form, role: str) -> User | None:
    email = form.email.data.strip().lower()
    if db.session.scalar(select(User).where(User.email == email)):
        # 登録済みかどうかが分からないよう、入力ミスと同じ扱いの文言にする
        form.email.errors.append("このメールアドレスでは登録できません")
        return None
    user = User(email=email, name=form.name.data.strip(), role=role)
    user.set_password(form.password.data)
    if role == Role.PRODUCER:
        user.farm_name = form.farm_name.data.strip()
        user.prefecture = form.prefecture.data
        user.farm_intro = (form.farm_intro.data or "").strip() or None
    db.session.add(user)
    db.session.commit()
    return user


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("shop.index"))
    form = RegisterForm()
    if form.validate_on_submit():
        user = _create_user(form, Role.CUSTOMER)
        if user:
            login_user(user)
            flash("会員登録が完了しました。収穫体験を探してみましょう")
            return redirect(url_for("shop.index"))
    return render_template("auth/register.html", form=form)


@bp.route("/producer/register", methods=["GET", "POST"])
def producer_register():
    if current_user.is_authenticated:
        return redirect(url_for("shop.index"))
    form = ProducerRegisterForm()
    if form.validate_on_submit():
        user = _create_user(form, Role.PRODUCER)
        if user:
            login_user(user)
            flash("生産者登録が完了しました。最初の収穫体験を出品しましょう")
            return redirect(url_for("producer.new"))
    return render_template("auth/producer_register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("shop.index"))
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        ip = request.remote_addr
        if is_locked(email, ip):
            flash("ログインの失敗が続いたため、15分ほど待ってからお試しください")
            return render_template("auth/login.html", form=form), 429
        user = db.session.scalar(select(User).where(User.email == email))
        if user is None:
            _dummy_check(form.password.data)
        if user and user.check_password(form.password.data):
            clear_failures(email)
            login_user(user)
            return redirect(
                safe_next(request.args.get("next")) or url_for("shop.index")
            )
        record_failure(email, ip)
        flash("メールアドレスかパスワードが違います")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("ログアウトしました")
    return redirect(url_for("shop.index"))
