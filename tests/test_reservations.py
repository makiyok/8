from datetime import date, datetime, timedelta

from harvest.extensions import db
from harvest.models import Reservation, ReservationStatus
from harvest.services.payment import PaymentError
from tests.conftest import login

S = ReservationStatus


def tomorrow():
    return (date.today() + timedelta(days=1)).isoformat()


def reserve(client, experience, people=2, visit_date=None, **extra):
    data = {"visit_date": visit_date or tomorrow(), "people": str(people), **extra}
    return client.post(f"/reservations/new/{experience.id}", data=data)


def only_reservation():
    return db.session.scalar(db.select(Reservation).order_by(Reservation.id.desc()))


def count():
    return db.session.scalar(db.select(db.func.count()).select_from(Reservation))


def paid_reservation(client, fake_payment, experience, people=2, days_ahead=1):
    visit = (date.today() + timedelta(days=days_ahead)).isoformat()
    reserve(client, experience, people=people, visit_date=visit)
    r = only_reservation()
    fake_payment.pay(r.stripe_session_id)
    client.get(f"/reservations/{r.id}/complete?session_id={r.stripe_session_id}")
    db.session.refresh(r)
    return r


def test_reserve_redirects_to_stripe_with_server_price(client, customer, experience):
    login(client, customer.email)
    r = reserve(client, experience, people=2, price="1", total="1")
    assert r.status_code == 303
    assert r.headers["Location"].startswith("https://checkout.stripe.com/")
    res = only_reservation()
    assert res.status == S.PENDING
    assert res.unit_price == 800 and res.total == 1600
    assert res.stripe_session_id == f"cs_test_{res.id}"


def test_complete_page_confirms_payment(client, customer, fake_payment, experience):
    login(client, customer.email)
    res = paid_reservation(client, fake_payment, experience)
    assert res.status == S.RESERVED
    assert res.paid_at is not None
    assert res.stripe_payment_intent_id == f"pi_{res.stripe_session_id}"


def test_complete_page_unpaid_stays_pending(client, customer, experience):
    login(client, customer.email)
    reserve(client, experience)
    res = only_reservation()
    client.get(f"/reservations/{res.id}/complete?session_id={res.stripe_session_id}")
    db.session.refresh(res)
    assert res.status == S.PENDING


def test_complete_page_rejects_other_session_id(
    client, customer, fake_payment, experience
):
    login(client, customer.email)
    reserve(client, experience)
    res = only_reservation()
    fake_payment.pay(res.stripe_session_id)
    r = client.get(f"/reservations/{res.id}/complete?session_id=cs_test_other")
    assert r.status_code == 404
    db.session.refresh(res)
    assert res.status == S.PENDING


def test_amount_mismatch_is_not_confirmed(client, customer, fake_payment, experience):
    login(client, customer.email)
    reserve(client, experience)
    res = only_reservation()
    fake_payment.pay(res.stripe_session_id)["amount_total"] = 1
    client.get(f"/reservations/{res.id}/complete?session_id={res.stripe_session_id}")
    db.session.refresh(res)
    assert res.status == S.PENDING


def test_stripe_failure_releases_hold(client, customer, fake_payment, experience):
    login(client, customer.email)
    fake_payment.error = PaymentError("決済ページを作れませんでした")
    reserve(client, experience, people=5)
    assert only_reservation().status == S.CANCELLED
    fake_payment.error = None
    reserve(client, experience, people=5)  # 定員が戻っているので予約できる
    assert only_reservation().status == S.PENDING


def test_login_required(client, experience):
    r = reserve(client, experience)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert count() == 0


def test_pending_holds_capacity(client, customer, other_customer, experience):
    login(client, customer.email)
    reserve(client, experience, people=4)  # 支払い待ちでも定員を押さえる
    client.post("/logout")
    login(client, other_customer.email)
    reserve(client, experience, people=2)  # 定員5なので入らない
    assert count() == 1


def test_expired_hold_frees_capacity(client, customer, other_customer, experience):
    login(client, customer.email)
    reserve(client, experience, people=5)
    res = only_reservation()
    res.hold_until = datetime.now() - timedelta(minutes=1)
    db.session.commit()
    client.post("/logout")
    login(client, other_customer.email)
    reserve(client, experience, people=5)
    assert count() == 2


def test_only_one_pending_per_user(client, customer, experience):
    login(client, customer.email)
    reserve(client, experience, people=1)
    reserve(client, experience, people=1)
    assert count() == 1


def test_out_of_season_rejected(client, customer, experience):
    login(client, customer.email)
    reserve(
        client, experience, visit_date=(date.today() + timedelta(days=60)).isoformat()
    )
    reserve(client, experience, visit_date=date.today().isoformat())
    assert count() == 0


def test_invalid_people_rejected(client, customer, experience):
    login(client, customer.email)
    for n in (0, -1, 21):
        reserve(client, experience, people=n)
    assert count() == 0


