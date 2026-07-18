"""Ulashish (share) sahifalari — "Mening ilmiy daraxtim".

Har bir olim o'z ilmiy shajara daraxtini ijtimoiy tarmoqlarga chiroyli card
sifatida ulashishi uchun public sahifa + dinamik OG-rasm:

  · GET /olim/<name>/daraxt        — publik share sahifasi (login talab qilinmaydi)
  · GET /olim/<name>/og-image.png  — 1200×630 dinamik OG kartochka (keshlanadi)

Ma'lumot modeli — mavjud, nom-asosli genealogy: dissertations.ilmiy_rahbar →
dissertations.olim. Yangi jadval kerak emas. Shajara vizuali sahifada mavjud
ShajaraTree (static/js/genealogy_tree.js) komponentini qayta ishlatadi.
"""
import os

from flask import (Blueprint, abort, current_app, redirect, render_template,
                   request, send_file, url_for)

from data import get_connection

share_bp = Blueprint('share', __name__)

_GEN_MAX_DEPTH = 25   # app.py._GEN_MAX_DEPTH bilan mos


def _gen_degree_from_daraja(daraja):
    up = (daraja or '').upper()
    low = (daraja or '').lower()
    if 'DSC' in up or 'док' in low:
        return 'DSc'
    if 'PHD' in up or 'фан' in low:
        return 'PhD'
    return None


def _scholar_card_data(name):
    """Kartochka + sahifa uchun olim ma'lumoti. Olim umuman uchramasa None.

    Qaytaradi: {name, degree, position, institution,
                n_students, n_generations, n_defended}
    """
    name = (name or '').strip()
    if not name:
        return None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Mavjudlik: hech qanday rolda uchramasa — 404
            cur.execute(
                "SELECT 1 FROM dissertations "
                "WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s)) "
                "   OR LOWER(TRIM(COALESCE(ilmiy_rahbar,''))) = LOWER(TRIM(%s)) "
                "LIMIT 1", (name, name))
            if cur.fetchone() is None:
                return None

            # Shajara statistikasi — bitta rekursiv CTE
            direct = descendants = gens = 0
            try:
                cur.execute(
                    """
                    WITH RECURSIVE tree AS (
                        SELECT TRIM(%s)::text AS name, 0 AS depth,
                               ARRAY[LOWER(TRIM(%s))] AS path
                        UNION ALL
                        SELECT DISTINCT TRIM(d.olim)::text, t.depth + 1,
                               t.path || LOWER(TRIM(d.olim))
                        FROM tree t
                        JOIN dissertations d
                          ON LOWER(TRIM(d.ilmiy_rahbar)) = LOWER(TRIM(t.name))
                        WHERE d.olim IS NOT NULL AND TRIM(d.olim) <> ''
                          AND NOT (LOWER(TRIM(d.olim)) = ANY(t.path))
                          AND t.depth < %s
                    )
                    SELECT
                        COUNT(DISTINCT CASE WHEN depth = 1 THEN LOWER(name) END),
                        COUNT(DISTINCT LOWER(name)) - 1,
                        COALESCE(MAX(depth), 0)
                    FROM tree
                    """, (name, name, _GEN_MAX_DEPTH))
                row = cur.fetchone()
                if row:
                    direct = int(row[0] or 0)
                    descendants = int(row[1] or 0)
                    gens = int(row[2] or 0)
            except Exception:
                conn.rollback()

            # Daraja / lavozim / tashkilot — avval olim_profiles, keyin dissertations
            degree = position = institution = None
            try:
                cur.execute(
                    "SELECT academic_degree, position, institution "
                    "FROM olim_profiles WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) "
                    "LIMIT 1", (name,))
                pr = cur.fetchone()
                if pr:
                    degree = (pr[0] or '').strip() or None
                    position = (pr[1] or '').strip() or None
                    institution = (pr[2] or '').strip() or None
            except Exception:
                conn.rollback()

            if not degree or not institution:
                try:
                    cur.execute(
                        "SELECT daraja, muassasa FROM dissertations "
                        "WHERE LOWER(TRIM(olim)) = LOWER(TRIM(%s)) "
                        "ORDER BY (CASE WHEN UPPER(COALESCE(daraja,'')) LIKE '%%DSC%%' "
                        "OR LOWER(COALESCE(daraja,'')) LIKE '%%док%%' THEN 0 ELSE 1 END), id DESC "
                        "LIMIT 1", (name,))
                    dr = cur.fetchone()
                    if dr:
                        if not degree:
                            degree = _gen_degree_from_daraja(dr[0])
                        if not institution:
                            institution = (dr[1] or '').strip() or None
                except Exception:
                    conn.rollback()
    finally:
        conn.close()

    # academic_degree matnini qisqa yorliqqa keltirish (PhD/DSc)
    if degree and degree not in ('PhD', 'DSc'):
        degree = _gen_degree_from_daraja(degree) or degree

    return {
        'name': name,
        'degree': degree or '',
        'position': position or '',
        'institution': institution or '',
        'n_students': direct,
        'n_generations': gens,
        'n_defended': descendants,
    }


def _slug_for(name):
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT slug FROM olim_profiles "
                            "WHERE LOWER(TRIM(olim_name)) = LOWER(TRIM(%s)) "
                            "AND slug IS NOT NULL LIMIT 1", (name,))
                r = cur.fetchone()
                return r[0] if r else None
        finally:
            conn.close()
    except Exception:
        return None


@share_bp.route('/olim/<path:name>/daraxt')
def olim_tree_share(name):
    """Publik ulashish sahifasi — olimning ilmiy shajara daraxti."""
    name = (name or '').strip()
    stats = _scholar_card_data(name)
    if not stats:
        abort(404)
    slug = _slug_for(name)
    # Kanonik ulashish havolasi (og:url va share tugmalari uchun) — absolyut
    try:
        page_url = url_for('share.olim_tree_share', name=name,
                           _external=True, _scheme='https')
    except Exception:
        page_url = request.url
    try:
        og_image_url = url_for('share.olim_og_image', name=name,
                               _external=True, _scheme='https')
    except Exception:
        og_image_url = request.url_root.rstrip('/') + '/olim/' + name + '/og-image.png'
    return render_template('share/tree.html', olim_name=name, stats=stats,
                           slug=slug, page_url=page_url, og_image_url=og_image_url)


@share_bp.route('/olim/<path:name>/og-image.png')
def olim_og_image(name):
    """Dinamik 1200×630 OG kartochka — keshlanadi; xatoda default rasm."""
    name = (name or '').strip()
    static_dir = current_app.static_folder
    cache_dir = os.path.join(static_dir, 'og-cache')
    fallback = os.path.join(static_dir, 'og-default.png')

    stats = None
    try:
        stats = _scholar_card_data(name)
    except Exception:
        stats = None

    path = fallback
    if stats:
        try:
            from og_image_generator import get_og_image
            path, _is_fallback = get_og_image(name, stats, cache_dir, fallback)
        except Exception:
            path = fallback

    if not os.path.exists(path):
        abort(404)
    resp = send_file(path, mimetype='image/png', max_age=86400)
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp
