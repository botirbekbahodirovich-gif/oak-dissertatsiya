"""AI yordamchi (sayt chati) — niyatni model o'zi aniqlaydi, bazaga oid savollarda tool chaqiradi.

- Salomlashish / umumiy savol → model to'g'ridan-to'g'ri javob beradi (tool yo'q).
- Dissertatsiya qidiruvi / statistika → `search_dissertations`, `dissertation_stats` toollari.
  Barcha raqamlar faqat shu toollar qaytargan SQL natijasidan; system prompt to'qishni taqiqlaydi.
- Qidiruv kengaytmasi: model tool kirishida inglizcha/ruscha/sinonim variantlarni beradi,
  `expand_terms` har birini lotin↔kirill shakllariga yoyadi (baza asosan kirillda).
- Anthropic (tool use) ishlamasa: so'rov bo'yicha bitta qidiruv bajarilib, natija kontekst
  sifatida zaxira provayderga (Gemini) beriladi — raqamlar baribir bazadan.
"""
import json
import logging
import time

from services import ai_client
from utils.transliterate import get_search_variants, kiril_to_lotin, lotin_to_kiril

logger = logging.getLogger(__name__)

FEATURE = 'ai_yordamchi'
MAX_TOOL_ROUNDS = 4
MAX_PATTERNS = 16
MAX_ROWS = 10
MIN_VARIANT_LEN = 4

SYSTEM_PROMPT = """Sen olimlar.uz platformasining AI yordamchisisan. Platformada O'zbekiston OAK \
dissertatsiyalari bazasi bor (mavzu, olim, ilmiy rahbar, muassasa, ixtisoslik, daraja PhD/DSc, sana).

Qanday ishlash:
- Salomlashish, platforma haqida yoki umumiy ilmiy savol bo'lsa — tool chaqirmasdan o'zing qisqa javob ber.
- Dissertatsiyalar, olimlar, rahbarlar, muassasalar, mavzular yoki ular soni/statistikasi so'ralsa — \
avval tool chaqir: ro'yxat/misollar uchun search_dissertations, sonlar va taqsimotlar uchun dissertation_stats.
- Qidiruvda `variants` ga mavzuning boshqa shakllarini qo'sh: kirill va lotin yozuvi, inglizcha va ruscha \
atama, yaqin sinonimlar (masalan "blokcheyn" → "блокчейн", "blockchain", "taqsimlangan reestr"). \
Qisqa o'zak shakllardan foydalan (qo'shimchasiz). 4 harfdan qisqa qisqartmalarni ("AI", "ИИ") qo'shma.

Qat'iy qoidalar:
- Bazaga oid har qanday son, sana, ism va mavzu nomini FAQAT tool natijasidan ol. Toolda yo'q narsani \
to'qima, taxmin qilma, yaxlitlama. Tool natija bermasa, shuni ochiq ayt va qidiruvni qanday \
o'zgartirish mumkinligini taklif qil.
- `total_count` — bazadagi jami mos yozuvlar; `rows` — ulardan faqat ko'rsatilgan namunasi. Ularni aralashtirma.
- O'zing hisob-kitob qilma (qo'shish, ayirish, foiz, "ikkalasi birga N ta"). Yangi son kerak bo'lsa — \
toolni mos filtr bilan qayta chaqir; aks holda faqat tool bergan sonlarni keltir.
- Foydalanuvchiga to'g'ridan-to'g'ri "siz" deb, o'zbek tilida javob ber. Savol qaysi alifboda yozilgan \
bo'lsa (lotin yoki kirill), butun javobni o'sha alifboda yoz — bir javobda aralashtirma; \
bazadagi ism va nomlarni o'zgartirmasdan keltir. \
"Foydalanuvchi so'radi..." kabi uchinchi shaxsda gapirma, so'rovni qayta aytib berma.
- Qisqa yoz: 2–5 jumla yoki 3–7 bandli ro'yxat. Oddiy Markdown: **qalin**, ro'yxat. Jadval ishlatma.
- Ism va mavzularni bazadagi yozuvida (kirill bo'lsa kirillda) keltir."""

_FIELDS = {
    'mavzu': 'mavzu', 'olim': 'olim', 'ilmiy_rahbar': 'ilmiy_rahbar',
    'muassasa': 'muassasa', 'ixtisoslik': 'ixtisoslik',
}
_YEAR_SQL = "substring(sana from '(19[0-9]{2}|20[0-9]{2})')"
_GROUPS = {
    'yil': _YEAR_SQL, 'daraja': "UPPER(TRIM(daraja))", 'muassasa': "TRIM(muassasa)",
    'ixtisoslik': "TRIM(ixtisoslik)", 'ilmiy_rahbar': "TRIM(ilmiy_rahbar)",
}

