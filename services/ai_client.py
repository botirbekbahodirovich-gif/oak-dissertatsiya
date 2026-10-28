"""Yagona AI mijozi — barcha AI funksiyalar (yordamchi, mavzu tahlili, rahbar xaritasi,
kafedra hujjatlari) shu yerdan o'tadi.

- Provayderlar tartibi `.env` dagi AI_PROVIDERS (standart: "anthropic,gemini"); asosiysi
  xato bersa keyingisiga avtomatik o'tiladi. Groq uchinchi variant sifatida qoldi, lekin
  ro'yxatga qo'shilmasa ishlatilmaydi.
- Ikki daraja: "fast" (qisqa, tezkor javoblar) va "deep" (tahlil, hujjatlar). Model nomlari
  `.env` da: AI_FAST_MODEL / AI_DEEP_MODEL (Anthropic), GEMINI_FAST_MODEL / GEMINI_DEEP_MODEL,
  GROQ_FAST_MODEL / GROQ_DEEP_MODEL.
- Har bir urinish (muvaffaqiyatli yoki xato) journalctl ga `AI_REQUEST {...}` qatori va
  `ai_request_log` jadvaliga yoziladi: funksiya, provayder, model, tokenlar, narx, vaqt, xato.
- Hamma provayder xato bersa `AIError` ko'tariladi — `user_message` foydalanuvchiga
  ko'rsatiladi, texnik sabab esa logda qoladi. Xatolar hech qachon jim yutilmaydi.
"""
import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

TIERS = ('fast', 'deep')

USER_MESSAGE_UNAVAILABLE = ("AI xizmati hozir javob bermayapti. Iltimos, bir necha daqiqadan "
                            "so'ng qayta urinib ko'ring. Muammo davom etsa, administratorga xabar bering.")
USER_MESSAGE_NOT_CONFIGURED = ("AI xizmati serverda sozlanmagan (API kalit topilmadi). "
                               "Administratorga xabar bering.")

# USD per 1M token: (kirish, chiqish, kesh o'qish, kesh yozish). Ro'yxatda yo'q model uchun
# narx NULL yoziladi — taxmin qilinmaydi.
PRICES = {
    'claude-haiku-5-5': (0.10, 0.50, 0.01, 0.125),
    'claude-sonnet-5-5': (2.00, 10.00, 0.20, 2.50),
    'claude-opus-5-5': (4.00, 20.00, 0.20, 5.00),
}


class AIError(Exception):
    """Barcha provayderlar muvaffaqiyatsiz. `user_message` — foydalanuvchiga ko'rsatish uchun."""

    def __init__(self, detail, user_message=USER_MESSAGE_UNAVAILABLE):
        super().__init__(detail)
        self.user_message = user_message


class _ProviderError(Exception):
    pass


def _env(name, default=''):
    return (os.environ.get(name) or default).strip()


def _timeout(tier):
    return float(_env('AI_DEEP_TIMEOUT', '90') if tier == 'deep' else _env('AI_FAST_TIMEOUT', '30'))


def _model_for(provider, tier):
    if provider == 'anthropic':
        return (_env('AI_DEEP_MODEL', 'claude-sonnet-5-5') if tier == 'deep'
                else _env('AI_FAST_MODEL', 'claude-haiku-5-5'))
    if provider == 'gemini':
        # 2026-10: joriy kalitda pro modellar 429 (bepul kvota yo'q), flash/3.x-flash
        # 60s+ osiladi, 2.5 yangi foydalanuvchilarga yopilgan — faqat flash-lite barqaror.
        return (_env('GEMINI_DEEP_MODEL', 'gemini-flash-lite-latest') if tier == 'deep'
                else _env('GEMINI_FAST_MODEL', 'gemini-flash-lite-latest'))
    if provider == 'groq':
        return (_env('GROQ_DEEP_MODEL', 'llama-3.3-70b-versatile') if tier == 'deep'
                else _env('GROQ_FAST_MODEL', 'llama-3.1-8b-instant'))
    raise _ProviderError(f"noma'lum provayder: {provider}")


_KEY_ENV = {'anthropic': 'ANTHROPIC_API_KEY', 'gemini': 'GEMINI_API_KEY', 'groq': 'GROQ_API_KEY'}


