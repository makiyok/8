from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, MultipleFileField
from wtforms import (
    BooleanField,
    DateField,
    EmailField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from harvest.categories import CATEGORY_CHOICES, PREFECTURES, UNITS

PREF_CHOICES = [(p, p) for p in PREFECTURES]
IMAGE_EXTS = ["jpg", "jpeg", "png", "webp"]


class RegisterForm(FlaskForm):
    name = StringField("お名前", validators=[DataRequired(), Length(max=50)])
    email = EmailField(
        "メールアドレス", validators=[DataRequired(), Email(), Length(max=255)]
    )
    password = PasswordField(
        "パスワード（10文字以上）", validators=[DataRequired(), Length(min=10, max=128)]
    )
    password_confirm = PasswordField(
        "パスワード（確認）",
        validators=[
            DataRequired(),
            EqualTo("password", message="パスワードが一致しません"),
        ],
    )
    submit = SubmitField("登録する")


class ProducerRegisterForm(RegisterForm):
    farm_name = StringField("農園名", validators=[DataRequired(), Length(max=80)])
    prefecture = SelectField(
        "都道府県", choices=PREF_CHOICES, validators=[DataRequired()]
    )
    farm_intro = TextAreaField("農園の紹介", validators=[Optional(), Length(max=1000)])
    agree = BooleanField(
        "農産物の生産者として、収穫体験を責任を持って提供します",
        validators=[DataRequired(message="確認にチェックしてください")],
    )
    submit = SubmitField("生産者として登録する")


class LoginForm(FlaskForm):
    email = EmailField("メールアドレス", validators=[DataRequired(), Email()])
    password = PasswordField("パスワード", validators=[DataRequired()])
    submit = SubmitField("ログイン")


class ExperienceForm(FlaskForm):
    title = StringField("タイトル", validators=[DataRequired(), Length(max=80)])
    category = SelectField(
        "体験の種類", choices=CATEGORY_CHOICES, validators=[DataRequired()]
    )
    description = TextAreaField(
        "体験の説明", validators=[DataRequired(), Length(max=3000)]
    )
    prefecture = SelectField(
        "都道府県", choices=PREF_CHOICES, validators=[DataRequired()]
    )
    address = StringField(
        "集合場所（住所）", validators=[DataRequired(), Length(max=200)]
    )
    season_start = DateField("受付開始日", validators=[DataRequired()])
    season_end = DateField("受付終了日", validators=[DataRequired()])
    hours = StringField(
        "受付時間（例: 9:00〜15:00）", validators=[DataRequired(), Length(max=50)]
    )
    take_home_amount = IntegerField(
        "1人あたりの持ち帰り量",
        validators=[DataRequired(), NumberRange(min=1, max=1000)],
    )
    take_home_unit = SelectField(
        "単位", choices=[(u, u) for u in UNITS], validators=[DataRequired()]
    )
    price = IntegerField(
        "1人あたりの料金（円）",
        validators=[DataRequired(), NumberRange(min=100, max=100000)],
    )
    daily_capacity = IntegerField(
        "1日の定員（人）", validators=[DataRequired(), NumberRange(min=1, max=500)]
    )
    photos = MultipleFileField(
        "写真を追加（JPEG・PNG・WebP、1枚5MBまで、全部で8枚まで）",
        validators=[FileAllowed(IMAGE_EXTS, "JPEG・PNG・WebP の写真を選んでください")],
    )
    is_published = BooleanField("公開する")
    submit = SubmitField("保存する")

    def validate_season_end(self, field):
        if (
            self.season_start.data
            and field.data
            and field.data < self.season_start.data
        ):
            raise ValidationError("受付終了日は開始日以降にしてください")
        if (
            self.season_start.data
            and field.data
            and (field.data - self.season_start.data).days > 366
        ):
            raise ValidationError("受付期間は1年以内にしてください")


class ReservationForm(FlaskForm):
    visit_date = DateField("来園日", validators=[DataRequired()])
    people = IntegerField(
        "人数", validators=[DataRequired(), NumberRange(min=1, max=20)]
    )
    submit = SubmitField("予約する")


class SearchForm(FlaskForm):
    class Meta:
        csrf = False  # 検索は GET なので CSRF は不要

    q = StringField("キーワード", validators=[Optional(), Length(max=50)])
    category = SelectField(
        "種類",
        choices=[("", "すべての種類")] + CATEGORY_CHOICES,
        validators=[Optional()],
    )
    prefecture = SelectField(
        "都道府県",
        choices=[("", "すべての地域")] + PREF_CHOICES,
        validators=[Optional()],
    )
    month = SelectField(
        "時期",
        choices=[("", "いつでも")] + [(str(m), f"{m}月") for m in range(1, 13)],
        validators=[Optional()],
    )
