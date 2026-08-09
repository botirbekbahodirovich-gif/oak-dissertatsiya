# -*- coding: utf-8 -*-
"""Manual OpenAlex Article Discovery — admin panel (Phase 2D.1).

Bu blueprint faqat HTTP orkestratsiya: qidiruv/klassifikatsiya/preview token
mantig'i — barchasi `services/article_discovery/`da (§77 naqsh —
`blueprints/article_import.py`dan bir xil). `blueprints/article_import.py`dan
ATAYIN ajratilgan blueprint — lekin bir xil admin avtorizatsiya/CSRF
konvensiyasidan foydalanadi va oxir-oqibat AYNAN o'sha `article_import`
navbatiga yozadi.

ENG MUHIM QOIDA: bu yerda HECH QANDAY `scientific_articles` yozuvi YO'Q.
Enqueue faqat `article_import_batches`/`article_import_items` yaratadi;
maqola FAQAT mavjud import review navbatida, admin aniq approve qilgandan
keyin yaratiladi.

Routes (admin, barchasi login + is_admin talab qiladi):
  GET  /admin/articles/discovery         — qidiruv formasi
  POST /admin/articles/discovery/search  — OpenAlex qidiruv (tashqi xarajat!)
  POST /admin/articles/discovery/enqueue — server-side preview tokenidan
                                            yangi import partiyasi yaratadi
"""
from __future__ import annotations

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request)
from flask_login import current_user, login_required

from data import get_connection
from security import log_exception
from services.article_discovery import bridge
from services.article_discovery.config import config_from_env
from services.article_discovery.models import (DiscoveryValidationError,
                                                ENQUEUABLE_CLASSIFICATIONS)
from services.article_discovery import service as discovery_service
from services.article_metadata.models import NOT_CONFIGURED, OK
from utils.scientific_fields import build_tree

article_discovery_bp = Blueprint('article_discovery', __name__)


def _require_admin():
    from app import _require_admin as guard
    guard()


def _cache():
    from extensions import cache
    return cache


def _field_tree_context(cur):
    cur.execute("""SELECT id, parent_id, name_uz, name_uz_cyrillic, name_ru, code,
                          slug, level, description, sort_order, is_active,
                          source_name, source_url, source_updated_at, raw_name,
                          source_line, source_sha256, review_status,
                          missing_parent_code
                   FROM scientific_fields WHERE is_active ORDER BY level, sort_order""")
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return build_tree(rows)


PROVIDER_ERROR_MESSAGES = {
    'not_found': 'OpenAlex bu so\'rov uchun hech narsa topmadi.',
    'timeout': 'OpenAlex javob bermadi (timeout). Birozdan keyin qayta urinib ko\'ring.',
    'rate_limited': 'OpenAlex so\'rovlar chegarasiga yetdi. Birozdan keyin qayta urinib ko\'ring.',
    'unavailable': 'OpenAlex hozircha ishlamayapti. Birozdan keyin qayta urinib ko\'ring.',
    'invalid_response': 'OpenAlex javobi tushunarsiz formatda edi.',
}


def _field_tree():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            tree = _field_tree_context(cur)
            conn.commit()
    finally:
        conn.close()
    return tree


@article_discovery_bp.route('/admin/articles/discovery')
@login_required
def discovery_search_form():
    _require_admin()
    config = config_from_env()
    return render_template('admin/article_discovery.html', config=config,
                           configured=config.is_configured,
                           field_tree=_field_tree())


