from datetime import date, datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from harvest.decorators import role_required
from harvest.extensions import db
from harvest.forms import ExperienceForm
from harvest.models import (
    Experience,
    ExperiencePhoto,
    Reservation,
    ReservationStatus,
    Role,
)
from harvest.services.booking import has_upcoming_reservations
from harvest.uploads import ImageError, delete_image, save_image

bp = Blueprint("producer", __name__, url_prefix="/producer")

FIELDS = (
    "title", "category", "description", "prefecture", "address",
    "season_start", "season_end", "hours", "take_home_amount",
    "take_home_unit", "price", "daily_capacity", "is_published",
)  # fmt: skip


def get_own_experience_or_403(experience_id: int) -> Experience:
    experience = db.session.get(Experience, experience_id)
    if experience is None:
        abort(404)
    if experience.producer_id != current_user.id:
        abort(403)
    return experience


def _uploaded_files(form):
    return [f for f in (form.photos.data or []) if f and f.filename]


def _save_photos(experience: Experience, files) -> bool:
    """写真を保存する。枚数や中身の問題があればフォームにエラーを出して False"""
    limit = current_app.config["MAX_PHOTOS"]
    if len(experience.photos) + len(files) > limit:
        return False
    saved = []
    try:
        for f in files:
            saved.append(save_image(f.read()))
    except ImageError:
        for name in saved:
            delete_image(name)
        raise
    start = max((p.position for p in experience.photos), default=-1) + 1
    for i, name in enumerate(saved):
        experience.photos.append(ExperiencePhoto(filename=name, position=start + i))
    return True


def _handle_form(form: ExperienceForm, experience: Experience, *, is_new: bool):
    files = _uploaded_files(form)
    limit = current_app.config["MAX_PHOTOS"]
    if len(experience.photos) + len(files) > limit:
        form.photos.errors.append(f"写真は全部で{limit}枚までです")
        return None
    for name in FIELDS:
        setattr(experience, name, getattr(form, name).data)
    if is_new:
        experience.producer_id = current_user.id  # 出品者はフォームからは受け取らない
        db.session.add(experience)
    try:
        _save_photos(experience, files)
    except ImageError as e:
        db.session.rollback()
        form.photos.errors.append(str(e))
        return None
    db.session.commit()
    return experience


@bp.route("/")
@role_required(Role.PRODUCER)
def index():
    items = db.session.scalars(
        select(Experience)
        .where(Experience.producer_id == current_user.id)
        .options(selectinload(Experience.photos))
        .order_by(Experience.created_at.desc())
    ).all()
    return render_template("producer/index.html", items=items)


@bp.route("/experiences/new", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def new():
    form = ExperienceForm()
    if not form.is_submitted():
        form.prefecture.data = current_user.prefecture
    if form.validate_on_submit():
        experience = _handle_form(form, Experience(), is_new=True)
        if experience:
            flash("収穫体験を登録しました")
            return redirect(url_for("producer.index"))
    return render_template("producer/form.html", form=form, experience=None)


@bp.route("/experiences/<int:experience_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(experience_id: int):
    experience = get_own_experience_or_403(experience_id)
    form = ExperienceForm(obj=experience)
    if form.validate_on_submit() and _handle_form(form, experience, is_new=False):
        flash("収穫体験を更新しました")
        return redirect(url_for("producer.edit", experience_id=experience.id))
    return render_template("producer/form.html", form=form, experience=experience)


@bp.route("/photos/<int:photo_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete_photo(photo_id: int):
    photo = db.session.get(ExperiencePhoto, photo_id)
    if photo is None:
        abort(404)
    experience = get_own_experience_or_403(photo.experience_id)
    filename = photo.filename
    db.session.delete(photo)
    db.session.commit()
    delete_image(filename)
    flash("写真を削除しました")
    return redirect(url_for("producer.edit", experience_id=experience.id))


@bp.route("/experiences/<int:experience_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete(experience_id: int):
    experience = get_own_experience_or_403(experience_id)
    if has_upcoming_reservations(experience.id, datetime.now()):
        flash("これからの予約がある体験は削除できません。非公開にしてください")
        return redirect(url_for("producer.edit", experience_id=experience.id))
    if experience.reservations:
        # 過去の予約の記録を残すため、削除ではなく非公開にする
        experience.is_published = False
        db.session.commit()
        flash("過去の予約があるため、削除せず非公開にしました")
        return redirect(url_for("producer.index"))
    filenames = [p.filename for p in experience.photos]
    db.session.delete(experience)
    db.session.commit()
    for name in filenames:
        delete_image(name)
    flash("収穫体験を削除しました")
    return redirect(url_for("producer.index"))


@bp.route("/reservations")
@role_required(Role.PRODUCER)
def reservations():
    items = db.session.scalars(
        select(Reservation)
        .join(Experience)
        .where(
            Experience.producer_id == current_user.id,
            Reservation.status == ReservationStatus.RESERVED,
            Reservation.visit_date >= date.today(),
        )
        .options(selectinload(Reservation.user), selectinload(Reservation.experience))
        .order_by(Reservation.visit_date, Reservation.id)
    ).all()
    return render_template("producer/reservations.html", items=items)
