# -*- coding: utf-8 -*-
"""Manual + Scheduled Article Discovery (OpenAlex + Crossref) — admin panel
(Phase 2D.1/2D.2/2D.3).

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
  GET  /admin/articles/discovery                          — qidiruv formasi
                                                              + saqlangan
                                                              so'rovlar
  POST /admin/articles/discovery/search                    — qidiruv
                                                              (tashqi xarajat!)
  POST /admin/articles/discovery/enqueue                   — server-side
                                                              preview
                                                              tokenidan
                                                              import
                                                              partiyasi
  POST /admin/articles/discovery/saved-queries              — joriy formani
                                                              nomlab saqlash
  POST /admin/articles/discovery/saved-queries/<id>/run     — ishga tushirish
                                                              (tashqi xarajat!,
                                                              PREVIEW —
                                                              o'zgarishsiz,
                                                              §Phase 2D.3 §9)
  GET  /admin/articles/discovery/saved-queries/<id>/edit    — tahrirlash
                                                              formasi
  POST /admin/articles/discovery/saved-queries/<id>/update  — yangilash
  POST /admin/articles/discovery/saved-queries/<id>/deactivate — soft
                                                              faolsizlantirish
  POST /admin/articles/discovery/saved-queries/<id>/schedule — rejalashtirishni
                                                              yoqish/o'chirish
                                                              (Phase 2D.3)

Saqlangan so'rov FAQAT konfiguratsiyani eslab qoladi. Rejalashtirish
(Phase 2D.3) — bu YAGONA joy qayerda ular "o'zi-o'zidan" ishga tushishi
mumkin, lekin BU HAM faqat `services/article_discovery/scheduler.py`
orqali, mavjud review navbatiga (§10 — hech qanday avtomatik approve/
publish YO'Q). Qo'lda "Run" tugmasi (yuqorida) ATAYIN o'zgarishsiz qoldi —
u hamon preview ko'rsatadi, admin aniq "enqueue" bosishi kerak (Phase
2D.2 xavfsizlik konventsiyasi buzilmagan); FAQAT scheduler mavjud
`service.run_saved_query()` orqali admin kutmasdan avtomatik enqueue
qiladi (§10 — bu istisno ATAYIN faqat scheduled trigger uchun).
"""
from __future__ import annotations

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request)
from flask_login import current_user, login_required

from data import get_connection
from security import log_exception
from services.article_discovery import bridge, saved_queries
from services.article_discovery import scheduler as discovery_scheduler
from services.article_discovery import scheduler_persistence
from services.article_discovery.config import config_from_env
from services.article_discovery.models import (PROVIDER_LABELS, SUPPORTED_PROVIDERS,
                                                DiscoveryValidationError,
                                                ENQUEUABLE_CLASSIFICATIONS)
from services.article_discovery import service as discovery_service
from services.article_discovery.scheduler_state import (FREQUENCIES,
                                                         FREQUENCY_LABELS,
                                                         RUN_STATUS_LABELS)
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
    'not_found': 'Manba bu so\'rov uchun hech narsa topmadi.',
    'timeout': 'Manba javob bermadi (timeout). Birozdan keyin qayta urinib ko\'ring.',
    'rate_limited': 'Manba so\'rovlar chegarasiga yetdi. Birozdan keyin qayta urinib ko\'ring.',
    'unavailable': 'Manba hozircha ishlamayapti. Birozdan keyin qayta urinib ko\'ring.',
    'invalid_response': 'Manba javobi tushunarsiz formatda edi.',
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


