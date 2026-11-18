"""予約の作成・支払いの確定・取り消し。金額と定員はすべてサーバーで DB から計算する。

状態の変化は、すべて「今の状態が○○なら」という条件付きの UPDATE で行う。
同じ処理が同時に2回走っても（Webhook と完了ページなど）、変わるのは1回だけになる。
"""

import logging
from datetime import date, datetime, timedelta
from enum import Enum

from sqlalchemy import and_, func, or_, select, update

from harvest.extensions import db
from harvest.models import Experience, Reservation, ReservationStatus

logger = logging.getLogger(__name__)

# 1人が同時に持てる予約（来園日がまだ来ていないもの）の上限。押さえっぱなしを防ぐ
MAX_ACTIVE_RESERVATIONS = 10
# 支払い待ちで定員を押さえておく時間（分）。Stripe の Session（31分）より長くする
HOLD_MINUTES = 35

S = ReservationStatus


class BookingError(ValueError):
    pass


def _holding(now: datetime):
    """定員を使っている予約：確定済み、または期限内の支払い待ち"""
    return or_(
        Reservation.status == S.RESERVED,
        and_(Reservation.status == S.PENDING, Reservation.hold_until > now),
    )


def booked_people(experience_id: int, visit_date: date, now: datetime) -> int:
    return db.session.scalar(
        select(func.coalesce(func.sum(Reservation.people), 0)).where(
            Reservation.experience_id == experience_id,
            Reservation.visit_date == visit_date,
            _holding(now),
        )
    )


def remaining(experience: Experience, visit_date: date, now: datetime) -> int:
    used = booked_people(experience.id, visit_date, now)
    return max(experience.daily_capacity - used, 0)


def create_reservation(
    user_id: int, experience_id: int, visit_date: date, people: int, now: datetime
) -> Reservation:
    """支払い待ちの予約を作り、定員を押さえる。条件を満たさなければ BookingError"""
    today = now.date()
    if not 1 <= people <= 20:
        raise BookingError("人数は1〜20人で選んでください")
    try:
        # 体験の行をロックしてから定員を数える（MySQL / PostgreSQL で有効。
        # SQLite は extensions.py の BEGIN IMMEDIATE で同じ効果）
        experience = db.session.scalar(
            select(Experience).where(Experience.id == experience_id).with_for_update()
        )
        if experience is None or not experience.is_published:
            raise BookingError("この体験は予約できません")
        if experience.producer_id == user_id:
            raise BookingError("自分の体験は予約できません")
        if visit_date <= today:
            raise BookingError("来園日は明日以降を選んでください")
        if not experience.in_season(visit_date):
            raise BookingError(
                f"来園日は受付期間（{experience.season_start:%Y-%m-%d}〜"
                f"{experience.season_end:%Y-%m-%d}）の中から選んでください"
            )
        mine = select(func.count()).select_from(Reservation)
        pending = db.session.scalar(
            mine.where(
                Reservation.user_id == user_id,
                Reservation.status == S.PENDING,
                Reservation.hold_until > now,
            )
        )
        if pending:
            raise BookingError(
                "支払いが済んでいない予約があります。予約一覧から支払うか、取り消してください"
            )
        active = db.session.scalar(
            mine.where(
                Reservation.user_id == user_id,
                _holding(now),
                Reservation.visit_date >= today,
            )
        )
        if active >= MAX_ACTIVE_RESERVATIONS:
            raise BookingError(f"同時に持てる予約は{MAX_ACTIVE_RESERVATIONS}件までです")
        left = remaining(experience, visit_date, now)
        if people > left:
            raise BookingError(
                f"その日の残りは{left}人分です" if left else "その日は満員です"
            )
        reservation = Reservation(
            user_id=user_id,
            experience_id=experience.id,
            visit_date=visit_date,
            people=people,
            unit_price=experience.price,
            total=experience.price * people,
            status=S.PENDING,
            hold_until=now + timedelta(minutes=HOLD_MINUTES),
        )
        db.session.add(reservation)
        db.session.commit()
        return reservation
    except BaseException:
        db.session.rollback()
        raise


def attach_session(reservation_id: int, session_id: str) -> None:
    db.session.execute(
        update(Reservation)
        .where(Reservation.id == reservation_id, Reservation.status == S.PENDING)
        .values(stripe_session_id=session_id)
    )
    db.session.commit()


class ConfirmResult(Enum):
    CONFIRMED = "confirmed"  # 今回、支払い済みにした
    ALREADY = "already"  # すでに支払い済みだった（2回目の通知など）
    NOT_PAID = "not_paid"  # まだ支払われていない
    MISMATCH = "mismatch"  # 金額や予約が合わない。確定しない
    NEEDS_REFUND = "needs_refund"  # 取り消し・期限切れのあとに支払われた。返金が必要


