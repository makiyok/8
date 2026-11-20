import os
from datetime import date, timedelta

import pytest

from harvest import create_app
from harvest.extensions import db
from harvest.models import Experience, Role, User


class FakePayment:
    """Stripe の代わり。services/payment.py と同じ名前の関数を持ち、呼ばれた内容を記録する。

    error に例外を入れておくと、どの関数もそれを投げる（失敗の場面を試せる）。
    """

    def __init__(self):
        self.error = None
        self.sessions = {}  # session_id → retrieve_session が返す dict
        self.expired = []
        self.refunds = []

    def _check(self):
        if self.error:
            raise self.error

    def create_checkout_session(self, reservation):
        self._check()
        session_id = f"cs_test_{reservation.id}"
        self.sessions[session_id] = {
            "id": session_id,
            "status": "open",
            "url": f"https://checkout.stripe.com/c/pay/{session_id}",
            "payment_status": "unpaid",
            "amount_total": reservation.total,
            "currency": "jpy",
            "metadata": {"reservation_id": str(reservation.id)},
            "payment_intent": None,
        }
        return session_id, self.sessions[session_id]["url"]

    def pay(self, session_id):
        """テスト用：決済が済んだことにする"""
        self.sessions[session_id].update(
            status="complete", payment_status="paid", payment_intent=f"pi_{session_id}"
        )
        return self.sessions[session_id]

    def retrieve_session(self, session_id):
        self._check()
        return dict(self.sessions[session_id])

    def expire_session(self, session_id):
        self._check()
        self.expired.append(session_id)
        self.sessions[session_id]["status"] = "expired"

    def refund(self, payment_intent_id, reservation_id, amount):
        self._check()
        self.refunds.append((payment_intent_id, reservation_id, amount))


@pytest.fixture
def fake_payment():
    return FakePayment()


@pytest.fixture
def app(tmp_path, fake_payment):
    app = create_app(
        {
            "TESTING": True,
            # 既定はメモリ上の SQLite。TEST_DATABASE_URL で PostgreSQL などでも試せる
            "SQLALCHEMY_DATABASE_URI": os.environ.get("TEST_DATABASE_URL", "sqlite://"),
            "WTF_CSRF_ENABLED": False,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "SECRET_KEY": "test",
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
            "PAYMENT_SERVICE": fake_payment,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(email, role=Role.CUSTOMER, name="テスト"):
    user = User(email=email, name=name, role=role)
    if role == Role.PRODUCER:
        user.farm_name = f"{name}農園"
        user.prefecture = "岩手県"
    user.set_password("password1234")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def producer(app):
    return make_user("farm@example.com", Role.PRODUCER, "生産者A")


@pytest.fixture
def other_producer(app):
    return make_user("farm2@example.com", Role.PRODUCER, "生産者B")


@pytest.fixture
def customer(app):
    return make_user("guest@example.com")


@pytest.fixture
def other_customer(app):
    return make_user("guest2@example.com")


@pytest.fixture
def experience(producer):
    today = date.today()
    e = Experience(
        producer_id=producer.id,
        title="テスト畑の大根",
        crop_name="青首大根",
        category="daikon",
        description="説明",
        prefecture="岩手県",
        address="八幡平市",
        season_start=today - timedelta(days=5),
        season_end=today + timedelta(days=30),
        hours="9:00〜15:00",
        take_home_amount=3,
        take_home_unit="本",
        price=800,
        daily_capacity=5,
        is_published=True,
    )
    db.session.add(e)
    db.session.commit()
    return e


def login(client, email, password="password1234"):
    return client.post("/login", data={"email": email, "password": password})
