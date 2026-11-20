import logging
import os

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from harvest.extensions import csrf, db, login_manager, migrate


def database_url() -> str:
    """DATABASE_URL を SQLAlchemy の形にする。

    Render などが渡す postgres:// / postgresql:// は、ドライバーを psycopg（v3）に指定する
    """
    url = os.environ.get("DATABASE_URL") or "sqlite:///harvest.sqlite"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # 本番かどうかは APP_ENV で決める（FLASK_DEBUG には左右されない）
    production = os.environ.get("APP_ENV", "development") == "production"

    app.config.from_mapping(
        PRODUCTION=production,
        SECRET_KEY=os.environ.get("SECRET_KEY") or None,
        SQLALCHEMY_DATABASE_URI=database_url(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production
        or os.environ.get("SESSION_COOKIE_SECURE") == "1",
        REMEMBER_COOKIE_SECURE=production,
        REMEMBER_COOKIE_SAMESITE="Lax",
        UPLOAD_FOLDER=os.path.join(app.root_path, "static", "uploads"),
        MAX_PHOTOS=8,
        # 写真は1枚5MB、1回に最大8枚。フォームの他の項目の分を見込んで全体は 42MB まで
        MAX_CONTENT_LENGTH=42 * 1024 * 1024,
        STRIPE_SECRET_KEY=os.environ.get("STRIPE_SECRET_KEY", ""),
        STRIPE_WEBHOOK_SECRET=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        # テストでは、本物の Stripe の代わりにフェイクを渡す（既定は None＝本物）
        PAYMENT_SERVICE=None,
    )
    if config is not None:
        app.config.update(config)

    if not app.config["SECRET_KEY"]:
        if app.config["PRODUCTION"]:
            # 本番で SECRET_KEY が無いと、セッションを偽造される。起動を止めて気づかせる
            raise RuntimeError("本番では SECRET_KEY を環境変数に設定してください")
        app.config["SECRET_KEY"] = "dev-only-not-secret"
    if app.config["PRODUCTION"]:
        # Render では、公開 URL のホスト名が RENDER_EXTERNAL_HOSTNAME に入る
        hosts = os.environ.get("TRUSTED_HOSTS") or os.environ.get(
            "RENDER_EXTERNAL_HOSTNAME", ""
        )
        if not hosts:
            raise RuntimeError(
                "本番では TRUSTED_HOSTS（例: example.com）を設定してください"
            )
        app.config["TRUSTED_HOSTS"] = [h.strip() for h in hosts.split(",") if h.strip()]

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "ログインしてください"

    # 外部サービスは app.extensions に入れ、ビューはここから呼ぶ
    from harvest.services import payment

    app.extensions["payment"] = app.config["PAYMENT_SERVICE"] or payment

    from harvest.views import auth, producer, reservations, shop, webhooks

    for module in (auth, shop, producer, reservations, webhooks):
        app.register_blueprint(module.bp)

    from harvest import commands

    commands.init_app(app)

    from harvest.categories import CATEGORIES
    from harvest.security import add_security_headers

    @app.after_request
    def security_headers(response):
        return add_security_headers(response, https=app.config["PRODUCTION"])

    @app.context_processor
    def inject_globals():
        return {"CATEGORIES": CATEGORIES}

    @app.template_filter("yen")
    def yen(value: int) -> str:
        return f"{value:,}円"

    @app.template_filter("date_jp")
    def date_jp(value) -> str:
        """「11月14日(土)」の形式にする"""
        weekdays = "月火水木金土日"
        return f"{value.month}月{value.day}日({weekdays[value.weekday()]})"

    for code in (403, 404, 413):

        def handler(error, code=code):
            return render_template(f"errors/{code}.html"), code

        app.register_error_handler(code, handler)

    logging.basicConfig(level=logging.INFO)
    return app