def _saved_queries():
    """Faqat FAOL saqlangan so'rovlar — `scientific_fields WHERE is_active`
    bilan bir xil konvensiya: `is_active=False` ro'yxatdan yashiradi
    (audit/tarix uchun qatorning o'zi DB'da qoladi, UI'da qayta
    faollashtirish tugmasi YO'Q — §11 UI ro'yxati faqat Run/Edit/Deactivate)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            rows = saved_queries.list_all(cur, include_inactive=False)
            conn.commit()
    finally:
        conn.close()
    # Jinja shablonlari Python funksiyasini to'g'ridan-to'g'ri chaqira
    # olmaydi — OAK yo'nalishi nomi shu yerda oldindan hisoblanadi (preview
    # shablonidagi `field_label` bilan bir xil naqsh).
    tree = _field_tree()
    for row in rows:
        row['oak_field_label'] = (_find_field_label(tree, row['oak_field_id'])
                                  if row.get('oak_field_id') else None)
    return rows


@article_discovery_bp.route('/admin/articles/discovery')
@login_required
def discovery_search_form():
    _require_admin()
    config = config_from_env()
    return render_template('admin/article_discovery.html',
                           **_search_form_context(config, configured=config.is_configured))


def _recent_runs(limit=10):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            runs = discovery_scheduler.list_recent_runs(cur, limit=limit)
            conn.commit()
    finally:
        conn.close()
    return runs


def _search_form_context(config, **extra):
    ctx = {'config': config, 'field_tree': _field_tree(),
          'saved_queries': _saved_queries(), 'providers': SUPPORTED_PROVIDERS,
          'provider_labels': PROVIDER_LABELS, 'frequencies': FREQUENCIES,
          'frequency_labels': FREQUENCY_LABELS, 'run_status_labels': RUN_STATUS_LABELS,
          'recent_runs': _recent_runs()}
    ctx.update(extra)
    return ctx


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
        return render_template('admin/article_discovery.html',
                               **_search_form_context(config, configured=config.is_configured)), 429

    try:
        query = discovery_service.validate_query(request.form, config=config)
    except DiscoveryValidationError as exc:
        flash(exc.public_message)
        return render_template(
            'admin/article_discovery.html',
            **_search_form_context(config, configured=config.is_configured,
                                   form=request.form)), 400

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            preview = discovery_service.run_multi_provider_search(cur, query, config=config)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Discovery qidiruv muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return render_template('admin/article_discovery.html',
                               **_search_form_context(config, configured=config.is_configured,
                                                      form=request.form)), 502
    finally:
        conn.close()

    if preview.status == NOT_CONFIGURED:
        return render_template('admin/article_discovery.html',
                               **_search_form_context(config, configured=False,
                                                      form=request.form))
    if preview.status != OK:
        flash(PROVIDER_ERROR_MESSAGES.get(preview.status,
                                          'Qidiruv so\'rovi muvaffaqiyatsiz bo\'ldi.'))
        return render_template('admin/article_discovery.html',
                               **_search_form_context(config, configured=config.is_configured,
                                                      form=request.form))

    token = discovery_service.store_preview(cache.set, current_user.id, preview,
                                            config=config)
    field_label = None
    if query.oak_field_id:
        field_label = _find_field_label(_field_tree(), query.oak_field_id)

    return render_template('admin/article_discovery_preview.html', config=config,
                           preview=preview, summary=preview.summary(), token=token,
                           field_label=field_label, provider_labels=PROVIDER_LABELS,
                           enqueuable=ENQUEUABLE_CLASSIFICATIONS)


def _request_id_for_token(admin_id, token):
    """Preview token'ning DB'da saqlanadigan idempotency identifikatori.

    Raw token HECH QACHON DB'ga/log'ga yozilmaydi (§8, §20) — faqat shu
    deterministic SHA-256 hash (admin bilan namespace qilingan, boshqa admin
    xuddi shu token qiymatiga "duch kelib qolsa" ham boshqa hash beradi)."""
    import hashlib
    return hashlib.sha256(('%s:%s' % (admin_id, token)).encode('utf-8')).hexdigest()


@article_discovery_bp.route('/admin/articles/discovery/enqueue', methods=['POST'])
@login_required
def discovery_enqueue():
    _require_admin()
    config = config_from_env()
    cache = _cache()
    token = (request.form.get('preview_token') or '').strip()
    if not token:
        flash("Qidiruv natijasi muddati o'tgan yoki topilmadi. Qayta qidiring.")
        return redirect('/admin/articles/discovery')

    request_id = _request_id_for_token(current_user.id, token)

    # Tezkor yo'l — best-effort, cache eviction/restart bo'lsa ham DB
    # (source_request_id, migrations/016) YAKUNIY himoya bo'lib qoladi (§3).
    consumed = discovery_service.load_consumed(cache.get, token)
    if consumed and consumed.get('consumed'):
        return redirect('/admin/articles/imports/%s' % consumed['batch_id'])

    entry = discovery_service.load_preview(cache.get, token, current_user.id)
    if entry is None:
        flash("Qidiruv natijasi muddati o'tgan yoki topilmadi. Qayta qidiring.")
        return redirect('/admin/articles/discovery')

    from services.article_discovery.models import ArticleDiscoveryQuery
    from services.article_metadata.models import OPENALEX
    from datetime import date as _date
    query_data = entry['query']
    # `providers` bo'lmasa (masalan 2D.1 davridan qolgan eski kesh entry'si)
    # — OpenAlex-only fallback, `ArticleDiscoveryQuery.providers` default'i
    # bilan bir xil (§Backward compat).
    providers = tuple(query_data.get('providers') or (OPENALEX,))
    query = ArticleDiscoveryQuery(
        keyword=query_data['keyword'],
        from_date=_date.fromisoformat(query_data['from_date']),
        to_date=_date.fromisoformat(query_data['to_date']),
        oak_field_id=query_data.get('oak_field_id'),
        limit=query_data['limit'],
        providers=providers)
    candidates = bridge.candidates_from_cache(entry['candidates'])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = bridge.enqueue_discovery_candidates(
                cur, current_user.id, query, candidates,
                total_count=entry.get('total_count', 0),
                excluded_no_doi_count=entry.get('excluded_no_doi_count', 0),
                request_id=request_id)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Discovery enqueue muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()

    if result['batch_id'] is None:
        # §9 — hech qanday yangi DOI yo'q edi va oldingi batch ham yo'q:
        # bo'sh "orphan" batch YARATILMADI.
        flash("Yangi maqolalar topilmadi yoki ularning barchasi allaqachon "
             "navbatda.")
        return redirect('/admin/articles/discovery')

    discovery_service.mark_preview_consumed(cache.set, token, result['batch_id'],
                                            config=config)
    if not result['created']:
        flash("Bu qidiruv natijasi allaqachon navbatga yuborilgan.")
    elif result['conflicted']:
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


# ── Saqlangan so'rovlar (Phase 2D.2, §9-§11) ─────────────────────────────────
# Egalik siyosati: `services/article_discovery/saved_queries.py` docstring'iga
# qarang — istalgan admin istalgan saqlangan so'rovni ko'radi/boshqaradi
# (mavjud import-partiya konvensiyasi bilan bir xil, per-admin devor yo'q).

@article_discovery_bp.route('/admin/articles/discovery/saved-queries', methods=['POST'])
@login_required
def saved_query_create():
    _require_admin()
    config = config_from_env()
    try:
        query = discovery_service.validate_query(request.form, config=config)
        name = saved_queries.validate_name(request.form.get('name'))
    except DiscoveryValidationError as exc:
        flash(exc.public_message)
        return render_template(
            'admin/article_discovery.html',
            **_search_form_context(config, configured=config.is_configured,
                                   form=request.form)), 400

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            saved_queries.create(cur, current_user.id, name, query)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Saqlangan qidiruv yaratilmadi')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()

    flash('Qidiruv "%s" nomi bilan saqlandi.' % name)
    return redirect('/admin/articles/discovery')


@article_discovery_bp.route('/admin/articles/discovery/saved-queries/<int:query_id>/run',
                            methods=['POST'])
@login_required
def saved_query_run(query_id):
    """Saqlangan so'rovni AYNAN saqlangan holicha ishga tushiradi — tashqi
    API chaqiruvi FAQAT shu yerda, ro'yxatni ko'rish/yuklashda EMAS (§14)."""
    _require_admin()
    config = config_from_env()
    cache = _cache()

    if not discovery_service.check_rate_limit(cache.get, cache.set, current_user.id,
                                              config=config):
        flash("Qidiruvlar chegarasiga yetdingiz — bir necha soniyadan keyin "
             "qayta urinib ko'ring.")
        return redirect('/admin/articles/discovery')

    query, preview = None, None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            row = saved_queries.get(cur, query_id)
            if row is not None:
                query = saved_queries.to_query(row)
                preview = discovery_service.run_multi_provider_search(cur, query, config=config)
                if preview.status == OK:
                    saved_queries.touch_last_used(cur, query_id)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Saqlangan qidiruvni ishga tushirish muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()

    if preview is None:
        flash("Saqlangan qidiruv topilmadi.")
        return redirect('/admin/articles/discovery')
    if preview.status == NOT_CONFIGURED:
        flash('OpenAlex ulanishi sozlanmagan.')
        return redirect('/admin/articles/discovery')
    if preview.status != OK:
        flash(PROVIDER_ERROR_MESSAGES.get(preview.status,
                                          'Qidiruv so\'rovi muvaffaqiyatsiz bo\'ldi.'))
        return redirect('/admin/articles/discovery')

    token = discovery_service.store_preview(cache.set, current_user.id, preview,
                                            config=config)
    field_label = None
    if query.oak_field_id:
        field_label = _find_field_label(_field_tree(), query.oak_field_id)
    return render_template('admin/article_discovery_preview.html', config=config,
                           preview=preview, summary=preview.summary(), token=token,
                           field_label=field_label, provider_labels=PROVIDER_LABELS,
                           enqueuable=ENQUEUABLE_CLASSIFICATIONS)


@article_discovery_bp.route('/admin/articles/discovery/saved-queries/<int:query_id>/edit')
@login_required
def saved_query_edit_form(query_id):
    _require_admin()
    config = config_from_env()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            row = saved_queries.get(cur, query_id)
            conn.commit()
    finally:
        conn.close()
    if row is None:
        abort(404)
    recent_runs = _recent_runs_for(query_id)
    return render_template('admin/article_discovery_saved_query_edit.html',
                           config=config, field_tree=_field_tree(),
                           providers=SUPPORTED_PROVIDERS,
                           provider_labels=PROVIDER_LABELS, saved_query=row,
                           frequencies=FREQUENCIES, frequency_labels=FREQUENCY_LABELS,
                           run_status_labels=RUN_STATUS_LABELS,
                           recent_runs=recent_runs)


def _recent_runs_for(query_id, limit=10):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            runs = discovery_scheduler.list_recent_runs(
                cur, saved_query_id=query_id, limit=limit)
            conn.commit()
    finally:
        conn.close()
    return runs


@article_discovery_bp.route('/admin/articles/discovery/saved-queries/<int:query_id>/update',
                            methods=['POST'])
@login_required
def saved_query_update(query_id):
    _require_admin()
    config = config_from_env()
    try:
        query = discovery_service.validate_query(request.form, config=config)
        name = saved_queries.validate_name(request.form.get('name'))
    except DiscoveryValidationError as exc:
        flash(exc.public_message)
        return redirect('/admin/articles/discovery/saved-queries/%d/edit' % query_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            found = saved_queries.update(cur, query_id, name, query)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Saqlangan qidiruvni yangilash muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()

    flash('Qidiruv yangilandi.' if found else 'Saqlangan qidiruv topilmadi.')
    return redirect('/admin/articles/discovery')


@article_discovery_bp.route('/admin/articles/discovery/saved-queries/<int:query_id>/deactivate',
                            methods=['POST'])
@login_required
def saved_query_deactivate(query_id):
    _require_admin()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            found = saved_queries.set_active(cur, query_id, False)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Saqlangan qidiruvni faolsizlantirish muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()
    flash('Qidiruv faolsizlantirildi.' if found else 'Saqlangan qidiruv topilmadi.')
    return redirect('/admin/articles/discovery')


# ── Rejalashtirish (Phase 2D.3, §2, §15, §17) ────────────────────────────────

@article_discovery_bp.route('/admin/articles/discovery/saved-queries/<int:query_id>/schedule',
                            methods=['POST'])
@login_required
def saved_query_schedule(query_id):
    """Rejalashtirishni yoqadi/o'chiradi. Tashqi API'ga HECH QANDAY so'rov
    yubormaydi (§14 — faqat DB yozuvi) — shuning uchun rate-limitga
    tushmaydi, boshqa saqlangan so'rov CRUD amallari bilan bir xil."""
    _require_admin()
    enabled = request.form.get('schedule_enabled') == 'on'
    frequency = (request.form.get('schedule_frequency') or '').strip() or None

    if enabled and frequency not in FREQUENCIES:
        flash("Chastota noto'g'ri tanlandi (kunlik/haftalik/oylik bo'lishi kerak).")
        return redirect('/admin/articles/discovery')

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            row = saved_queries.get(cur, query_id)
            if row is None:
                conn.commit()
                flash("Saqlangan qidiruv topilmadi.")
                return redirect('/admin/articles/discovery')
            if enabled and not row['is_active']:
                conn.commit()
                flash("Faolsizlantirilgan qidiruvni rejalashtirib bo'lmaydi.")
                return redirect('/admin/articles/discovery')
            scheduler_persistence.set_schedule(
                cur, query_id, enabled=enabled, frequency=frequency if enabled else None)
            conn.commit()
    except Exception:
        conn.rollback()
        log_exception(current_app.logger, 'Rejalashtirishni o\'zgartirish muvaffaqiyatsiz')
        flash('Kutilmagan ichki xato. Qayta urinib ko\'ring.')
        return redirect('/admin/articles/discovery')
    finally:
        conn.close()

    flash("Avtomatik qidiruv yoqildi." if enabled else "Avtomatik qidiruv o'chirildi.")
    return redirect('/admin/articles/discovery')
