# -*- coding: utf-8 -*-
"""Ommaviy DOI/CSV import — admin panel (article_import_bp, Phase 2C.2).

Bu blueprint faqat HTTP orkestratsiya: parsing, dedup, holat mashinasi va
DB yozish — barchasi `services/article_import/` da (§77 — route biznes
logikani o'zida ushlamaydi). `blueprints/articles.py`dan ATAYIN ajratilgan
(u allaqachon 2000+ qator) — lekin bir xil admin avtorizatsiya va CSRF
konvensiyasidan foydalanadi.

ENG MUHIM QOIDA: import — publish EMAS. Tasdiqlangan (`approve`) element
har doim `verification_status='pending_review'`, `publication_status='draft'`
bilan yaratiladi (`services/article_import/persistence.py:create_article_from_metadata`).

Routes (admin, barchasi login + is_admin talab qiladi):
  GET/POST /admin/articles/import                — yangi partiya (DOI/CSV)
  GET      /admin/articles/imports                — partiyalar tarixi
  GET      /admin/articles/imports/<id>            — ko'rib chiqish navbati
  GET      /admin/articles/imports/<id>/status      — JSON progress (polling)
  POST     /admin/articles/imports/<id>/process     — bitta chunk ishlaydi
  POST     /admin/articles/imports/<id>/cancel      — yumshoq bekor qilish
  POST     /admin/articles/imports/<id>/bulk-approve — xavfsiz nomzodlarni tasdiqlash
  GET      /admin/articles/imports/<id>/items/<iid> — element tafsiloti
  POST     /admin/articles/imports/<id>/items/<iid>/approve
  POST     /admin/articles/imports/<id>/items/<iid>/reject
  POST     /admin/articles/imports/<id>/items/<iid>/skip
  POST     /admin/articles/imports/<id>/items/<iid>/retry
"""
from __future__ import annotations

import math

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request)
from flask_login import current_user, login_required

from data import get_connection
from security import log_exception
from security_uploads import UploadValidationError, validate_tabular_upload
from services.article_import import (DuplicateArticleError, ImportServiceError,
                                      ImportParseError, config_from_env,
                                      ITEM_STATUS_LABELS, BATCH_STATUS_LABELS)
from services.article_import import service as import_service
from services.article_import.state import ITEM_STATUSES
from services.article_metadata import flask_cache_adapters
from utils.scientific_fields import build_tree

article_import_bp = Blueprint('article_import', __name__)

BATCH_PER_PAGE = 20
ITEM_PER_PAGE = 50


def _require_admin():
    from app import _require_admin as guard
    guard()


def _metadata_cache_adapters():
    """Bir xil kesh — services/article_metadata FileSystemCache (§50)."""
    try:
        from extensions import cache
        return flask_cache_adapters(cache)
    except Exception:
        return None, None


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


# ═══════════════════════════════════════════════════════════════════════════
#  Yaratish
# ═══════════════════════════════════════════════════════════════════════════

@article_import_bp.route('/admin/articles/import', methods=['GET', 'POST'])
@login_required
def import_new():
    _require_admin()
    config = config_from_env()

    if request.method == 'GET':
        return render_template('admin/article_import.html',
                               max_items=config.max_items)

    source_type = (request.form.get('source_type') or 'doi_list').strip()
    raw_text = request.form.get('doi_list') or ''
    csv_bytes = None
    original_filename = None

    if source_type == 'csv':
        upload = request.files.get('csv_file')
        if not upload or not upload.filename:
            return render_template(
                'admin/article_import.html', max_items=config.max_items,
                error="CSV fayl tanlanmadi.", source_type=source_type), 400
        try:
            _ext, csv_bytes = validate_tabular_upload(upload, ('csv',))
        except UploadValidationError as exc:
            return render_template(
                'admin/article_import.html', max_items=config.max_items,
                error=exc.public_message, source_type=source_type), 400
        original_filename = upload.filename[:255]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                batch_id = import_service.create_import_batch(
                    cur, current_user.id, source_type, raw_text=raw_text,
                    csv_bytes=csv_bytes, original_filename=original_filename,
                    config=config)
                conn.commit()
            except (ImportParseError, ImportServiceError) as exc:
                conn.rollback()
                return render_template(
                    'admin/article_import.html', max_items=config.max_items,
                    error=exc.public_message, source_type=source_type,
                    doi_list=raw_text if source_type == 'doi_list' else ''), 400
    finally:
        conn.close()

    flash("Import partiyasi yaratildi.")
    return redirect(f'/admin/articles/imports/{batch_id}')


# ═══════════════════════════════════════════════════════════════════════════
#  Tarix va ko'rib chiqish navbati
# ═══════════════════════════════════════════════════════════════════════════