def confirm_payment(session: dict) -> tuple[ConfirmResult, Reservation | None]:
    """Stripe の Checkout Session（dict）を見て、予約を支払い済みにする。

    Webhook と完了ページの両方から呼ばれる。何度呼ばれても結果は同じ。
    """
    reservation = db.session.scalar(
        select(Reservation).where(Reservation.stripe_session_id == session.get("id"))
    )
    if reservation is None:
        logger.warning("知らない Checkout Session です（%s）", session.get("id"))
        return ConfirmResult.MISMATCH, None
    if session.get("payment_status") != "paid":
        return ConfirmResult.NOT_PAID, reservation
    metadata = session.get("metadata") or {}
    if (
        metadata.get("reservation_id") != str(reservation.id)
        or session.get("amount_total") != reservation.total
        or (session.get("currency") or "").lower() != "jpy"
    ):
        logger.error(
            "支払いの内容が予約と合いません（予約 %s、Session %s）",
            reservation.id,
            session.get("id"),
        )
        return ConfirmResult.MISMATCH, reservation
    result = db.session.execute(
        update(Reservation)
        .where(Reservation.id == reservation.id, Reservation.status == S.PENDING)
        .values(
            status=S.RESERVED,
            paid_at=datetime.now(),
            stripe_payment_intent_id=session.get("payment_intent"),
        )
    )
    db.session.commit()
    db.session.refresh(reservation)
    if result.rowcount == 1:
        return ConfirmResult.CONFIRMED, reservation
    if reservation.status == S.RESERVED:
        return ConfirmResult.ALREADY, reservation
    # 取り消しや期限切れのあとに支払いが届いた。お金だけ受け取らないよう返金する
    if reservation.stripe_payment_intent_id is None:
        reservation.stripe_payment_intent_id = session.get("payment_intent")
        db.session.commit()
    return ConfirmResult.NEEDS_REFUND, reservation


def mark_expired(session_id: str) -> None:
    db.session.execute(
        update(Reservation)
        .where(
            Reservation.stripe_session_id == session_id,
            Reservation.status == S.PENDING,
        )
        .values(status=S.EXPIRED)
    )
    db.session.commit()


def cancel_pending(reservation_id: int) -> bool:
    """支払い待ちの予約を取り消す（定員の押さえを外す）"""
    result = db.session.execute(
        update(Reservation)
        .where(Reservation.id == reservation_id, Reservation.status == S.PENDING)
        .values(status=S.CANCELLED)
    )
    db.session.commit()
    return result.rowcount == 1


# キャンセル規定：来園日の前日はキャンセル料50％、2日前までは無料、当日は取り消し不可
DAY_BEFORE_FEE_PERCENT = 50


def cancellation_fee(reservation: Reservation, today: date) -> int | None:
    """今取り消したときのキャンセル料（円）。取り消せない日なら None"""
    days_before = (reservation.visit_date - today).days
    if days_before <= 0:
        return None
    if days_before == 1:
        # 1円未満は切り捨て（お客さま有利）
        return reservation.total * DAY_BEFORE_FEE_PERCENT // 100
    return 0


def cancel_paid(reservation: Reservation, today: date) -> int | None:
    """確定済みの予約を取り消し、返金する額を返す。取り消せなければ None。

    返金そのものは呼び出し元で行う。条件付き UPDATE なので、二重に取り消されない
    """
    fee = cancellation_fee(reservation, today)
    if fee is None:
        return None
    refund_amount = reservation.total - fee
    result = db.session.execute(
        update(Reservation)
        .where(
            Reservation.id == reservation.id,
            Reservation.status == S.RESERVED,
            Reservation.visit_date > today,
        )
        .values(status=S.CANCELLED, cancel_fee=fee, refund_amount=refund_amount)
    )
    db.session.commit()
    return refund_amount if result.rowcount == 1 else None


def undo_cancel(reservation_id: int) -> None:
    """返金に失敗したとき、取り消しを元に戻す"""
    db.session.execute(
        update(Reservation)
        .where(
            Reservation.id == reservation_id,
            Reservation.status == S.CANCELLED,
            Reservation.refunded_at.is_(None),
        )
        .values(status=S.RESERVED, cancel_fee=None, refund_amount=None)
    )
    db.session.commit()


def mark_refunded(reservation_id: int, amount: int) -> None:
    db.session.execute(
        update(Reservation)
        .where(Reservation.id == reservation_id, Reservation.refunded_at.is_(None))
        .values(refunded_at=datetime.now(), refund_amount=amount)
    )
    db.session.commit()


def has_upcoming_reservations(experience_id: int, now: datetime) -> bool:
    return bool(
        db.session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.experience_id == experience_id,
                _holding(now),
                Reservation.visit_date >= now.date(),
            )
        )
    )
