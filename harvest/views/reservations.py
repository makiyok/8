from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from harvest.extensions import db
from harvest.forms import ReservationForm
from harvest.models import Experience, Reservation, ReservationStatus
from harvest.services import booking
from harvest.services.booking import BookingError, ConfirmResult
from harvest.services.checkout import payment_service, settle_session
from harvest.services.payment import PaymentError

bp = Blueprint("reservations", __name__, url_prefix="/reservations")

S = ReservationStatus


@bp.route("/new/<int:experience_id>", methods=["POST"])
@login_required
def create(experience_id: int):
    experience = db.session.get(Experience, experience_id)
    if experience is None or not experience.is_published:
        abort(404)
    form = ReservationForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for message in errors:
                flash(message)
        return redirect(url_for("shop.detail", experience_id=experience_id))
    try:
        # 値段や合計はフォームから受け取らず、サーバーで DB から計算する
        reservation = booking.create_reservation(
            current_user.id,
            experience_id,
            form.visit_date.data,
            form.people.data,
            now=datetime.now(),
        )
    except BookingError as e:
        flash(str(e))
        return redirect(url_for("shop.detail", experience_id=experience_id))
    try:
        session_id, url = payment_service().create_checkout_session(reservation)
    except PaymentError as e:
        # 決済ページを作れなかったら、押さえた定員をすぐに戻す
        booking.cancel_pending(reservation.id)
        flash(str(e))
        return redirect(url_for("shop.detail", experience_id=experience_id))
    booking.attach_session(reservation.id, session_id)
    return redirect(url, code=303)


def get_own_reservation_or_403(reservation_id: int) -> Reservation:
    reservation = db.session.get(Reservation, reservation_id)
    if reservation is None:
        abort(404)
    if reservation.user_id != current_user.id:
        abort(403)
    return reservation


@bp.route("/")
@login_required
def index():
    items = db.session.scalars(
        select(Reservation)
        .where(Reservation.user_id == current_user.id)
        .options(selectinload(Reservation.experience).selectinload(Experience.photos))
        .order_by(Reservation.visit_date.desc(), Reservation.id.desc())
    ).all()
    return render_template("reservations/index.html", items=items, today=date.today())


@bp.route("/<int:reservation_id>")
@login_required
def show(reservation_id: int):
    reservation = get_own_reservation_or_403(reservation_id)
    today = date.today()
    fee = None
    if reservation.status == S.RESERVED:
        fee = booking.cancellation_fee(reservation, today)
    return render_template(
        "reservations/show.html",
        r=reservation,
        today=today,
        fee=fee,
        fee_percent=booking.DAY_BEFORE_FEE_PERCENT,
    )


@bp.route("/<int:reservation_id>/complete")
@login_required
def complete(reservation_id: int):
    """Stripe の決済ページから戻ってくる画面。Webhook より先に来ることもあるので、ここでも確かめる"""
    reservation = get_own_reservation_or_403(reservation_id)
    session_id = request.args.get("session_id", "")
    if not session_id or session_id != reservation.stripe_session_id:
        # URL の session_id は書き換えられるので、保存してある ID と一致するものだけ信じる
        abort(404)
    if reservation.status == S.PENDING:
        try:
            result = settle_session(payment_service().retrieve_session(session_id))
        except PaymentError as e:
            flash(str(e))
            return redirect(url_for("reservations.show", reservation_id=reservation.id))
        if result is ConfirmResult.NOT_PAID:
            flash("支払いの確認を待っています。少し待ってから画面を更新してください")
        elif result is ConfirmResult.MISMATCH:
            flash("支払いの内容を確認できませんでした。お問い合わせください")
        elif result is ConfirmResult.NEEDS_REFUND:
            flash("予約の期限が切れていたため、お支払いは返金しました")
    db.session.refresh(reservation)
    if reservation.status == S.RESERVED:
        flash("お支払いが完了し、予約が確定しました")
    return redirect(url_for("reservations.show", reservation_id=reservation.id))


@bp.route("/<int:reservation_id>/pay", methods=["POST"])
@login_required
def pay(reservation_id: int):
    """支払い待ちの予約の決済ページに戻る"""
    reservation = get_own_reservation_or_403(reservation_id)
    back = redirect(url_for("reservations.show", reservation_id=reservation.id))
    if not reservation.is_payable or not reservation.stripe_session_id:
        flash("この予約はもう支払えません。もう一度予約してください")
        return back
    try:
        session = payment_service().retrieve_session(reservation.stripe_session_id)
    except PaymentError as e:
        flash(str(e))
        return back
    if session.get("status") == "open" and session.get("url"):
        return redirect(session["url"], code=303)
    settle_session(session)
    flash("決済ページの期限が切れています。もう一度予約してください")
    return back


@bp.route("/<int:reservation_id>/cancel", methods=["POST"])
@login_required
def cancel(reservation_id: int):
    reservation = get_own_reservation_or_403(reservation_id)
    back = redirect(url_for("reservations.show", reservation_id=reservation.id))

    if reservation.status == S.PENDING:
        if reservation.stripe_session_id and reservation.is_payable:
            try:
                # 先に決済ページを閉じる。閉じる前に支払われた場合は Stripe がエラーを返す
                payment_service().expire_session(reservation.stripe_session_id)
            except PaymentError:
                flash(
                    "支払いの手続き中のため、取り消せませんでした。少し待ってからお試しください"
                )
                return back
        booking.cancel_pending(reservation.id)
        flash("予約を取り消しました")
        return back

    # キャンセル料は、サーバーで来園日と今日の日付から計算する
    refund_amount = booking.cancel_paid(reservation, today=date.today())
    if refund_amount is None:
        flash("この予約は取り消せません（来園日の前日まで取り消せます）")
        return back
    if reservation.stripe_payment_intent_id and refund_amount > 0:
        try:
            payment_service().refund(
                reservation.stripe_payment_intent_id, reservation.id, refund_amount
            )
        except PaymentError as e:
            booking.undo_cancel(reservation.id)
            flash(str(e))
            return back
        booking.mark_refunded(reservation.id, refund_amount)
    fee = reservation.total - refund_amount
    if fee:
        flash(
            f"予約を取り消しました。キャンセル料 {fee:,}円 を差し引き、"
            f"{refund_amount:,}円 を返金します（反映まで数日かかります）"
        )
    else:
        flash(
            "予約を取り消しました。お支払いは全額返金します（反映まで数日かかります）"
        )
    return back
