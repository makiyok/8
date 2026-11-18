from harvest import create_app
from harvest.extensions import db
from harvest.models import Role, User
from harvest.security import safe_next
from tests.conftest import login


def test_customer_register(client):
    r = client.post(
        "/register",
        data={
            "name": "花子",
            "email": "Hana@Example.com",
            "password": "password1234",
            "password_confirm": "password1234",
        },
    )
    assert r.status_code == 302
    user = db.session.scalar(db.select(User).filter_by(email="hana@example.com"))
    assert user.role == Role.CUSTOMER


def test_producer_register(client):
    r = client.post(
        "/producer/register",
        data={
            "name": "太郎",
            "email": "farm-new@example.com",
            "password": "password1234",
            "password_confirm": "password1234",
            "farm_name": "八幡平農園",
            "prefecture": "岩手県",
            "agree": "y",
        },
    )
    assert r.status_code == 302
    user = db.session.scalar(db.select(User).filter_by(email="farm-new@example.com"))
    assert user.role == Role.PRODUCER
    assert user.farm_name == "八幡平農園"


def test_producer_register_requires_agree(client):
    r = client.post(
        "/producer/register",
        data={
            "name": "太郎",
            "email": "x@example.com",
            "password": "password1234",
            "password_confirm": "password1234",
            "farm_name": "農園",
            "prefecture": "岩手県",
        },
    )
    assert r.status_code == 200
    assert db.session.scalar(db.select(User).filter_by(email="x@example.com")) is None


def test_role_cannot_be_injected_on_register(client):
    client.post(
        "/register",
        data={
            "name": "a",
            "email": "a@example.com",
            "password": "password1234",
            "password_confirm": "password1234",
            "role": "producer",
        },
    )
    user = db.session.scalar(db.select(User).filter_by(email="a@example.com"))
    assert user.role == Role.CUSTOMER


def test_login_lockout(client, customer):
    for _ in range(5):
        login(client, customer.email, "wrong-password")
    r = login(client, customer.email)  # 正しいパスワードでも止まる
    assert r.status_code == 429


def test_safe_next():
    assert safe_next("/experiences") == "/experiences"
    for bad in ["//evil.com", "/\\evil.com", "https://evil.com", "/\tevil", "evil.com"]:
        assert safe_next(bad) is None


def test_login_redirect_blocks_backslash(client, customer):
    r = client.post(
        "/login?next=/%5Cevil.com",
        data={"email": customer.email, "password": "password1234"},
    )
    assert r.status_code == 302
    assert "evil" not in r.headers["Location"]


def test_security_headers(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_csrf_is_enabled_by_default(tmp_path):
    app = create_app(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://", "SECRET_KEY": "t"}
    )
    with app.app_context():
        db.create_all()
        r = app.test_client().post("/login", data={"email": "a@b.c", "password": "x"})
        assert r.status_code == 400


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    import pytest

    with pytest.raises(RuntimeError):
        create_app()