@article_discovery_bp.route('/admin/articles/discovery/search', methods=['POST'])
@login_required
def discovery_search():
    _require_admin()
    config = config_from_env()
    cache = _cache()

    if not discovery_service.check_rate_limit(cache.get, cache.set, current_user.id,
                                              config=config):
        flash("Qidiruvlar chegarasiga yetdingiz — bir necha soniyadan keyin "
             "qayta urinib ko'ring.")
        return render_template('admin/article_discovery.html', config=config,
                               configured=config.is_configured,
                               field_tree=_field_tree()), 429

    try:
        query = discovery_service.validate_query(request.form, config=config)
    except DiscoveryValidationError as exc:
        flash(exc.public_message)
        return render_template(
            'admin/article_discovery.html', config=config,
            configured=config.is_configured, form=request.form,
            field_tree=_field_tree()), 400

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            preview = discovery_service.run_discovery_search(cur, query, config=config)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Discovery qidiruv muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return render_template('admin/article_discovery.html', config=config,
                               configured=config.is_configured, form=request.form,
                               field_tree=_field_tree()), 502
    finally:
        conn.close()

    if preview.status == NOT_CONFIGURED:
        return render_template('admin/article_discovery.html', config=config,
                               configured=False, form=request.form,
                               field_tree=_field_tree())
    if preview.status != OK:
        flash(PROVIDER_ERROR_MESSAGES.get(preview.status,
                                          'OpenAlex so\'rovi muvaffaqiyatsiz bo\'ldi.'))
        return render_template('admin/article_discovery.html', config=config,
                               configured=config.is_configured, form=request.form,
                               field_tree=_field_tree())

    token = discovery_service.store_preview(cache.set, current_user.id, preview,
                                            config=config)
    field_label = None
    if query.oak_field_id:
        field_label = _find_field_label(_field_tree(), query.oak_field_id)

    return render_template('admin/article_discovery_preview.html', config=config,
                           preview=preview, summary=preview.summary(), token=token,
                           field_label=field_label,
                           enqueuable=ENQUEUABLE_CLASSIFICATIONS)


@article_discovery_bp.route('/admin/articles/discovery/enqueue', methods=['POST'])
@login_required
def discovery_enqueue():
    _require_admin()
    config = config_from_env()
    cache = _cache()
    token = (request.form.get('preview_token') or '').strip()

    consumed = discovery_service.load_consumed(cache.get, token)
    if consumed and consumed.get('consumed'):
        # Double-click idempotency (§24) — xuddi shu batch'ga qaytadi, yangi
        # batch YARATILMAYDI.
        return redirect('/admin/articles/imports/%s' % consumed['batch_id'])

    entry = discovery_service.load_preview(cache.get, token, current_user.id)
    if entry is None:
        flash("Qidiruv natijasi muddati o'tgan yoki topilmadi. Qayta qidiring.")
        return redirect('/admin/articles/discovery')

    from services.article_discovery.models import ArticleDiscoveryQuery
    from datetime import date as _date
    query_data = entry['query']
    query = ArticleDiscoveryQuery(
        keyword=query_data['keyword'],
        from_date=_date.fromisoformat(query_data['from_date']),
        to_date=_date.fromisoformat(query_data['to_date']),
        oak_field_id=query_data.get('oak_field_id'),
        limit=query_data['limit'])
    candidates = bridge.candidates_from_cache(entry['candidates'])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = bridge.enqueue_discovery_candidates(
                cur, current_user.id, query, candidates,
                total_count=entry.get('total_count', 0),
                excluded_no_doi_count=entry.get('excluded_no_doi_count', 0))
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Discovery enqueue muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()

    discovery_service.mark_preview_consumed(cache.set, token, result['batch_id'],
                                            config=config)
    if result['conflicted']:
        flash("%d ta DOI navbatga qo'shildi. %d ta DOI parallel so'rov "
             "tomonidan allaqachon navbatga olingan edi."
             % (len(result['queued']), len(result['conflicted'])))
    else:
        flash("%d ta yangi maqola ko'rib chiqish navbatiga yuborildi."
             % len(result['queued']))
    return redirect('/admin/articles/imports/%s' % result['batch_id'])


def _find_field_label(nodes, field_id):
    for node in nodes:
        if node.get('id') == field_id:
            return node.get('name_uz')
        found = _find_field_label(node.get('children') or [], field_id)
        if found:
            return found
    return None
