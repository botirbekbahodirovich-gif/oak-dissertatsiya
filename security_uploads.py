"""Bounded, content-aware validation for user supplied files."""
from __future__ import annotations

import hashlib
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


# ── PDF (ilmiy maqolalar katalogi) ──────────────────────────────────────────
# Hajm chegarasi konfiguratsiyadan. Flask'ning global MAX_CONTENT_LENGTH
# (app.py: 20 MB) butun so'rovni rad etadi, shuning uchun bu chegara undan
# KICHIK bo'lishi shart — aks holda foydalanuvchi tushunarsiz 413 oladi.
MAX_PDF_BYTES = min(
    int(os.environ.get("ARTICLE_PDF_MAX_MB", "15")) * 1024 * 1024,
    19 * 1024 * 1024,
)

_PDF_MIMES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",   # ba'zi brauzerlar PDF ni shunday yuboradi
}


def validate_pdf_upload(file_storage, max_bytes=None):
    """Extension + MIME + magic bytes + hajm; (data, sha256) qaytaradi.

    PDF ichidagi JavaScript, /OpenAction yoki o'rnatilgan fayllarni TOZALAMAYDI
    — hozirgi stack'da (Pillow bor, PDF kutubxonasi yo'q) sanitizatsiya
    imkoni yo'q. Bu cheklov docs/articles-module.md da hujjatlashtirilgan;
    shu sababli PDF hech qachon brauzerga inline `application/pdf` sifatida
    to'g'ridan-to'g'ri berilmaydi (Phase 2B.2 viewer kontrakti).
    """
    limit = max_bytes or MAX_PDF_BYTES
    name = file_storage.filename or ""
    if os.path.splitext(name)[1].lower() != ".pdf":
        raise UploadValidationError("Faqat .pdf kengaytmali fayl qabul qilinadi")

    mimetype = (file_storage.mimetype or "").lower()
    if mimetype and mimetype not in _PDF_MIMES:
        raise UploadValidationError("Fayl MIME turi PDF emas")

    data = _read_bounded(file_storage, limit)
    if not data.startswith(b"%PDF-"):
        raise UploadValidationError("Fayl PDF signaturasiga ega emas (%PDF)")
    # Buzilgan yoki kesilgan yuklamani erta ushlash: har bir haqiqiy PDF
    # oxirida %%EOF bo'ladi (ba'zi generatorlar keyin bo'sh joy qoldiradi).
    if b"%%EOF" not in data[-2048:]:
        raise UploadValidationError("PDF fayl to'liq yuklanmagan yoki buzilgan")

    return data, hashlib.sha256(data).hexdigest()


def sanitize_pdf_filename(name, fallback="maqola.pdf"):
    """Foydalanuvchi bergan nomni ko'rsatish uchun xavfsiz holga keltiradi.

    Bu nom DISKDA ISHLATILMAYDI (fayl nomi doim server generatsiya qiladi) —
    faqat metadata sifatida saqlanadi va yuklab olishda taklif qilinadi.
    Directory traversal shu sababli ikki qavat to'silgan: basename + oq ro'yxat.
    """
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    cleaned = "".join(c for c in base if c.isalnum() or c in " ._-")[:120].strip(" .")
    if not cleaned or not cleaned.lower().endswith(".pdf"):
        cleaned = (cleaned or "maqola") + ".pdf"
    return cleaned or fallback


def save_private_pdf(data, storage_dir):
    """PDF ni static/ TASHQARISIGA atomik yozadi; nisbiy yo'lni qaytaradi.

    * Fayl nomi to'liq server tomonidan generatsiya qilinadi (secrets) —
      foydalanuvchi URL ni taxmin qila olmaydi va traversal imkonsiz.
    * Sana bo'yicha pastki papka — bitta katalogda o'n minglab fayl to'planmasin.
    * os.replace bilan atomik: yarim yozilgan fayl hech qachon ko'rinmaydi.
    """
    from datetime import date

    bucket = date.today().strftime("%Y/%m")
    target_dir = os.path.join(storage_dir, bucket)
    os.makedirs(target_dir, exist_ok=True)
    filename = f"{secrets.token_urlsafe(24).replace('-', '_')}.pdf"
    final_path = os.path.join(target_dir, filename)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target_dir, prefix=".upload_", suffix=".tmp", delete=False
        ) as temporary:
            temp_path = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temp_path, final_path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return f"{bucket}/{filename}"


def resolve_private_pdf(storage_dir, relative_path):
    """Saqlangan nisbiy yo'lni absolyut yo'lga aylantiradi, traversalni to'sib.

    DB buzilgan yoki qo'lda tahrirlangan bo'lsa ham storage_dir dan tashqariga
    chiqib bo'lmaydi — realpath solishtiruvi oxirgi to'siq.
    """
    if not relative_path:
        return None
    root = os.path.realpath(storage_dir)
    candidate = os.path.realpath(os.path.join(root, str(relative_path).lstrip("/\\")))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None