_FILTER_PROPS = {
    'query': {'type': 'string',
              'description': "Asosiy qidiruv so'zi yoki ism (bo'sh — butun baza bo'yicha)."},
    'variants': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8,
                 'description': "Qo'shimcha shakllar: kirill/lotin, inglizcha, ruscha, sinonimlar."},
    'field': {'type': 'string', 'enum': ['any', *_FIELDS],
              'description': "Qaysi ustunda qidirish. Odatda 'any'; ism bo'lsa 'olim' yoki 'ilmiy_rahbar'."},
    'daraja': {'type': 'string', 'enum': ['any', 'PhD', 'DSc']},
    'year_from': {'type': 'integer', 'minimum': 1950, 'maximum': 2100},
    'year_to': {'type': 'integer', 'minimum': 1950, 'maximum': 2100},
}

TOOLS = [
    {
        'name': 'search_dissertations',
        'description': ("OAK dissertatsiyalar bazasidan qidiradi. Mos yozuvlar jami sonini (total_count) va "
                        "eng yangi 10 tagacha namunani (rows) qaytaradi."),
        'input_schema': {'type': 'object', 'properties': {
            **_FILTER_PROPS,
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ROWS},
        }, 'additionalProperties': False},
    },
    {
        'name': 'dissertation_stats',
        'description': ("Mos dissertatsiyalar sonini va guruhlar bo'yicha taqsimotini hisoblaydi "
                        "(yil, daraja, muassasa, ixtisoslik yoki ilmiy rahbar bo'yicha eng ko'plari)."),
        'input_schema': {'type': 'object', 'properties': {
            **_FILTER_PROPS,
            'group_by': {'type': 'string', 'enum': list(_GROUPS)},
            'top': {'type': 'integer', 'minimum': 1, 'maximum': 15},
        }, 'required': ['group_by'], 'additionalProperties': False},
        'cache_control': {'type': 'ephemeral'},  # tools + system — barqaror prefiks
    },
]


# ── qidiruv kengaytmasi va SQL ─────────────────────────────────────────────────

def expand_terms(query, variants=()):
    """So'z va variantlarini lotin/kirill shakllariga yoyadi (tartib saqlanadi, takrorsiz)."""
    out = []
    for i, term in enumerate([query, *(variants or [])]):
        term = ' '.join((term or '').split())
        # Qisqa variantlar ("AI", "ИИ") boshqa so'zlar ichida ham topilib natijani
        # shishiradi — variantlar uchun kamida 4 belgi; asosiy so'z uchun 2.
        if len(term) < (2 if i == 0 else MIN_VARIANT_LEN):
            continue
        for v in (term, *get_search_variants(term), lotin_to_kiril(term), kiril_to_lotin(term)):
            v = (v or '').strip().lower()
            if len(v) >= 2 and v not in out:
                out.append(v)
    return out[:MAX_PATTERNS]


def build_where(args):
    """Tool argumentlaridan (WHERE sql, params, ishlatilgan shakllar) quradi. Faqat parametrlar."""
    clauses, params = [], []
    terms = expand_terms(args.get('query'), args.get('variants'))
    if terms:
        field = args.get('field') or 'any'
        cols = [_FIELDS[field]] if field in _FIELDS else list(_FIELDS.values())
        ors = []
        for col in cols:
            for t in terms:
                ors.append(f"{col} ILIKE %s")
                params.append(f"%{t}%")
        clauses.append('(' + ' OR '.join(ors) + ')')
    daraja = args.get('daraja')
    if daraja in ('PhD', 'DSc'):
        clauses.append("UPPER(TRIM(daraja)) = %s")
        params.append(daraja.upper())
    for key, op in (('year_from', '>='), ('year_to', '<=')):
        if isinstance(args.get(key), int):
            clauses.append(f"{_YEAR_SQL}::int {op} %s")
            params.append(args[key])
    return (' AND '.join(clauses) or 'TRUE'), params, terms


def _query(sql, params):
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def search_dissertations(args):
    where, params, terms = build_where(args)
    limit = min(int(args.get('limit') or MAX_ROWS), MAX_ROWS)
    total = _query(f"SELECT COUNT(*) AS n FROM dissertations WHERE {where}", params)[0]['n']
    rows = _query(
        f"SELECT olim, mavzu, daraja, muassasa, ilmiy_rahbar, ixtisoslik, sana FROM dissertations "
        f"WHERE {where} ORDER BY {_YEAR_SQL} DESC NULLS LAST, id DESC LIMIT %s", params + [limit])
    return {'total_count': total, 'shown': len(rows), 'searched_forms': terms,
            'rows': [{k: (v or '').strip() if isinstance(v, str) else v for k, v in r.items()} for r in rows]}


def dissertation_stats(args):
    where, params, terms = build_where(args)
    expr = _GROUPS.get(args.get('group_by'), _GROUPS['yil'])
    top = min(int(args.get('top') or 10), 15)
    total = _query(f"SELECT COUNT(*) AS n FROM dissertations WHERE {where}", params)[0]['n']
    order = 'key DESC' if args.get('group_by') == 'yil' else 'count DESC, key'
    groups = _query(
        f"SELECT {expr} AS key, COUNT(*) AS count FROM dissertations WHERE {where} "
        f"AND NULLIF(TRIM(COALESCE({expr}, '')), '') IS NOT NULL "
        f"GROUP BY 1 ORDER BY {order} LIMIT %s", params + [top])
    return {'total_count': total, 'group_by': args.get('group_by'),
            'searched_forms': terms, 'groups': groups}


