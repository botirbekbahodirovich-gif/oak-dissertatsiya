"""Yangi OAK himoyalari uchun yagona bildirishnoma yo'li.

scraper.py (server cron'i) yangi qo'shilgan qatorlar id'larini beradi
(INSERT ... ON CONFLICT DO NOTHING RETURNING id), bu modul esa uchta kanalga
tarqatadi:
  * himoya mosliklari   — blueprints/reminders.notify_himoya_matches
                          (olim_profiles.ixtisoslik; sayt + Telegram)
  * ixtisoslik obunasi  — blueprints/subscriptions.notify_specialty_subscribers
                          (specialty_subscriptions; sayt + Telegram)
  * olim kuzatuvchilari — blueprints/olimlar_catalog.notify_scholar_follows
                          (scholar_follows; sayt qo'ng'irog'i)

At-most-once: avval `dissertations.notified_at` ALOHIDA tranzaksiyada
"egallanadi" (UPDATE ... WHERE notified_at IS NULL RETURNING) va commit
qilinadi, xabarlar shundan KEYIN yuboriladi. Ikkinchi chaqiruv hech narsa
egallamaydi, demak qayta yubormaydi. Yuborish xatosi log'ga yoziladi va qayta
urinilmaydi. Migratsiya 022 mavjud qatorlarning hammasini notified_at = NOW()
bilan belgilaydi — eski yozuvlar uchun hech narsa ketmaydi.

dry_run: egallash ham, yozish ham, yuborish ham yo'q — faqat kimga nima
ketishi `report` da qaytadi.
"""
import logging

log = logging.getLogger(__name__)

_COLUMNS = ('id', 'olim', 'mavzu', 'ixtisoslik', 'ilmiy_rahbar', 'link')

_CLAIM_SQL = """
    UPDATE dissertations SET notified_at = NOW()
    WHERE id = ANY(%s) AND notified_at IS NULL
    RETURNING id, olim, mavzu, ixtisoslik, ilmiy_rahbar, link
"""

_PEEK_SQL = """
    SELECT id, olim, mavzu, ixtisoslik, ilmiy_rahbar, link
    FROM dissertations
    WHERE id = ANY(%s) AND notified_at IS NULL
    ORDER BY id
"""


def _dispatchers():
    """(nom, funksiya) ro'yxati. Blueprint modullari `from app import csrf`
    qiladi; ularni app'dan oldin import qilish circular import beradi, shuning
    uchun avval app to'liq yuklanadi (gunicorn ichida bu no-op)."""
    import app  # noqa: F401
    from blueprints.reminders import notify_himoya_matches
    from blueprints.subscriptions import notify_specialty_subscribers
    from blueprints.olimlar_catalog import notify_scholar_follows
    return [('himoya', notify_himoya_matches),
            ('obuna', notify_specialty_subscribers),
            ('kuzatuv', notify_scholar_follows)]


def _new_result(dry_run):
    return {'dry_run': dry_run, 'claimed': 0, 'ids': [], 'counts': {},
            'errors': [], 'report': []}


def _dispatch(records, result):
    """Har bir kanal alohida: bittasining xatosi qolganlarini to'xtatmaydi."""
    try:
        dispatchers = _dispatchers()
    except Exception:
        log.exception("bildirishnoma modullarini yuklab bo'lmadi")
        result['errors'].append('import')
        return result
    for name, fn in dispatchers:
        try:
            result['counts'][name] = fn(records, dry_run=result['dry_run'],
                                        report=result['report'])
        except Exception:
            log.exception("%s bildirishnomalari xatosi", name)
            result['errors'].append(name)
    return result


def notify_new_dissertations(ids, dry_run=False):
    """`ids` (dissertations.id) uchun hali yuborilmaganlarini tarqatadi.

    Idempotent: notified_at bir marta egallanadi. Exception faqat egallash
    bosqichida (masalan DB yo'q) ko'tariladi — chaqiruvchi uni log'ga yozadi.
    """
    result = _new_result(dry_run)
    ids = sorted({int(i) for i in (ids or []) if i})
    if not ids:
        return result
    from data import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_PEEK_SQL if dry_run else _CLAIM_SQL, (ids,))
            rows = cur.fetchall()
        if dry_run:
            conn.rollback()
        else:
            conn.commit()   # egallash yuborishdan OLDIN commit (at-most-once)
    finally:
        conn.close()
    records = [dict(zip(_COLUMNS, row)) for row in rows]
    result['claimed'] = len(records)
    result['ids'] = [r['id'] for r in records]
    if not records:
        return result
    return _dispatch(records, result)


def preview_records(records):
    """Bazada hali yo'q yozuvlar uchun (scraper --dry-run): kimga nima ketardi."""
    result = _new_result(True)
    result['claimed'] = len(records)
    if not records:
        return result
    return _dispatch(list(records), result)


def format_result(result):
    """Log uchun qisqa matn (chat id'lar chiqarilmaydi)."""
    head = "Bildirishnoma" + (" [DRY-RUN]" if result['dry_run'] else "")
    counts = ", ".join(f"{k}={v}" for k, v in result['counts'].items()) or "-"
    lines = [f"{head}: {result['claimed']} ta yozuv; {counts}"
             + (f"; xatolar: {', '.join(result['errors'])}" if result['errors'] else "")]
    for r in result['report']:
        text = " ".join(str(r.get('text') or '').split())[:140]
        lines.append(f"  - [{r['kind']}/{r['channel']}] user={r.get('user_id')} "
                     f"diss={r.get('diss_id')}: {text}")
    return "\n".join(lines)