def configured_providers():
    order = [p.strip().lower() for p in _env('AI_PROVIDERS', 'anthropic,gemini').split(',') if p.strip()]
    return [p for p in order if p in _KEY_ENV and _env(_KEY_ENV[p])]


# ── provayderlar ─────────────────────────────────────────────────────────────
# Har biri {'text', 'input_tokens', 'output_tokens', 'cache_read', 'cache_write'} qaytaradi.

def _call_anthropic(model, tier, system, user, max_tokens, json_mode):
    import anthropic
    client = anthropic.Anthropic(api_key=_env('ANTHROPIC_API_KEY'),
                                 timeout=_timeout(tier), max_retries=1)
    if json_mode:
        system += "\n\nJavobni FAQAT bitta JSON obyekt sifatida qaytar, boshqa matn yozma."
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        # System prompt har so'rovda bir xil — kesh breakpoint. Prefiks modelning minimal
        # kesh hajmidan qisqa bo'lsa API uni jim o'tkazib yuboradi (xato emas).
        system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
        messages=[{'role': 'user', 'content': user}],
        output_config={'effort': 'low' if tier == 'fast' else 'medium'},
    )
    try:
        resp = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        raise _ProviderError(f"HTTP {e.status_code}: {getattr(e, 'message', e)}") from e
    except anthropic.APIConnectionError as e:
        raise _ProviderError(f"ulanish xatosi: {e}") from e
    if resp.stop_reason == 'refusal':
        raise _ProviderError('model javob berishni rad etdi (refusal)')
    text = ''.join(b.text for b in resp.content if b.type == 'text').strip()
    if not text:
        raise _ProviderError(f"bo'sh javob (stop_reason={resp.stop_reason})")
    u = resp.usage
    return {'text': text, 'input_tokens': u.input_tokens, 'output_tokens': u.output_tokens,
            'cache_read': getattr(u, 'cache_read_input_tokens', 0) or 0,
            'cache_write': getattr(u, 'cache_creation_input_tokens', 0) or 0}


def _call_gemini(model, tier, system, user, max_tokens, json_mode):
    import requests
    gen_cfg = {'maxOutputTokens': max_tokens}
    if json_mode:
        gen_cfg['responseMimeType'] = 'application/json'
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={'x-goog-api-key': _env('GEMINI_API_KEY'), 'Content-Type': 'application/json'},
            json={'systemInstruction': {'parts': [{'text': system}]},
                  'contents': [{'role': 'user', 'parts': [{'text': user}]}],
                  'generationConfig': gen_cfg},
            timeout=_timeout(tier))
    except requests.RequestException as e:
        raise _ProviderError(f"ulanish xatosi: {e}") from e
    if r.status_code != 200:
        try:
            msg = r.json().get('error', {}).get('message', '')
        except ValueError:
            msg = r.text[:200]
        raise _ProviderError(f"HTTP {r.status_code}: {msg}")
    data = r.json()
    cands = data.get('candidates') or []
    parts = (cands[0].get('content') or {}).get('parts') or [] if cands else []
    text = ''.join(p.get('text', '') for p in parts if not p.get('thought')).strip()
    if not text:
        reason = cands[0].get('finishReason') if cands else data.get('promptFeedback')
        raise _ProviderError(f"bo'sh javob ({reason})")
    um = data.get('usageMetadata') or {}
    return {'text': text, 'input_tokens': um.get('promptTokenCount', 0),
            'output_tokens': um.get('candidatesTokenCount', 0) + um.get('thoughtsTokenCount', 0),
            'cache_read': um.get('cachedContentTokenCount', 0), 'cache_write': 0}


def _call_groq(model, tier, system, user, max_tokens, json_mode):
    from groq import Groq
    client = Groq(api_key=_env('GROQ_API_KEY'), timeout=_timeout(tier))
    kwargs = dict(model=model, max_tokens=max_tokens,
                  messages=[{'role': 'system', 'content': system},
                            {'role': 'user', 'content': user}])
    if json_mode:
        kwargs['response_format'] = {'type': 'json_object'}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        raise _ProviderError(f"{type(e).__name__}: {e}") from e
    text = (resp.choices[0].message.content or '').strip()
    if not text:
        raise _ProviderError("bo'sh javob")
    u = resp.usage
    return {'text': text, 'input_tokens': u.prompt_tokens, 'output_tokens': u.completion_tokens,
            'cache_read': 0, 'cache_write': 0}


