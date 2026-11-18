"""開発用のコマンド。`uv run flask seed` でサンプルの生産者と収穫体験を作る"""

from datetime import date, timedelta

import click

from harvest.extensions import db
from harvest.models import Experience, Role, User

SAMPLES = [
    ("daikon", "畑でまるごと！青首大根抜き", "大更", 3, "本", 800, -20, 60),
    ("mikan", "海が見える段々畑のミカン狩り", "平舘", 2, "kg", 1500, -15, 70),
    ("blueberry", "完熟ブルーベリー摘み放題", "西根", 500, "g", 1800, 200, 280),
    ("kaki", "秋晴れの柿狩り（富有柿）", "松尾", 6, "個", 1200, -30, 30),
    ("negi", "深谷ネギ 引っこ抜き体験", "安代", 1, "束", 900, -10, 90),
    ("gobo", "1メートル級！ゴボウ抜きチャレンジ", "田頭", 2, "本", 1300, -5, 40),
    ("tomato", "ハウスで真っ赤なトマト狩り", "寺田", 1, "kg", 1400, -40, 120),
    ("ichigo", "甘さ自慢のいちご狩り", "堀切", 1, "パック", 2000, 30, 170),
    ("imo", "親子でさつまいも掘り", "荒屋", 3, "kg", 1000, -60, 10),
    ("ringo", "りんご狩りとジュース試飲", "細野", 5, "個", 1600, -25, 25),
]


def init_app(app):
    @app.cli.command("seed")
    def seed():
        """サンプルの生産者（farm@example.com / password1234）と体験を作る"""
        if db.session.query(User).filter_by(email="farm@example.com").first():
            click.echo("すでにサンプルがあります")
            return
        farmer = User(
            email="farm@example.com",
            name="山田 太郎",
            role=Role.PRODUCER,
            farm_name="八幡平やまだ農園",
            prefecture="岩手県",
            farm_intro="家族で営む小さな農園です。",
        )
        farmer.set_password("password1234")
        customer = User(email="guest@example.com", name="佐藤 花子")
        customer.set_password("password1234")
        db.session.add_all([farmer, customer])
        today = date.today()
        for cat, title, pref, amount, unit, price, start, end in SAMPLES:
            db.session.add(
                Experience(
                    producer=farmer,
                    title=title,
                    category=cat,
                    description=(
                        f"{title}。自分の手で収穫した{amount}{unit}までお持ち帰りいただけます。"
                        "軍手と袋はこちらで用意します。汚れてもよい服装でお越しください。"
                    ),
                    prefecture="岩手県",
                    address=f"岩手県八幡平市{pref}（農園入口に集合）",
                    season_start=today + timedelta(days=start),
                    season_end=today + timedelta(days=end),
                    hours="9:00〜15:00",
                    take_home_amount=amount,
                    take_home_unit=unit,
                    price=price,
                    daily_capacity=20,
                    is_published=True,
                )
            )
        db.session.commit()
        click.echo(
            "サンプルを作りました（farm@example.com / guest@example.com、パスワード password1234）"
        )
