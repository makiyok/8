from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request
from flask_login import current_user
from sqlalchemy import and_, extract, or_, select
from sqlalchemy.orm import selectinload

from harvest.categories import CATEGORIES
from harvest.extensions import db
from harvest.forms import ReservationForm, SearchForm
from harvest.models import Experience

bp = Blueprint("shop", __name__)


def _published():
    return (
        select(Experience)
        .where(Experience.is_published.is_(True))
        .options(selectinload(Experience.photos), selectinload(Experience.producer))
    )


def _in_month(month: int):
    """受付期間がその月にかかる体験（年をまたぐ期間も考える）"""
    start_m = extract("month", Experience.season_start)
    end_m = extract("month", Experience.season_end)
    same_year = extract("year", Experience.season_start) == extract(
        "year", Experience.season_end
    )
    return or_(
        and_(same_year, start_m <= month, end_m >= month),
        and_(~same_year, or_(start_m <= month, end_m >= month)),
    )


@bp.route("/")
def index():
    today = date.today()
    in_season = db.session.scalars(
        _published()
        .where(Experience.season_start <= today, Experience.season_end >= today)
        .order_by(Experience.created_at.desc())
        .limit(8)
    ).all()
    newest = db.session.scalars(
        _published().order_by(Experience.created_at.desc()).limit(8)
    ).all()
    return render_template("shop/index.html", in_season=in_season, newest=newest)


@bp.route("/experiences")
def experiences():
    form = SearchForm(request.args)
    stmt = _published()
    if form.validate():
        if form.category.data:
            stmt = stmt.where(Experience.category == form.category.data)
        if form.prefecture.data:
            stmt = stmt.where(Experience.prefecture == form.prefecture.data)
        if form.month.data:
            stmt = stmt.where(_in_month(int(form.month.data)))
        if form.q.data:
            # LIKE の特殊文字をエスケープしてから部分一致で探す
            q = form.q.data.strip()
            q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Experience.title.like(pattern, escape="\\"),
                    Experience.description.like(pattern, escape="\\"),
                    Experience.address.like(pattern, escape="\\"),
                )
            )
    items = db.session.scalars(
        stmt.order_by(Experience.season_start, Experience.id).limit(60)
    ).all()
    active_category = CATEGORIES.get(form.category.data or "")
    return render_template(
        "shop/list.html", form=form, items=items, active_category=active_category
    )


@bp.route("/experiences/<int:experience_id>")
def detail(experience_id: int):
    experience = db.session.get(Experience, experience_id)
    if experience is None:
        abort(404)
    if not experience.is_published and not (
        current_user.is_authenticated and current_user.id == experience.producer_id
    ):
        # 非公開の体験は、出品した本人以外には存在しないことにする
        abort(404)
    today = date.today()
    form = ReservationForm()
    form.people.data = 1
    # 予約できる最初の日（明日か、受付開始日の遅いほう）
    min_date = max(experience.season_start, today + timedelta(days=1))
    if min_date <= experience.season_end:
        form.visit_date.data = min_date
    return render_template(
        "shop/detail.html",
        experience=experience,
        form=form,
        today=today,
        min_date=min_date,
    )