def test_producer_cannot_book_own(client, producer, experience):
    login(client, producer.email)
    reserve(client, experience)
    assert count() == 0


def test_unpublished_cannot_be_booked(client, customer, experience):
    experience.is_published = False
    db.session.commit()
    login(client, customer.email)
    assert reserve(client, experience).status_code == 404


def test_other_user_cannot_view_pay_complete_or_cancel(
    client, customer, other_customer, fake_payment, experience
):
    login(client, customer.email)
    res = paid_reservation(client, fake_payment, experience)
    client.post("/logout")
    login(client, other_customer.email)
    assert client.get(f"/reservations/{res.id}").status_code == 403
    assert client.post(f"/reservations/{res.id}/pay").status_code == 403
    assert client.post(f"/reservations/{res.id}/cancel").status_code == 403
    complete_url = f"/reservations/{res.id}/complete?session_id={res.stripe_session_id}"
    assert client.get(complete_url).status_code == 403
    db.session.refresh(res)
    assert res.status == S.RESERVED
    assert fake_payment.refunds == []


def test_cancel_pending_expires_session(client, customer, fake_payment, experience):
    login(client, customer.email)
    reserve(client, experience)
    res = only_reservation()
    client.post(f"/reservations/{res.id}/cancel")
    db.session.refresh(res)
    assert res.status == S.CANCELLED
    assert fake_payment.expired == [res.stripe_session_id]


def test_cancel_two_days_before_full_refund(client, customer, fake_payment, experience):
    login(client, customer.email)
    res = paid_reservation(client, fake_payment, experience, people=5, days_ahead=2)
    client.post(f"/reservations/{res.id}/cancel")
    db.session.refresh(res)
    assert res.status == S.CANCELLED and res.refunded_at is not None
    assert res.cancel_fee == 0 and res.refund_amount == 4000
    assert fake_payment.refunds == [(res.stripe_payment_intent_id, res.id, 4000)]
    # 二重に取り消しても、返金は1回だけ
    client.post(f"/reservations/{res.id}/cancel")
    assert len(fake_payment.refunds) == 1


def test_cancel_day_before_charges_half(client, customer, fake_payment, experience):
    login(client, customer.email)
    res = paid_reservation(client, fake_payment, experience, people=3, days_ahead=1)
    page = client.get(f"/reservations/{res.id}").get_data(as_text=True)
    assert "キャンセル料 50％（1,200円）" in page
    client.post(f"/reservations/{res.id}/cancel")
    db.session.refresh(res)
    assert res.total == 2400
    assert res.cancel_fee == 1200 and res.refund_amount == 1200
    assert fake_payment.refunds == [(res.stripe_payment_intent_id, res.id, 1200)]


def test_cancellation_fee_rounds_in_customer_favor(experience):
    from harvest.services.booking import cancellation_fee

    today = date.today()
    res = Reservation(total=1650, visit_date=today + timedelta(days=1))
    assert cancellation_fee(res, today) == 825
    res.total = 1651
    assert cancellation_fee(res, today) == 825  # 0.5円は切り捨て
    res.visit_date = today
    assert cancellation_fee(res, today) is None  # 当日は取り消せない


def test_cancel_fee_ignores_form_input(client, customer, fake_payment, experience):
    login(client, customer.email)
    res = paid_reservation(client, fake_payment, experience, people=2, days_ahead=1)
    client.post(f"/reservations/{res.id}/cancel", data={"refund_amount": "1600"})
    db.session.refresh(res)
    assert res.refund_amount == 800


def test_refund_failure_keeps_reservation(client, customer, fake_payment, experience):
    login(client, customer.email)
    res = paid_reservation(client, fake_payment, experience)
    fake_payment.error = PaymentError("返金できませんでした")
    client.post(f"/reservations/{res.id}/cancel")
    db.session.refresh(res)
    assert res.status == S.RESERVED and res.refunded_at is None
    assert res.cancel_fee is None and res.refund_amount is None


def test_resume_payment_redirects_to_open_session(client, customer, experience):
    login(client, customer.email)
    reserve(client, experience)
    res = only_reservation()
    r = client.post(f"/reservations/{res.id}/pay")
    assert r.status_code == 303
    assert res.stripe_session_id in r.headers["Location"]


def test_producer_sees_only_own_paid_reservations(
    client, customer, other_producer, producer, fake_payment, experience
):
    login(client, customer.email)
    paid_reservation(client, fake_payment, experience)
    client.post("/logout")
    login(client, other_producer.email)
    body = client.get("/producer/reservations").get_data(as_text=True)
    assert "テスト畑の大根" not in body
    client.post("/logout")
    login(client, producer.email)
    body = client.get("/producer/reservations").get_data(as_text=True)
    assert "テスト畑の大根" in body
