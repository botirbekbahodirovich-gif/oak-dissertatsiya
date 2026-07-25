"""Bounded, content-aware validation for user supplied files."""
from __future__ import annotations

import io
import os
import secrets
import tempfile
import warnings
import zipfile

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_TABULAR_BYTES = 15 * 1024 * 1024
MAX_XLSX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_SIDE = 4096

_TABULAR_MIMES = {
    "application/csv",
    "application/octet-stream",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/plain",
}
_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_IMAGE_MIMES = {
    "application/octet-stream",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


class UploadValidationError(ValueError):
    """The upload does not match the expected safe format."""

    def __init__(self, public_message):
        self.public_message = public_message
        super().__init__(public_message)


class UploadTooLarge(UploadValidationError):
    """The upload exceeds its bounded processing limit."""


def _read_bounded(file_storage, limit: int) -> bytes:
    declared = file_storage.content_length
    if declared is not None and declared > limit:
        raise UploadTooLarge("Fayl ruxsat etilgan hajmdan katta")
    data = file_storage.stream.read(limit + 1)
    file_storage.stream.seek(0)
    if len(data) > limit:
        raise UploadTooLarge("Fayl ruxsat etilgan hajmdan katta")
    if not data:
        raise UploadValidationError("Fayl bo'sh")
    return data


def validate_tabular_upload(file_storage, allowed_extensions) -> tuple[str, bytes]:
    """Validate extension, MIME and magic bytes, then return bounded contents."""
    name = file_storage.filename or ""
    extension = os.path.splitext(name)[1].lower().lstrip(".")
    if extension not in set(allowed_extensions):
        raise UploadValidationError("Fayl turi ruxsat etilmagan")

    mimetype = (file_storage.mimetype or "").lower()
    if mimetype and mimetype not in _TABULAR_MIMES:
        raise UploadValidationError("Fayl MIME turi ruxsat etilmagan")

    data = _read_bounded(file_storage, MAX_TABULAR_BYTES)
    if extension == "csv":
        if b"\x00" in data[:8192] or data.startswith((b"PK\x03\x04", _OLE_SIGNATURE)):
            raise UploadValidationError("CSV fayl mazmuni noto'g'ri")
    elif extension == "xlsx":
        if not data.startswith(b"PK\x03\x04"):
            raise UploadValidationError("XLSX fayl signaturasi noto'g'ri")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 2000:
                    raise UploadTooLarge("XLSX tarkibida fayllar juda ko'p")
                expanded = sum(item.file_size for item in infos)
                if expanded > MAX_XLSX_EXPANDED_BYTES:
                    raise UploadTooLarge("XLSX ochilgan hajmi juda katta")
        except zipfile.BadZipFile as exc:
            raise UploadValidationError("XLSX fayl tuzilmasi noto'g'ri") from exc
    elif extension == "xls" and not data.startswith(_OLE_SIGNATURE):
        raise UploadValidationError("XLS fayl signaturasi noto'g'ri")
    return extension, data


def save_safe_image(file_storage, upload_dir: str, prefix: str) -> str:
    """Decode a bounded raster image and atomically re-encode it as WEBP."""
    extension = os.path.splitext(file_storage.filename or "")[1].lower()
    if extension not in _IMAGE_EXTENSIONS:
        raise UploadValidationError("Rasm turi ruxsat etilmagan")
    mimetype = (file_storage.mimetype or "").lower()
    if mimetype and mimetype not in _IMAGE_MIMES:
        raise UploadValidationError("Rasm MIME turi ruxsat etilmagan")

    data = _read_bounded(file_storage, MAX_IMAGE_BYTES)
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(data)) as source:
                width, height = source.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_SIDE
                    or height > MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise UploadTooLarge("Rasm o'lchamlari juda katta")
                image = ImageOps.exif_transpose(source)
                image.seek(0)
                image.load()
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                else:
                    image = image.copy()
    except UploadValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UploadTooLarge("Rasm o'lchamlari juda katta") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadValidationError("Rasm fayli buzilgan yoki noto'g'ri") from exc

    os.makedirs(upload_dir, exist_ok=True)
    safe_prefix = "".join(c for c in str(prefix) if c.isalnum() or c in "-_")[:40] or "image"
    filename = f"{safe_prefix}_{secrets.token_hex(16)}.webp"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=upload_dir, prefix=".upload_", suffix=".tmp", delete=False
        ) as temporary:
            temp_path = temporary.name
        image.save(temp_path, format="WEBP", quality=88, method=6)
        os.replace(temp_path, os.path.join(upload_dir, filename))
    except Exception:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    finally:
        image.close()
    return filename
