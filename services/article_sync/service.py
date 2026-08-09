# -*- coding: utf-8 -*-
"""Ommaviy orkestratsiya API — `check_article_metadata` / `apply_...`
(Phase 2C.3A).

Flask'ga BOG'LIQ EMAS: barcha funksiyalar ochiq psycopg2 kursurini oladi
(`services/article_import/service.py` bilan bir xil naqsh) — shu tufayli
kelajakdagi background scheduler (§31) AYNAN shu `check_article_metadata`ni
chaqira oladi, admin route bilan parallel logika yaratilmaydi.
"""
from __future__ import annotations

import json

from services.article_sync import persistence
from services.article_sync.diff import compare_article_metadata
from services.article_sync.models import CRITICAL, MetadataSyncResult
from services.article_sync.policy import APPLICABLE_FIELDS
from services.article_sync.state import (ACTOR_ADMIN, BULK_SAFE_QUEUE_STATES,
                                         derive_queue_state, validate_actor)
from services.article_metadata import MetadataLookupError, fetch_article_metadata


class ArticleSyncError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan xato (texnik trace emas)."""

    def __init__(self, code, public_message):
        self.code = code
        self.public_message = public_message
        super().__init__(public_message)


def _load_json(value):
    if value in (None, ''):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def check_article_metadata(cur, article_id, actor_id, *, actor_type=ACTOR_ADMIN,
                           config=None, fetch=fetch_article_metadata,
                           cache_get=None, cache_set=None):
    """DOI orqali FRESH metadata oladi va DB bilan diff qiladi (§2, §14).

    DB'dagi `scientific_articles`/`article_authors`ga HECH NARSA yozmaydi —
    faqat `article_metadata_syncs`ga audit yozuvi (§14: "faqat metadata fetch
    qiladi, diff qaytaradi, article maydonlarini o'zgartirmaydi").

    `actor_type`/`actor_id` — Phase 2C.3B: kelajakdagi scheduler (Phase 2C.3C)
    `actor_type='system', actor_id=None` bilan AYNAN shu funksiyani chaqiradi
    (§3, §36) — parallel logika yaratilmagan. Hozircha faqat admin route
    `actor_type='admin'` bilan chaqiradi.
    """
    try:
        validate_actor(actor_type, actor_id)
    except ValueError as exc:
        raise ArticleSyncError('invalid_actor', str(exc)) from exc

    persistence.ensure_schema(cur)
    article = persistence.get_article_for_sync(cur, article_id)
    if article is None:
        raise ArticleSyncError('not_found', 'Maqola topilmadi.')
    if not article.get('doi'):
        raise ArticleSyncError(
            'missing_doi',
            "Bu maqolada DOI yo'q — metadata sinxronizatsiyasi mumkin emas.")

    try:
        result = fetch(article['doi'], config=config, cache_get=cache_get,
                       cache_set=cache_set)
    except MetadataLookupError as exc:
        raise ArticleSyncError(exc.code, exc.public_message) from exc

    diffs, doi_mismatch = compare_article_metadata(article, result.metadata)
    provider_summary = {name: r.public_dict() for name, r in result.providers.items()}
    warnings = [w.to_dict() for w in result.warnings]

    sync_id = persistence.create_sync_record(
        cur, article_id, actor_id, provider_summary, diffs, article['updated_at'],
        actor_type=actor_type)

    return MetadataSyncResult(
        article_id=article_id, sync_id=sync_id, diffs=diffs,
        provider_summary=provider_summary, warnings=warnings,
        doi_mismatch=doi_mismatch, article_updated_at_snapshot=article['updated_at'])


def get_sync_result(cur, article_id, sync_id):
    """Saqlangan sync yozuvini `MetadataSyncResult`ga qayta ochadi (UI uchun,
    masalan sahifa yangilangandan keyin)."""
    persistence.ensure_schema(cur)
    row = persistence.get_sync(cur, sync_id)
    if row is None or row['article_id'] != article_id:
        raise ArticleSyncError('not_found', 'Sinxronizatsiya yozuvi topilmadi.')
    from services.article_sync.models import MetadataFieldDiff
    diffs = [MetadataFieldDiff(**{k: v for k, v in d.items()
                                  if k not in ('severity_label',)})
            for d in (_load_json(row['diff_summary']) or [])]
    return row, diffs


def get_sync_result_row(cur, sync_id):
    """Bitta sync yozuvi — DASHBOARD detail sahifasi uchun (§10). Faqat
    `sync_id`dan (route URL'da `article_id` yo'q). Diff'lar allaqachon
    JSON-parse qilingan oddiy dict ro'yxati (shablon uchun qulay); `is_stale`
    va `queue_state` — `services.article_sync.state` orqali DETERMINISTIK
    hisoblanadi, qayta saqlanmaydi."""
    persistence.ensure_schema(cur)
    row = persistence.get_sync(cur, sync_id)
    if row is None:
        return None
    current_updated_at = persistence.get_article_updated_at(cur, row['article_id'])
    is_stale = (current_updated_at is not None
               and current_updated_at != row['article_updated_at_snapshot'])
    row = dict(row)
    row['diffs'] = _load_json(row['diff_summary']) or []
    row['provider_summary'] = _load_json(row['provider_summary']) or {}
    row['is_stale'] = is_stale
    row['queue_state'] = derive_queue_state(row, is_stale=is_stale)
    return row


def apply_article_metadata_changes(cur, article_id, sync_id, actor_id,
                                   selected_fields, *, confirm_retraction=False,
                                   confirm_doi_mismatch=False):
    """Tanlangan maydonlarni SAQLANGAN sync yozuvidan (client YUBORGAN
    qiymatdan EMAS) qo'llaydi (§16). Stale-himoya (§17) va idempotency
    (§18) MAJBURIY tekshiriladi.
    """
    persistence.ensure_schema(cur)
    sync = persistence.get_sync(cur, sync_id)
    if sync is None or sync['article_id'] != article_id:
        raise ArticleSyncError('not_found', 'Sinxronizatsiya yozuvi topilmadi.')
    if sync['status'] != 'checked':
        raise ArticleSyncError(
            'already_applied',
            "Bu tekshiruv natijasi allaqachon qo'llanilgan (yoki eskirgan). "
            "Qayta tekshiring.")

    current_updated_at = persistence.get_article_updated_at(cur, article_id)
    if current_updated_at is None:
        raise ArticleSyncError('not_found', 'Maqola topilmadi.')
    if current_updated_at != sync['article_updated_at_snapshot']:
        raise ArticleSyncError(
            'stale',
            "Metadata tekshirilgandan keyin maqola o'zgartirilgan. "
            "Qayta tekshiring.")

    diffs_raw = _load_json(sync['diff_summary']) or []
    diffs_by_field = {d['field']: d for d in diffs_raw}

    if 'doi' in diffs_by_field and not confirm_doi_mismatch:
        raise ArticleSyncError(
            'doi_mismatch_confirmation_required',
            "Provayder javobi so'ralgan DOI'dan farq qiladi — davom etish "
            "uchun aniq tasdiqlash kerak.")

    retraction_diff = diffs_by_field.get('is_retracted')
    if ('is_retracted' in selected_fields and retraction_diff
            and retraction_diff.get('severity') == CRITICAL
            and not confirm_retraction):
        raise ArticleSyncError(
            'retraction_confirmation_required',
            "Bu maqola chaqirib olingan (retracted) deb belgilanmoqda — "
            "davom etish uchun aniq tasdiqlash kerak.")

    selected = [f for f in dict.fromkeys(selected_fields)
               if f in APPLICABLE_FIELDS and f in diffs_by_field
               and diffs_by_field[f].get('applicable')]
    if not selected:
        raise ArticleSyncError(
            'no_fields_selected',
            "Hech qanday qo'llanadigan maydon tanlanmadi.")

    authors_applied = persistence.apply_field_changes(
        cur, article_id, diffs_by_field, selected, actor_id)

    ok = persistence.mark_sync_applied(cur, sync_id, actor_id, selected)
    if not ok:
        # Amalda yuz BERMASLIGI kerak (bitta tranzaksiya ichida status
        # yuqorida allaqachon 'checked' ekani tekshirilgan) — lekin
        # muvaffaqiyatsiz bo'lsa jim yutib yubormaymiz: article allaqachon
        # yozilgan, shuning uchun BALAND OVOZDA xato — route buni ROLLBACK
        # qiladi (article yozuvi HAM birga bekor bo'ladi, yarim holat yo'q).
        raise ArticleSyncError(
            'sync_record_not_linked',
            "Ichki nomuvofiqlik: o'zgarishlar qo'llanildi, lekin sinxronizatsiya "
            "yozuvi bilan bog'lab bo'lmadi. Qayta urinib ko'ring.")

    return {'article_id': article_id, 'applied_fields': selected,
           'authors_applied': authors_applied}


def list_sync_history(cur, article_id, limit=10, offset=0):
    """(rows, total) — bitta maqolaning TO'LIQ tarixi, sahifalangan (§17).
    Xom API javobi yo'q — faqat saqlangan diff xulosasi."""
    persistence.ensure_schema(cur)
    return persistence.list_syncs_for_article(cur, article_id, limit=limit,
                                              offset=offset)


def dismiss_sync_result(cur, sync_id, actor_id, reason=None):
    """"Ko'rib chiqildi, o'zgartirishsiz yopish" (§12). Maqolani RAD ETMAYDI —
    faqat shu sync candidate yopiladi; tarix o'chirilmaydi."""
    persistence.ensure_schema(cur)
    sync = persistence.get_sync(cur, sync_id)
    if sync is None:
        raise ArticleSyncError('not_found', 'Sinxronizatsiya yozuvi topilmadi.')
    ok = persistence.dismiss_sync(cur, sync_id, actor_id, reason=reason)
    if not ok:
        raise ArticleSyncError(
            'cannot_dismiss',
            "Bu element hozirgi holatda yopib bo'lmaydi (allaqachon "
            "qo'llangan yoki yopilgan).")
    return persistence.get_sync(cur, sync_id)


# ── Dashboard (Phase 2C.3B) ─────────────────────────────────────────────────

def list_dashboard(cur, **filters):
    """(rows, total) — eng so'nggi sync har maqola uchun, filtrlangan/
    saralangan/sahifalangan (§4-§9). `filters` —
    `persistence.list_dashboard`ning kalit so'z argumentlari.

    Har qatorga `queue_state` QO'SHILADI shu yerda — shablon holatni o'zi
    HISOBLAMAYDI (§6: "Status stringlar route/template bo'ylab tarqalmasin,
    bitta policy/state helper ishlat"). `is_stale` allaqachon SQL'da
    hisoblangan (`persistence._LATEST_SYNC_CTE`).
    """
    persistence.ensure_schema(cur)
    rows, total = persistence.list_dashboard(cur, **filters)
    for row in rows:
        row['queue_state'] = derive_queue_state(row, is_stale=row.get('is_stale'))
    return rows, total


def get_dashboard_summary(cur):
    persistence.ensure_schema(cur)
    return persistence.get_dashboard_summary(cur)


def bulk_apply_safe_changes(cur, sync_ids, actor_id):
    """"Tanlangan xavfsiz o'zgarishlarni qo'llash" (§16) — FAQAT:
    `severity=safe`, `default_selected=true`, eskirmagan, retraction/kritik
    konfliktsiz maydonlar. Review/critical fieldlar bulk actionga UMUMAN
    kirmaydi. Yangi apply logika YOZILMAYDI — har bir sync uchun mavjud
    `apply_article_metadata_changes()` chaqiriladi (§11).
    """
    applied, skipped = [], []
    for sync_id in sync_ids:
        sync = persistence.get_sync(cur, sync_id)
        if sync is None or sync['status'] != 'checked':
            skipped.append({'sync_id': sync_id, 'reason': 'not_checked'})
            continue
        current_updated_at = persistence.get_article_updated_at(cur, sync['article_id'])
        is_stale = current_updated_at != sync['article_updated_at_snapshot']
        queue_state = derive_queue_state(sync, is_stale=is_stale)
        if queue_state not in BULK_SAFE_QUEUE_STATES:
            skipped.append({'sync_id': sync_id, 'reason': 'not_bulk_safe'})
            continue
        if sync.get('critical_change_count') or sync.get('has_retraction_change'):
            skipped.append({'sync_id': sync_id, 'reason': 'has_critical_or_retraction'})
            continue

        diffs_raw = _load_json(sync['diff_summary']) or []
        safe_fields = [d['field'] for d in diffs_raw
                      if d.get('severity') == 'safe' and d.get('default_selected')
                      and d.get('applicable')]
        if not safe_fields:
            skipped.append({'sync_id': sync_id, 'reason': 'no_safe_fields'})
            continue
        try:
            apply_article_metadata_changes(cur, sync['article_id'], sync_id,
                                           actor_id, safe_fields)
            applied.append({'sync_id': sync_id, 'article_id': sync['article_id'],
                           'fields': safe_fields})
        except ArticleSyncError as exc:
            skipped.append({'sync_id': sync_id, 'reason': exc.code})
    return {'applied': applied, 'skipped': skipped}
