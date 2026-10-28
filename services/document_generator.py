"""Kafedra hujjatlari — AI orqali kontent generatsiya (services/document_generator.py).

Provayder, model, zaxiraga o'tish va so'rov logi `services/ai_client.py` da (tier='deep').
Bu yerga qo'shilgan narsa — JSON parse va formatga mos kelmasa qayta urinish (max 3 marta),
chunki bu modul modeldan qat'iy JSON kutadi.
"""
import json
import logging

from services.ai_client import AIError, complete, extract_json
from services.document_prompts import DOCUMENT_TYPES, build_prompt

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_TOKENS = 16000  # deep modelda adaptive thinking ham shu limitdan sarflanadi


class GenerationError(Exception):
    """AI kontent yarata olmadi (API xatosi yoki JSON hech qachon to'g'ri kelmadi)."""


_extract_json = extract_json


def generate_document_content(doc_type, form):
    """doc_type + foydalanuvchi form qiymatlari → AI yaratgan JSON kontent (dict).

    Xato bo'lsa `GenerationError` ko'taradi (chaqiruvchi status='failed' qo'yadi)."""
    if doc_type not in DOCUMENT_TYPES:
        raise GenerationError(f"Noma'lum hujjat turi: {doc_type}")

    system_prompt, user_prompt = build_prompt(doc_type, form)

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        prompt = user_prompt
        if attempt > 1:
            prompt += ("\n\nOldingi javobing to'g'ri JSON emas edi. Faqat va faqat "
                       "so'ralgan JSON obyektini qaytar, boshqa hech narsa yozma.")
        try:
            raw = complete(f'kafedra:{doc_type}', system=system_prompt, user=prompt,
                           tier='deep', max_tokens=MAX_TOKENS, json_mode=True)
        except AIError as e:
            # Provayderlar allaqachon zaxiraga o'tib bo'lgan — qayta urinish foydasiz.
            raise GenerationError(e.user_message) from e
        try:
            return extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            logger.warning("kafedra: JSON parse xatosi (urinish %d/%d): %s", attempt, MAX_RETRIES, e)

    raise GenerationError(f"AI hujjatni to'g'ri formatda yarata olmadi: {last_err}")
