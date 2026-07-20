"""Kafedra hujjatlari — Groq AI orqali kontent generatsiya (services/document_generator.py).

Naqsh `blueprints/topic_analysis.py`dagi bilan bir xil: lazy `from groq import Groq`,
`GROQ_API_KEY`/`GROQ_MODEL` env, timeout, try/except → None. Bu yerga qo'shilgan narsa —
JSON parse va formatga mos kelmasa qayta urinish (max 3 marta), chunki bu modul modeldan
qat'iy JSON kutadi (repo boshqa joyda response_format ishlatmaydi, Groq JSON-mode har doim
ham qo'llab-quvvatlanavermaydi — shu sabab qo'lda parse + retry qilinadi).
"""
import json
import logging
import os
import re

from services.document_prompts import DOCUMENT_TYPES, build_prompt

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GROQ_MODEL = os.environ.get('GROQ_KAFEDRA_MODEL', os.environ.get('GROQ_TOPIC_MODEL', 'llama-3.3-70b-versatile'))
GROQ_TIMEOUT = 45  # bitta hujjat bir nechta bo'limdan iborat — mavzu tahlilidan uzunroq
MAX_RETRIES = 3
MAX_TOKENS = 4000


class GenerationError(Exception):
    """AI kontent yarata olmadi (API xatosi yoki JSON hech qachon to'g'ri kelmadi)."""


def _extract_json(text):
    """Modeldan qaytgan matndan JSON obyektini ajratadi (``` qo'shsa ham)."""
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.I | re.M)
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise ValueError('JSON topilmadi')
    return json.loads(text[start:end + 1])


def generate_document_content(doc_type, form):
    """doc_type + foydalanuvchi form qiymatlari → AI yaratgan JSON kontent (dict).

    Xato bo'lsa `GenerationError` ko'taradi (chaqiruvchi status='failed' qo'yadi)."""
    if doc_type not in DOCUMENT_TYPES:
        raise GenerationError(f"Noma'lum hujjat turi: {doc_type}")
    if not GROQ_API_KEY:
        raise GenerationError("AI xizmati sozlanmagan (GROQ_API_KEY yo'q)")

    system_prompt, user_prompt = build_prompt(doc_type, form)

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT)
    except Exception as e:
        raise GenerationError(f"AI xizmatiga ulanib bo'lmadi: {e}")

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if attempt > 1:
                messages.append({"role": "user", "content":
                                 "Oldingi javobing to'g'ri JSON emas edi. Faqat va faqat "
                                 "so'ralgan JSON obyektini qaytar, boshqa hech narsa yozma."})
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                max_tokens=MAX_TOKENS,
                temperature=0.4,
            )
            raw = resp.choices[0].message.content
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            logger.warning("kafedra: JSON parse xatosi (urinish %d/%d): %s", attempt, MAX_RETRIES, e)
        except Exception as e:
            last_err = e
            logger.warning("kafedra: Groq so'rovi xato (urinish %d/%d): %s", attempt, MAX_RETRIES, e)

    raise GenerationError(f"AI hujjatni yarata olmadi: {last_err}")