@article_import_bp.route('/admin/articles/imports')
@login_required
def import_history():
    _require_admin()
    page = max(1, _int_arg('page', 1))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            batches, total = import_service.list_batches(
                cur, limit=BATCH_PER_PAGE, offset=(page - 1) * BATCH_PER_PAGE)
            conn.commit()
    finally:
        conn.close()
    return render_template('admin/article_imports.html', batches=batches,
                           total=total, page=page,
                           pages=max(1, math.ceil(total / BATCH_PER_PAGE)),
                           status_labels=BATCH_STATUS_LABELS)


@article_import_bp.route('/admin/articles/imports/<int:batch_id>')
@login_required
def import_batch_detail(batch_id):
    _require_admin()
    status_filter = (request.args.get('status') or '').strip()
    if status_filter not in ITEM_STATUSES:
        status_filter = None
    page = max(1, _int_arg('page', 1))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            batch = import_service.get_batch(cur, batch_id)
            if batch is None:
                abort(404)
            items, total = import_service.list_items(
                cur, batch_id, status=status_filter, limit=ITEM_PER_PAGE,
                offset=(page - 1) * ITEM_PER_PAGE)
            conn.commit()
    finally:
        conn.close()

    return render_template(
        'admin/article_import_batch.html', batch=batch, items=items, total=total,
        page=page, pages=max(1, math.ceil(total / ITEM_PER_PAGE)),
        status_filter=status_filter, item_status_labels=ITEM_STATUS_LABELS,
        batch_status_labels=BATCH_STATUS_LABELS, item_statuses=ITEM_STATUSES)


@article_import_bp.route('/admin/articles/imports/<int:batch_id>/status')
@login_required
def import_batch_status(batch_id):
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            batch = import_service.get_batch(cur, batch_id)
            conn.commit()
    finally:
        conn.close()
    if batch is None:
        return jsonify({'error': 'not_found'}), 404
    from services.article_import.models import ImportBatch
    return jsonify(ImportBatch.from_row(batch).to_dict())


@article_import_bp.route('/admin/articles/imports/<int:batch_id>/process',
                         methods=['POST'])
@login_required
def import_batch_process(batch_id):
    """Bitta chunk ishlaydi (§20, §21, §44 — JS buni qayta-qayta chaqiradi)."""
    _require_admin()
    config = config_from_env()
    cache_get, cache_set = _metadata_cache_adapters()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                result = import_service.process_import_batch(
                    cur, batch_id, config=config, cache_get=cache_get,
                    cache_set=cache_set)
                conn.commit()
            except ImportServiceError as exc:
                conn.rollback()
                return jsonify({'error': exc.code, 'message': exc.public_message}), 400
            except Exception:
                conn.rollback()
                log_exception(current_app.logger, 'Import chunk ishlanmadi')
                return jsonify({'error': 'internal',
                                'message': 'Kutilmagan ichki xato.'}), 502
    finally:
        conn.close()
    from services.article_import.models import ImportBatch
    return jsonify({'processed': result['processed'],
                    'batch': ImportBatch.from_row(result['batch']).to_dict()})


@article_import_bp.route('/admin/articles/imports/<int:batch_id>/cancel',
                         methods=['POST'])
@login_required
def import_batch_cancel(batch_id):
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                import_service.cancel_import_batch(cur, batch_id)
                conn.commit()
            except ImportServiceError as exc:
                conn.rollback()
                return jsonify({'error': exc.code, 'message': exc.public_message}), 400
    finally:
        conn.close()
    flash('Import bekor qilish so\'raldi.')
    return redirect(f'/admin/articles/imports/{batch_id}')


@article_import_bp.route('/admin/articles/imports/<int:batch_id>/bulk-approve',
                         methods=['POST'])
@login_required
def import_batch_bulk_approve(batch_id):
    _require_admin()
    item_ids = [int(v) for v in request.form.getlist('item_id') if v.isdigit()]
    primary_field_id = _int_or_none(request.form.get('primary_field_id'))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            summary = import_service.bulk_approve_safe_items(
                cur, batch_id, current_user.id, item_ids,
                primary_field_id=primary_field_id)
            conn.commit()
    finally:
        conn.close()
    flash("Tasdiqlandi: %d, dublikat: %d, o'tkazib yuborilgan: %d" % (
        len(summary['approved']), len(summary['duplicates']),
        len(summary['skipped_unsafe'])))
    return redirect(f'/admin/articles/imports/{batch_id}')


# ═══════════════════════════════════════════════════════════════════════════
#  Element tafsiloti va harakatlar
# ═══════════════════════════════════════════════════════════════════════════

@article_import_bp.route(
    '/admin/articles/imports/<int:batch_id>/items/<int:item_id>')
