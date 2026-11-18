"""決済サービス（Stripe）と予約（DB）をつなぐ処理。Webhook と画面の両方から使う"""

import logging

from flask import current_app

from harvest.services import booking
from harvest.services.booking import ConfirmResult

logger = logging.getLogger(__name__)


def payment_service():
    return current_app.extensions["payment"]


def settle_session(session: dict) -> ConfirmResult:
    """Checkout Session の結果を予約に反映する。

    取り消し・期限切れのあとに支払いが届いたときは返金する。
    返金に失敗したら PaymentError をそのまま投げる（Webhook は 500 を返し、Stripe が再送する）
    """
    result, reservation = booking.confirm_payment(session)
    if result is ConfirmResult.NEEDS_REFUND and reservation.refunded_at is None:
        logger.warning("無効な予約への支払いを返金します（予約 %s）", reservation.id)
        # 予約として成立していない支払いなので、キャンセル料は取らず全額返す
        payment_service().refund(
            reservation.stripe_payment_intent_id, reservation.id, reservation.total
        )
        booking.mark_refunded(reservation.id, reservation.total)
    return result
