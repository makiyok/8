"""ログイン後の戻り先の検査、ログインの回数制限、セキュリティヘッダー"""

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select

from harvest.extensions import db
from harvest.models import LoginFailure

MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)


def safe_next(target: str | None) -> str | None:
    """同じサイト内のパスだけを返す。外部サイトへ飛ばせる値なら None"""
    if not target:
        return None
    # ブラウザは \ を / として扱うので、/\evil.com を //evil.com として検査する
    normalized = target.replace("\\", "/")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in normalized):
        return None
    parts = urlsplit(normalized)
    if (
        not normalized.startswith("/")
        or normalized.startswith("//")
        or parts.scheme
        or parts.netloc
    ):
        return None
    return normalized


def _keys(email: str, ip: str | None) -> list[str]:
    return [f"email:{email.strip().lower()}", f"ip:{ip or '-'}"]


def is_locked(email: str, ip: str | None) -> bool:
    since = datetime.now() - WINDOW
    for key in _keys(email, ip):
        count = db.session.scalar(
            select(func.count())
            .select_from(LoginFailure)
            .where(LoginFailure.key == key, LoginFailure.created_at >= since)
        )
        # IP は同じ回線の複数人がいるので、メールより多めに許す
        limit = MAX_FAILURES if key.startswith("email:") else MAX_FAILURES * 4
        if count >= limit:
            return True
    return False


def record_failure(email: str, ip: str | None) -> None:
    for key in _keys(email, ip):
        db.session.add(LoginFailure(key=key))
    # 古い記録は消す
    db.session.execute(
        delete(LoginFailure).where(LoginFailure.created_at < datetime.now() - WINDOW)
    )
    db.session.commit()


def clear_failures(email: str) -> None:
    db.session.execute(
        delete(LoginFailure).where(LoginFailure.key == _keys(email, None)[0])
    )
    db.session.commit()


CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; form-action 'self' https://checkout.stripe.com; frame-ancestors 'none'; "
    "base-uri 'self'; object-src 'none'"
)


def add_security_headers(response, *, https: bool):
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if https:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response