_HANDLERS = {'search_dissertations': search_dissertations, 'dissertation_stats': dissertation_stats}


def run_tool(name, args):
    """Tool natijasi (JSON matn) va xato belgisini qaytaradi; xato modelga ham, logga ham boradi."""
    try:
        return json.dumps(_HANDLERS[name](args or {}), ensure_ascii=False, default=str), False
    except Exception as e:
        logger.exception('ai_assistant tool xatosi: %s %s', name, args)
        return json.dumps({'error': f"{type(e).__name__}: bazadan o'qib bo'lmadi"}), True


# ── Anthropic tool-use tsikli ──────────────────────────────────────────────────

def _usage(resp):
    u = resp.usage
    return {'input_tokens': u.input_tokens, 'output_tokens': u.output_tokens,
            'cache_read': getattr(u, 'cache_read_input_tokens', 0) or 0,
            'cache_write': getattr(u, 'cache_creation_input_tokens', 0) or 0}


def _answer_with_tools(message):
    import anthropic
    model = ai_client._model_for('anthropic', 'fast')
    client = anthropic.Anthropic(api_key=ai_client._env('ANTHROPIC_API_KEY'),
                                 timeout=ai_client._timeout('fast'), max_retries=1)
    messages = [{'role': 'user', 'content': message}]
    tools_used = []
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        started = time.monotonic()
        last = round_no == MAX_TOOL_ROUNDS
        try:
            resp = client.messages.create(
                model=model, max_tokens=4000,
                system=[{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}],
                tools=TOOLS, tool_choice={'type': 'none' if last else 'auto'},
                messages=messages, output_config={'effort': 'low'})
        except anthropic.APIStatusError as e:
            err = f"HTTP {e.status_code}: {getattr(e, 'message', e)}"
            ai_client.log_attempt(FEATURE, 'fast', 'anthropic', model, started, error=err)
            raise ai_client._ProviderError(err) from e
        except anthropic.APIConnectionError as e:
            ai_client.log_attempt(FEATURE, 'fast', 'anthropic', model, started, error=f"ulanish: {e}")
            raise ai_client._ProviderError(str(e)) from e
        ai_client.log_attempt(FEATURE, 'fast', 'anthropic', model, started, _usage(resp))

        if resp.stop_reason == 'refusal':
            raise ai_client._ProviderError('refusal')
        calls = [b for b in resp.content if b.type == 'tool_use']
        if resp.stop_reason != 'tool_use' or not calls:
            text = ''.join(b.text for b in resp.content if b.type == 'text').strip()
            if not text:
                raise ai_client._ProviderError(f"bo'sh javob (stop_reason={resp.stop_reason})")
            return text, tools_used

        messages.append({'role': 'assistant', 'content': resp.content})
        results = []
        for call in calls:
            content, is_error = run_tool(call.name, call.input)
            tools_used.append({'name': call.name, 'input': call.input})
            results.append({'type': 'tool_result', 'tool_use_id': call.id,
                            'content': content, 'is_error': is_error})
        messages.append({'role': 'user', 'content': results})
    raise ai_client._ProviderError('tool tsikli chegarasidan oshdi')


def _answer_fallback(message):
    """Tool use'siz zaxira: bitta keng qidiruv natijasi kontekst sifatida boshqa provayderga."""
    data = search_dissertations({'query': message})
    context = json.dumps(data, ensure_ascii=False, default=str)
    others = [p for p in ai_client.configured_providers() if p != 'anthropic']
    text = ai_client.complete(
        FEATURE, system=SYSTEM_PROMPT + "\n\nTool o'rniga quyidagi qidiruv natijasi berilgan.",
        user=f"{message}\n\nQidiruv natijasi (JSON):\n{context}", tier='fast',
        max_tokens=1500, providers=others)
    return text, [{'name': 'search_dissertations', 'input': {'query': message}, 'fallback': True}]


def answer(message):
    """(javob matni, ishlatilgan toollar) qaytaradi; hamma yo'l yopiq bo'lsa ai_client.AIError."""
    providers = ai_client.configured_providers()
    if not providers:
        raise ai_client.AIError('AI kalitlari sozlanmagan', ai_client.USER_MESSAGE_NOT_CONFIGURED)
    if 'anthropic' in providers:
        try:
            return _answer_with_tools(message)
        except ai_client._ProviderError as e:
            logger.error('AI yordamchi: Anthropic tool-use muvaffaqiyatsiz, zaxiraga o\'tilmoqda: %s', e)
        except Exception:
            logger.exception('AI yordamchi: kutilmagan xato, zaxiraga o\'tilmoqda')
    if len(providers) == 1 and providers[0] == 'anthropic':
        raise ai_client.AIError('anthropic ishlamadi, zaxira provayder yo\'q')
    return _answer_fallback(message)
