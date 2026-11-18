"""Stripe Webhook の受け口。CSRF の対象外にするのは、このビューだけ。

CSRF トークンの代わりに、Stripe の署名（Stripe-Signature ヘッダー）で本物の通知かを確かめる。
"""

import logging

import stripe
from flask import Blueprint, abort, current_app, request

from harvest.extensions import csrf
from harvest.services import booking
from harvest.services.checkout import settle_session
from harvest.services.payment import PaymentError

bp = Blueprint("webhooks", __name__)
logger = logging.getLogger(__name__)


@bp.route("/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    secret = current_app.config["STRIPE_WEBHOOK_SECRET"]
    if not secret:
        logger.error("STRIPE_WEBHOOK_SECRET が設定されていません")
        abort(500)
    try:
        # 署名は「生の本文」で確かめる。時刻のずれの許容は既定の300秒（リプレイ対策）
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature"), secret
        ).to_dict()
    except ValueError, stripe.SignatureVerificationError:
        logger.warning("署名を確かめられない Webhook を拒否しました")
        abort(400)

    kind = event.get("type")
    session = (event.get("data") or {}).get("object") or {}
    if kind in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        try:
            settle_session(session)
        except PaymentError:
            # 返金に失敗した。500 を返すと Stripe があとで再送する
            abort(500)
    elif kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        booking.mark_expired(session.get("id", ""))
    return {"received": True}
