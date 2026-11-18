"""写真のアップロード。

中身を Pillow で開いて JPEG / PNG / WebP だけを受け付け、
向きを直してから JPEG に作り直す。作り直すことで EXIF（撮影場所の GPS など）が消える。
ファイル名はサーバーで UUID を付ける。
"""

import io
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 1600
# 巨大な画像（解凍爆弾）でメモリを使い切られないようにする
Image.MAX_IMAGE_PIXELS = 40_000_000


class ImageError(ValueError):
    pass


def process_image(data: bytes) -> bytes:
    """受け取った画像を検査し、メタデータを消した JPEG のバイト列を返す"""
    if len(data) > MAX_BYTES:
        raise ImageError("写真は1枚5MBまでです")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise ImageError("JPEG・PNG・WebP の写真を選んでください")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
        raise ImageError("写真として読み込めませんでした") from e


def save_image(data: bytes) -> str:
    folder = Path(current_app.config["UPLOAD_FOLDER"])
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    (folder / filename).write_bytes(process_image(data))
    return filename


def delete_image(filename: str | None) -> None:
    if not filename:
        return
    # 保存先の外を消さないように、ファイル名の部分だけを使う
    path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(filename).name
    path.unlink(missing_ok=True)