@login_required
def import_item_detail(batch_id, item_id):
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            batch = import_service.get_batch(cur, batch_id)
            item = import_service.get_item(cur, item_id)
            if batch is None or item is None or item['batch_id'] != batch_id:
                abort(404)
            field_tree = _field_tree_context(cur)
            existing_article = None
            if item.get('existing_article_id'):
                cur.execute("SELECT id, title, slug FROM scientific_articles WHERE id = %s",
                           (item['existing_article_id'],))
                row = cur.fetchone()
                if row:
                    existing_article = {'id': row[0], 'title': row[1], 'slug': row[2]}
            created_article = None
            if item.get('created_article_id'):
                cur.execute("SELECT id, title, slug FROM scientific_articles WHERE id = %s",
                           (item['created_article_id'],))
                row = cur.fetchone()
                if row:
                    created_article = {'id': row[0], 'title': row[1], 'slug': row[2]}
            conn.commit()
    finally:
        conn.close()
    return render_template(
        'admin/article_import_item.html', batch=batch, item=item,
        field_tree=field_tree, existing_article=existing_article,
        created_article=created_article, item_status_labels=ITEM_STATUS_LABELS)


def _item_action(batch_id, item_id, action):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                result = action(cur)
                conn.commit()
            except DuplicateArticleError as exc:
                conn.rollback()
                with conn.cursor() as cur2:
                    import_service.resolve_duplicate_after_conflict(
                        cur2, item_id, exc.doi, batch_id)
                    conn.commit()
                flash('Bu DOI approve paytida allaqachon yaratilgan — element '
                     'yangilandi.')
                return None
            except ImportServiceError as exc:
                conn.rollback()
                flash(exc.public_message)
                return None
    finally:
        conn.close()
    return result


@article_import_bp.route(
    '/admin/articles/imports/<int:batch_id>/items/<int:item_id>/approve',
    methods=['POST'])
@login_required
def import_item_approve(batch_id, item_id):
    _require_admin()
    primary_field_id = _int_or_none(request.form.get('primary_field_id'))
    extra_field_ids = [int(v) for v in request.form.getlist('extra_field_ids')
                      if v.isdigit()]
    confirm_retracted = bool(request.form.get('confirm_retracted'))
    confirm_critical = bool(request.form.get('confirm_critical'))
    metadata_overrides = {}
    if 'abstract' in request.form:
        metadata_overrides['abstract'] = request.form.get('abstract')

    result = _item_action(batch_id, item_id, lambda cur: import_service.approve_import_item(
        cur, item_id, current_user.id, primary_field_id=primary_field_id,
        extra_field_ids=extra_field_ids, confirm_retracted=confirm_retracted,
        confirm_critical=confirm_critical, metadata_overrides=metadata_overrides))
    if result is None:
        return redirect(
            f'/admin/articles/imports/{batch_id}/items/{item_id}')

    article_id = result['article_id']
    flash("Qoralama yaratildi. Ma'lumotlarni tekshirib, huquq/PDF holatini "
          "to'ldiring va keyin nashr qiling.")
    return redirect(f'/admin/articles/{article_id}/edit')


@article_import_bp.route(
    '/admin/articles/imports/<int:batch_id>/items/<int:item_id>/reject',
    methods=['POST'])
@login_required
def import_item_reject(batch_id, item_id):
    _require_admin()
    _item_action(batch_id, item_id, lambda cur: import_service.reject_import_item(
        cur, item_id, current_user.id))
    flash('Element rad etildi.')
    return redirect(f'/admin/articles/imports/{batch_id}')


@article_import_bp.route(
    '/admin/articles/imports/<int:batch_id>/items/<int:item_id>/skip',
    methods=['POST'])
@login_required
def import_item_skip(batch_id, item_id):
    _require_admin()
    _item_action(batch_id, item_id, lambda cur: import_service.skip_import_item(
        cur, item_id, current_user.id))
    flash("Element o'tkazib yuborildi.")
    return redirect(f'/admin/articles/imports/{batch_id}')


@article_import_bp.route(
    '/admin/articles/imports/<int:batch_id>/items/<int:item_id>/retry',
    methods=['POST'])
@login_required
def import_item_retry(batch_id, item_id):
    _require_admin()
    _item_action(batch_id, item_id, lambda cur: import_service.retry_import_item(
        cur, item_id))
    flash('Element qayta navbatga qo\'yildi.')
    return redirect(f'/admin/articles/imports/{batch_id}')


# ── Yordamchilar ─────────────────────────────────────────────────────────────

def _int_arg(name, default=None):
    raw = (request.args.get(name) or '').strip()
    if not raw.isdigit():
        return default
    return int(raw)


def _int_or_none(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    return int(raw) if raw.isdigit() else None
