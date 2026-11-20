import io
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

from harvest.extensions import db
from harvest.models import Experience
from tests.conftest import login


def jpeg_with_exif() -> bytes:
    img = Image.new("RGB", (40, 30), "orange")
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def form_data(**overrides):
    today = date.today()
    data = {
        "title": "ミカン狩り",
        "crop_name": "温州みかん",
        "category": "mikan",
        "description": "甘いミカン",
        "prefecture": "岩手県",
        "address": "八幡平市",
        "season_start": (today + timedelta(days=1)).isoformat(),
        "season_end": (today + timedelta(days=30)).isoformat(),
        "hours": "9:00〜15:00",
        "take_home_amount": "2",
        "take_home_unit": "kg",
        "price": "1500",
        "daily_capacity": "10",
        "is_published": "y",
    }
    data.update(overrides)
    return data


def test_producer_creates_experience_with_photo(client, app, producer):
    login(client, producer.email)
    data = form_data()
    data["photos"] = [(io.BytesIO(jpeg_with_exif()), "photo.jpg")]
    r = client.post(
        "/producer/experiences/new", data=data, content_type="multipart/form-data"
    )
    assert r.status_code == 302
    e = db.session.scalar(db.select(Experience))
    assert e.producer_id == producer.id
    assert e.price == 1500 and e.take_home_amount == 2
    assert len(e.photos) == 1
    saved = Path(app.config["UPLOAD_FOLDER"]) / e.photos[0].filename
    assert e.photos[0].filename.endswith(".jpg")
    assert b"SecretCameraMaker" not in saved.read_bytes()  # EXIF が消えている


def test_upload_rejects_non_image(client, producer):
    login(client, producer.email)
    data = form_data()
    data["photos"] = [(io.BytesIO(b"<svg onload=alert(1)>"), "x.jpg")]
    r = client.post(
        "/producer/experiences/new", data=data, content_type="multipart/form-data"
    )
    assert r.status_code == 200
    assert db.session.scalar(db.select(Experience)) is None


def test_season_end_before_start_rejected(client, producer):
    login(client, producer.email)
    today = date.today()
    r = client.post(
        "/producer/experiences/new",
        data=form_data(
            season_start=(today + timedelta(days=10)).isoformat(),
            season_end=today.isoformat(),
        ),
    )
    assert r.status_code == 200
    assert db.session.scalar(db.select(Experience)) is None


def test_price_out_of_range_rejected(client, producer):
    login(client, producer.email)
    r = client.post("/producer/experiences/new", data=form_data(price="-100"))
    assert r.status_code == 200
    assert db.session.scalar(db.select(Experience)) is None


def test_customer_cannot_access_producer_pages(client, customer):
    login(client, customer.email)
    assert client.get("/producer/").status_code == 403
    assert client.post("/producer/experiences/new", data=form_data()).status_code == 403


def test_other_producer_cannot_edit_or_delete(client, other_producer, experience):
    login(client, other_producer.email)
    assert client.get(f"/producer/experiences/{experience.id}/edit").status_code == 403
    assert (
        client.post(
            f"/producer/experiences/{experience.id}/edit", data=form_data(price="100")
        ).status_code
        == 403
    )
    assert (
        client.post(f"/producer/experiences/{experience.id}/delete").status_code == 403
    )
    db.session.refresh(experience)
    assert experience.price == 800


def test_unpublished_is_404_for_others(client, customer, experience):
    experience.is_published = False
    db.session.commit()
    assert client.get(f"/experiences/{experience.id}").status_code == 404
    login(client, customer.email)
    assert client.get(f"/experiences/{experience.id}").status_code == 404


def test_search_by_category(client, experience):
    r = client.get("/experiences?category=daikon")
    assert "テスト畑の大根" in r.get_data(as_text=True)
    r = client.get("/experiences?category=mikan")
    assert "テスト畑の大根" not in r.get_data(as_text=True)


def test_pages_render(client, experience):
    for path in [
        "/",
        "/experiences",
        f"/experiences/{experience.id}",
        "/register",
        "/producer/register",
        "/login",
    ]:
        assert client.get(path).status_code == 200, path


def test_any_crop_can_be_listed(client, producer):
    """作物の種類は限定しない：分類に無い作物も「その他」で出品でき、名前で探せる"""
    login(client, producer.email)
    r = client.post(
        "/producer/experiences/new",
        data=form_data(
            title="夏のズッキーニ収穫", crop_name="ズッキーニ", category="other"
        ),
    )
    assert r.status_code == 302
    e = db.session.scalar(db.select(Experience))
    assert e.crop_name == "ズッキーニ" and e.category == "other"
    body = client.get("/experiences?q=ズッキーニ").get_data(as_text=True)
    assert "夏のズッキーニ収穫" in body
    assert "ズッキーニ" in client.get(f"/experiences/{e.id}").get_data(as_text=True)


def test_crop_name_required(client, producer):
    login(client, producer.email)
    r = client.post("/producer/experiences/new", data=form_data(crop_name=""))
    assert r.status_code == 200
    assert db.session.scalar(db.select(Experience)) is None


def test_unknown_category_rejected(client, producer):
    login(client, producer.email)
    r = client.post("/producer/experiences/new", data=form_data(category="hacked"))
    assert r.status_code == 200
    assert db.session.scalar(db.select(Experience)) is None