_CALLERS = {'anthropic': _call_anthropic, 'gemini': _call_gemini, 'groq': _call_groq}


# ── log ──────────────────────────────────────────────────────────────────────

def _cost(model, r):
    p = PRICES.get(model)
    if not p or r is None:
        return None
    return round((r['input_tokens'] * p[0] + r['output_tokens'] * p[1]
                  + r['cache_read'] * p[2] + r['cache_write'] * p[3]) / 1_000_000, 6)


_schema_ready = False


def _log(entry):
    level = logging.INFO if entry['ok'] else logging.ERROR
    logger.log(level, 'AI_REQUEST %s', json.dumps(entry, ensure_ascii=False))
    global _schema_ready
    try:
        from data import get_connection
        conn = get_connection()
    except Exception:
        return  # test muhiti yoki DB yo'q — journal qatori yetarli
    try:
        with conn.cursor() as cur:
            if not _schema_ready:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_request_log (
                        id BIGSERIAL PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        feature TEXT NOT NULL,
                        tier TEXT,
                        provider TEXT NOT NULL,
                        model TEXT,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        cache_read_tokens INTEGER,
                        cache_write_tokens INTEGER,
                        cost_usd NUMERIC(12, 6),
                        latency_ms INTEGER,
                        ok BOOLEAN NOT NULL,
                        error TEXT
                    )""")
                cur.execute("CREATE INDEX IF NOT EXISTS ai_request_log_created_idx "
                            "ON ai_request_log (created_at DESC)")
            cur.execute("""
                INSERT INTO ai_request_log (feature, tier, provider, model, input_tokens,
                    output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
                    latency_ms, ok, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (entry['feature'], entry['tier'], entry['provider'], entry['model'],
                 entry['input_tokens'], entry['output_tokens'], entry['cache_read_tokens'],
                 entry['cache_write_tokens'], entry['cost_usd'], entry['latency_ms'],
                 entry['ok'], entry['error']))
        conn.commit()
        _schema_ready = True
    except Exception:
        logger.exception('ai_request_log ga yozib bo\'lmadi')
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# ── ochiq API ────────────────────────────────────────────────────────────────

def complete(feature, system, user, tier='fast', max_tokens=2000, json_mode=False):
    """Matn javobini qaytaradi; barcha provayderlar xato bersa `AIError` ko'taradi."""
    if tier not in TIERS:
        raise ValueError(f"tier: {tier}")
    providers = configured_providers()
    if not providers:
        logger.error('AI_REQUEST feature=%s: hech bir provayder kaliti sozlanmagan', feature)
        raise AIError('AI kalitlari sozlanmagan', USER_MESSAGE_NOT_CONFIGURED)

    errors = []
    for provider in providers:
        model = _model_for(provider, tier)
        started = time.monotonic()
        result, err = None, None
        try:
            result = _CALLERS[provider](model, tier, system, user, max_tokens, json_mode)
        except _ProviderError as e:
            err = str(e)
        except Exception as e:  # SDK yo'q, kutilmagan javob shakli va h.k.
            err = f"{type(e).__name__}: {e}"
        _log({
            'feature': feature, 'tier': tier, 'provider': provider, 'model': model,
            'input_tokens': result and result['input_tokens'],
            'output_tokens': result and result['output_tokens'],
            'cache_read_tokens': result and result['cache_read'],
            'cache_write_tokens': result and result['cache_write'],
            'cost_usd': _cost(model, result),
            'latency_ms': int((time.monotonic() - started) * 1000),
            'ok': result is not None, 'error': err and err[:500],
        })
        if result is not None:
            return result['text']
        errors.append(f"{provider}/{model}: {err}")

    raise AIError(' | '.join(errors))


def extract_json(text):
    """Model matnidan JSON obyektini ajratadi (``` bilan o'ralgan bo'lsa ham)."""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (text or '').strip(), flags=re.I | re.M)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise ValueError('JSON topilmadi')
    return json.loads(text[start:end + 1])
