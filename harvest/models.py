from datetime import date, datetime
from typing import ClassVar

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from harvest.categories import category_image, category_label
from harvest.extensions import db, login_manager


class Role:
    CUSTOMER = "customer"
    PRODUCER = "producer"
    LABELS: ClassVar[dict[str, str]] = {CUSTOMER: "利用者", PRODUCER: "生産者"}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), default=Role.CUSTOMER)
    # 生産者だけが使う項目
    farm_name: Mapped[str | None] = mapped_column(String(80))
    prefecture: Mapped[str | None] = mapped_column(String(10))
    farm_intro: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    experiences: Mapped[list[Experience]] = relationship(back_populates="producer")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_producer(self) -> bool:
        return self.role == Role.PRODUCER

    @property
    def role_label(self) -> str:
        return Role.LABELS.get(self.role, self.role)


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


class Experience(db.Model):
    """生産者が販売する収穫体験（採取する権利）"""

    __tablename__ = "experiences"
    __table_args__ = (
        CheckConstraint("price BETWEEN 100 AND 100000", name="ck_exp_price"),
        CheckConstraint("take_home_amount BETWEEN 1 AND 1000", name="ck_exp_amount"),
        CheckConstraint("daily_capacity BETWEEN 1 AND 500", name="ck_exp_capacity"),
        CheckConstraint("season_start <= season_end", name="ck_exp_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    producer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(80))
    # 作物名は自由入力（どんな作物でも出品できる）。category はイラストと絞り込み用の分類
    crop_name: Mapped[str] = mapped_column(String(50), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    description: Mapped[str] = mapped_column(Text)
    prefecture: Mapped[str] = mapped_column(String(10), index=True)
    address: Mapped[str] = mapped_column(String(200))
    season_start: Mapped[date]
    season_end: Mapped[date]
    hours: Mapped[str] = mapped_column(String(50))
    take_home_amount: Mapped[int]
    take_home_unit: Mapped[str] = mapped_column(String(10))
    price: Mapped[int]
    daily_capacity: Mapped[int]
    is_published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    producer: Mapped[User] = relationship(back_populates="experiences")
    photos: Mapped[list[ExperiencePhoto]] = relationship(
        back_populates="experience",
        order_by="ExperiencePhoto.position",
        cascade="all, delete-orphan",
    )
    reservations: Mapped[list[Reservation]] = relationship(back_populates="experience")

    @property
    def category_label(self) -> str:
        return category_label(self.category)

    @property
    def fallback_image(self) -> str:
        return category_image(self.category)

    def in_season(self, day: date) -> bool:
        return self.season_start <= day <= self.season_end


class ExperiencePhoto(db.Model):
    __tablename__ = "experience_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    experience_id: Mapped[int] = mapped_column(ForeignKey("experiences.id"), index=True)
    filename: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(default=0)

    experience: Mapped[Experience] = relationship(back_populates="photos")


class ReservationStatus:
    PENDING = "pending"  # 支払い待ち（hold_until まで定員を押さえる）
    RESERVED = "reserved"  # 支払い済みで確定
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # 期限内に支払われなかった
    LABELS: ClassVar[dict[str, str]] = {
        PENDING: "支払い待ち",
        RESERVED: "予約確定",
        CANCELLED: "取り消し",
        EXPIRED: "期限切れ",
    }


class Reservation(db.Model):
    """予約。金額は予約したときの値段をサーバーで計算して保存する"""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("people BETWEEN 1 AND 20", name="ck_res_people"),
        CheckConstraint("total = unit_price * people", name="ck_res_total"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    experience_id: Mapped[int] = mapped_column(ForeignKey("experiences.id"), index=True)
    visit_date: Mapped[date] = mapped_column(index=True)
    people: Mapped[int]
    unit_price: Mapped[int]
    total: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default=ReservationStatus.PENDING)
    hold_until: Mapped[datetime | None]
    stripe_session_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    paid_at: Mapped[datetime | None]
    refunded_at: Mapped[datetime | None]
    # 取り消したときのキャンセル料と返金額（円）
    cancel_fee: Mapped[int | None]
    refund_amount: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    user: Mapped[User] = relationship()
    experience: Mapped[Experience] = relationship(back_populates="reservations")

    @property
    def is_payable(self) -> bool:
        """まだ支払える（支払い待ちで、押さえの期限内）"""
        return (
            self.status == ReservationStatus.PENDING
            and self.hold_until is not None
            and self.hold_until > datetime.now()
        )

    @property
    def status_label(self) -> str:
        if self.status == ReservationStatus.PENDING and not self.is_payable:
            return ReservationStatus.LABELS[ReservationStatus.EXPIRED]
        return ReservationStatus.LABELS.get(self.status, self.status)


class LoginFailure(db.Model):
    """ログインの失敗の記録。総当たりを止めるために使う"""

    __tablename__ = "login_failures"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(300), index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now, index=True)
