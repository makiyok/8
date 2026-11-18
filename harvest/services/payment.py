"""Stripe を呼ぶのは、このファイルだけ。

ビューは `current_app.extensions["payment"]` 経由で呼ぶ。テストではフェイクに差し替える。
Stripe のオブジェクトは dict ではないので、呼び出し元には to_dict() した dict を返す。
"""

import logging
import time

import stripe
from flask import current_app, url_for

from harvest.models import Reservation

logger = logging.getLogger(__name__)

# Checkout Session の有効期限（分）。Stripe の最短は30分なので、時計のずれを見込んで31分。
# 予約の押さえ（booking.HOLD_MINUTES）は、これより長くしておく
SESSION_MINUTES = 31


class PaymentError(Exception):
    """決済サービスの失敗。メッセージは、そのまま画面に出せる日本語"""


def _client() -> stripe.StripeClient:
    key = current_app.config["STRIPE_SECRET_KEY"]
    if not key:
        raise PaymentError("決済の設定がされていません（STRIPE_SECRET_KEY）")
    return stripe.StripeClient(key)


def create_checkout_session(reservation: Reservation) -> tuple[str, str]:
    """Checkout Session を作り、(session_id, 決済ページの URL) を返す。

    金額は予約（DB の値）から取る。JPY は小数のない通貨なので、unit_amount は円のまま。
    """
    experience = reservation.experience
    params = {
        "mode": "payment",
        "line_items": [
            {
                "quantity": reservation.people,
                "price_data": {
                    "currency": "jpy",
                    "unit_amount": reservation.unit_price,
                    "product_data": {
                        "name": experience.title,
                        "description": (
                            f"来園日 {reservation.visit_date:%Y-%m-%d}・"
                            f"1人 {experience.take_home_amount}"
                            f"{experience.take_home_unit}まで持ち帰り"
                        ),
                    },
                },
            }
        ],
        "customer_email": reservation.user.email,
        "client_reference_id": str(reservation.id),
        "metadata": {"reservation_id": str(reservation.id)},
        # {CHECKOUT_SESSION_ID} は、Stripe が Session の ID に置き換える
        "success_url": url_for(
            "reservations.complete", reservation_id=reservation.id, _external=True
        )
        + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": url_for(
            "reservations.show", reservation_id=reservation.id, _external=True
        ),
        "expires_at": int(time.time()) + SESSION_MINUTES * 60,
    }
    try:
        session = _client().v1.checkout.sessions.create(
            params,
            # 同じ予約で二重に Session を作らない
            options={"idempotency_key": f"checkout-reservation-{reservation.id}"},
        )
    except stripe.StripeError as error:
        logger.exception(
            "Checkout Session を作れませんでした（予約 %s）", reservation.id
        )
        raise PaymentError(
            "決済ページを作れませんでした。時間をおいて、もう一度お試しください"
        ) from error
    return session.id, session.url


def retrieve_session(session_id: str) -> dict:
    try:
        return _client().v1.checkout.sessions.retrieve(session_id).to_dict()
    except stripe.StripeError as error:
        logger.exception("Checkout Session を取得できませんでした（%s）", session_id)
        raise PaymentError(
            "支払いの状態を確認できませんでした。時間をおいて、もう一度お試しください"
        ) from error


def expire_session(session_id: str) -> None:
    """Checkout Session を閉じる。すでに支払い済みなら Stripe がエラーを返す"""
    try:
        _client().v1.checkout.sessions.expire(session_id)
    except stripe.StripeError as error:
        logger.exception("Checkout Session を閉じられませんでした（%s）", session_id)
        raise PaymentError(
            "決済ページを閉じられませんでした。時間をおいて、もう一度お試しください"
        ) from error


def refund(payment_intent_id: str, reservation_id: int, amount: int) -> None:
    """amount 円を返金する（JPY なので円のまま）。

    同じ予約・同じ額で二重に返金しないよう idempotency_key を付ける
    """
    try:
        _client().v1.refunds.create(
            {"payment_intent": payment_intent_id, "amount": amount},
            options={
                "idempotency_key": f"refund-reservation-{reservation_id}-{amount}"
            },
        )
    except stripe.StripeError as error:
        logger.exception("返金できませんでした（予約 %s）", reservation_id)
        raise PaymentError(
            "返金の手続きができませんでした。時間をおいて、もう一度お試しください"
        ) from error
