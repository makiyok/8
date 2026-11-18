"""Webhook のテスト。署名は本物の Stripe と同じ方式（HMAC-SHA256）で作って確かめる"""

import hashlib
import hmac
import json
import time

from harvest.extensions import db
from harvest.models import Reservation, ReservationStatus
from tests.conftest import login
from tests.test_reservations import only_reservation, reserve

S = ReservationStatus
SECRET = "whsec_test"


def signed_post(client, event: dict, secret=SECRET, timestamp=None):
    payload = json.dumps(event)
    t = int(timestamp or time.time())
    sig = hmac.new(
        secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return client.post(
        "/stripe/webhook",
        data=payload,
        content_type="application/json",
        headers={"Stripe-Signature": f"t={t},v1={sig}"},
    )


def event(kind: str, session: dict) -> dict:
    return {"id": "evt_1", "object": "event", "type": kind, "data": {"object": session}}


def pending(client, customer, experience) -> Reservation:
    login(client, customer.email)
    reserve(client, experience, people=2)
    client.post("/logout")
    return only_reservation()


def test_completed_webhook_confirms(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    session = fake_payment.pay(res.stripe_session_id)
    assert (
        signed_post(client, event("checkout.session.completed", session)).status_code
        == 200
    )
    db.session.refresh(res)
    assert res.status == S.RESERVED
    # 同じ通知がもう一度来ても変わらない（冪等）
    assert (
        signed_post(client, event("checkout.session.completed", session)).status_code
        == 200
    )
    db.session.refresh(res)
    assert res.status == S.RESERVED


def test_bad_signature_rejected(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    session = fake_payment.pay(res.stripe_session_id)
    r = signed_post(
        client, event("checkout.session.completed", session), secret="whsec_wrong"
    )
    assert r.status_code == 400
    r = client.post(
        "/stripe/webhook", json=event("checkout.session.completed", session)
    )
    assert r.status_code == 400
    db.session.refresh(res)
    assert res.status == S.PENDING


def test_old_timestamp_rejected(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    session = fake_payment.pay(res.stripe_session_id)
    r = signed_post(
        client,
        event("checkout.session.completed", session),
        timestamp=time.time() - 3600,
    )
    assert r.status_code == 400


def test_unpaid_session_not_confirmed(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    session = fake_payment.retrieve_session(res.stripe_session_id)
    signed_post(client, event("checkout.session.completed", session))
    db.session.refresh(res)
    assert res.status == S.PENDING


def test_amount_mismatch_not_confirmed(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    session = dict(fake_payment.pay(res.stripe_session_id), amount_total=1)
    signed_post(client, event("checkout.session.completed", session))
    db.session.refresh(res)
    assert res.status == S.PENDING


def test_expired_webhook_releases(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    session = fake_payment.retrieve_session(res.stripe_session_id)
    signed_post(client, event("checkout.session.expired", session))
    db.session.refresh(res)
    assert res.status == S.EXPIRED


def test_payment_after_cancel_is_refunded(client, customer, fake_payment, experience):
    res = pending(client, customer, experience)
    res.status = S.CANCELLED
    db.session.commit()
    session = fake_payment.pay(res.stripe_session_id)
    signed_post(client, event("checkout.session.completed", session))
    db.session.refresh(res)
    assert res.status == S.CANCELLED
    assert res.refunded_at is not None
    # 成立していない予約への支払いは、キャンセル料なしで全額返す
    assert fake_payment.refunds == [(session["payment_intent"], res.id, res.total)]


def test_webhook_needs_secret(app, client):
    app.config["STRIPE_WEBHOOK_SECRET"] = ""
    assert client.post("/stripe/webhook", data="{}").status_code == 500
