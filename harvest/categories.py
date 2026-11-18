"""体験の種類。キーは DB に保存する値、label は画面の表示、image は static/img/crops/ のイラスト"""

CATEGORIES = {
    "daikon": {"label": "大根抜き", "image": "daikon.svg"},
    "mikan": {"label": "ミカン狩り", "image": "mikan.svg"},
    "blueberry": {"label": "ブルーベリー狩り", "image": "blueberry.svg"},
    "kaki": {"label": "柿狩り", "image": "kaki.svg"},
    "negi": {"label": "ネギ狩り", "image": "negi.svg"},
    "gobo": {"label": "ゴボウ抜き", "image": "gobo.svg"},
    "tomato": {"label": "トマト狩り", "image": "tomato.svg"},
    "ichigo": {"label": "いちご狩り", "image": "ichigo.svg"},
    "imo": {"label": "芋掘り", "image": "imo.svg"},
    "ringo": {"label": "りんご狩り", "image": "ringo.svg"},
    "budo": {"label": "ぶどう狩り", "image": "budo.svg"},
    "other": {"label": "その他の収穫体験", "image": "other.svg"},
}

CATEGORY_CHOICES = [(key, value["label"]) for key, value in CATEGORIES.items()]

# 持ち帰り量の単位
UNITS = ["本", "個", "kg", "g", "房", "束", "袋", "パック", "株"]

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]  # fmt: skip


def category_label(key: str) -> str:
    return CATEGORIES.get(key, CATEGORIES["other"])["label"]


def category_image(key: str) -> str:
    return CATEGORIES.get(key, CATEGORIES["other"])["image"]
