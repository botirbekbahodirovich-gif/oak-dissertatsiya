"""Admin Data Quality dashboard blueprint.

Routes (all admin-only + CSRF-protected via the app-wide CSRFProtect):

    GET  /admin/data-quality                     dashboard
    GET  /admin/data-quality/duplicates          duplicate review queue
    GET  /admin/data-quality/export.csv          streaming CSV export
    POST /admin/data-quality/scan                trigger a (bounded) persist scan
    POST /admin/data-quality/issue/<id>/resolve  resolve / ignore an issue
    POST /admin/data-quality/duplicate/<id>/review  confirm / reject a candidate

Business logic lives in data_quality.* services; this file only does auth,
request parsing, and rendering. No raw HTML is rendered from data values, no
Telegram/user-private data is shown (the dissertations table has none), and the
listing is server-side filtered + paginated (no N+1).
"""

from __future__ import annotations

import csv
import io

from flask import (Blueprint, Response, abort, current_app, flash, jsonify,
                   redirect, render_template, request, url_for)
from flask_login import current_user, login_required

# Reuse the EXISTING admin authorization + logging helpers — no new auth system.
from security import admin_required, log_exception

data_quality_bp = Blueprint("data_quality", __name__)

PER_PAGE = 50
# Review cards are tall (two records side by side); a screenful at a time keeps
# the page light while the keyboard flow does the paging.
DUP_PER_PAGE = 25
# A synchronous web request must not scan all 28k+ rows; full scans use the CLI.
MAX_DASHBOARD_SCAN = 3000
DEFAULT_DASHBOARD_SCAN = 1000


def _filters_from_request():
    keys = ("issue_code", "severity", "status", "run_id", "record_id",
            "organization", "year", "specialty")
    out = {}
    for k in keys:
        v = (request.args.get(k) or "").strip()
        if v:
            out[k] = v
    # numeric coercions where the repository expects ints
    for k in ("run_id", "record_id"):
        if k in out:
            try:
                out[k] = int(out[k])
            except ValueError:
                out.pop(k, None)
    return out


@data_quality_bp.route("/admin/data-quality")
@login_required
@admin_required
def dashboard():
    from data import get_connection
    from data_quality import repository as repo
    from data_quality.quality_service import QualityReportService

    page = max(1, request.args.get("page", 1, type=int))
    filters = _filters_from_request()

    conn = get_connection()
    try:
        if not repo.dq_tables_exist(conn):
            return render_template("admin_data_quality.html", tables_ready=False,
                                   summary=None, issues=[], duplicates=[],
                                   filters=filters, page=1, total_pages=1,
                                   total_issues=0)

        summary = QualityReportService(conn).dashboard_summary(
            filters.get("run_id"))
        total_issues = repo.count_issues(conn, filters)
        total_pages = max(1, (total_issues + PER_PAGE - 1) // PER_PAGE)
        page = min(page, total_pages)
        issues = repo.list_issues(conn, filters, PER_PAGE, (page - 1) * PER_PAGE)
        duplicates = repo.list_duplicates(
            conn, request.args.get("dup_status", "pending"), 50, 0)
        return render_template(
            "admin_data_quality.html", tables_ready=True, summary=summary,
            issues=issues, duplicates=duplicates, filters=filters, page=page,
            total_pages=total_pages, total_issues=total_issues)
    except Exception as exc:  # noqa: BLE001
        log_exception(current_app.logger, "data-quality admin action failed")
        abort(500)
    finally:
        conn.close()


@data_quality_bp.route("/admin/data-quality/scan", methods=["POST"])
@login_required
@admin_required
def trigger_scan():
    from data import get_connection
    from data_quality.config import get_config
    from data_quality.quality_service import DataQualityService

    limit = request.form.get("limit", DEFAULT_DASHBOARD_SCAN, type=int) or DEFAULT_DASHBOARD_SCAN
    limit = max(1, min(limit, MAX_DASHBOARD_SCAN))

    conn = get_connection()
    try:
        result = DataQualityService(conn, get_config()).scan(
            mode="persist", limit=limit)
        if result.status == "completed":
            flash(f"Skaner tugadi: {result.summary.checked_records} yozuv tekshirildi, "
                  f"{result.summary.issue_count} muammo, "
                  f"{result.summary.duplicate_count} dublikat nomzod.", "success")
        else:
            flash("Skanerlashda xatolik yuz berdi.", "danger")
    except Exception as exc:  # noqa: BLE001
        log_exception(current_app.logger, "data-quality admin action failed")
        flash("Skanerlashda xatolik yuz berdi.", "danger")
    finally:
        conn.close()
    return redirect(url_for("data_quality.dashboard"))


@data_quality_bp.route("/admin/data-quality/issue/<int:issue_id>/resolve",
                       methods=["POST"])
@login_required
@admin_required
def resolve_issue(issue_id):
    from data import get_connection
    from data_quality import repository as repo

    status = request.form.get("status", "resolved")
    if status not in ("resolved", "ignored", "open"):
        abort(400)
    conn = get_connection()
    try:
        repo.resolve_issue(conn, issue_id, getattr(current_user, "id", None), status)
        conn.commit()
        flash("Muammo holati yangilandi.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log_exception(current_app.logger, "data-quality admin action failed")
        flash("Amalni bajarib bo‘lmadi.", "danger")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("data_quality.dashboard"))


@data_quality_bp.route("/admin/data-quality/duplicates")
@login_required
@admin_required
def duplicate_queue():
    """Review queue: 1 597 candidates are not reviewable from the dashboard's
    50-row preview, and they are not equally worth the time — see
    data_quality.duplicate_review for the ordering/bucket rationale."""
    from data import get_connection
    from data_quality import duplicate_review as review
    from data_quality import repository as repo

    page = max(1, request.args.get("page", 1, type=int))
    filters = _duplicate_filters_from_request()

    conn = get_connection()
    try:
        if not repo.dq_tables_exist(conn):
            return render_template("admin_duplicate_review.html",
                                   tables_ready=False, candidates=[],
                                   progress=None, filters=filters, page=1,
                                   total_pages=1, total=0)

        total = repo.count_duplicates(conn, filters)
        total_pages = max(1, (total + DUP_PER_PAGE - 1) // DUP_PER_PAGE)
        page = min(page, total_pages)
        rows = repo.list_duplicates_for_review(
            conn, filters, DUP_PER_PAGE, (page - 1) * DUP_PER_PAGE)
        candidates = [review.decorate(r) for r in rows]
        progress = review.progress_view(repo.duplicate_progress(conn))
        return render_template(
            "admin_duplicate_review.html", tables_ready=True,
            candidates=candidates, progress=progress, filters=filters,
            page=page, total_pages=total_pages, total=total,
            level_labels=review.LEVEL_LABEL, status_labels=review.STATUS_LABEL,
            bucket_labels=review.BUCKET_LABEL)
    except ValueError:
        abort(400)                      # a hand-edited filter value
    except Exception:  # noqa: BLE001
        log_exception(current_app.logger, "data-quality admin action failed")
        abort(500)
    finally:
        conn.close()


def _duplicate_filters_from_request():
    out = {}
    for key in ("status", "match_level", "bucket"):
        value = (request.args.get(key) or "").strip()
        if value:
            out[key] = value
    out.setdefault("status", "pending")   # a queue shows unreviewed work first
    return out


@data_quality_bp.route("/admin/data-quality/duplicate/<int:dup_id>/review",
                       methods=["POST"])
@login_required
@admin_required
def review_duplicate(dup_id):
    """Set a candidate's status. NEVER deletes or merges the records — merge is
    a separate, later job. Answers JSON for the keyboard-driven queue and a
    redirect for the plain form on the dashboard, so both share one authz path.
    """
    from data import get_connection
    from data_quality import repository as repo

    status = request.form.get("status")
    # NOTE: 'merged' is intentionally NOT accepted here — merge is Phase 2.
    if status not in ("confirmed", "rejected", "pending"):
        abort(400)
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    conn = get_connection()
    try:
        updated = repo.review_duplicate(conn, dup_id, status,
                                        getattr(current_user, "id", None))
        conn.commit()
        if wants_json:
            if not updated:
                return jsonify(ok=False, error="not_found"), 404
            return jsonify(ok=True, id=dup_id, status=status)
        flash("Dublikat nomzod holati yangilandi.", "success")
    except Exception:  # noqa: BLE001
        conn.rollback()
        log_exception(current_app.logger, "data-quality admin action failed")
        if wants_json:
            return jsonify(ok=False, error="failed"), 500
        flash("Amalni bajarib bo‘lmadi.", "danger")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("data_quality.dashboard"))


@data_quality_bp.route("/admin/data-quality/export.csv")
@login_required
@admin_required
def export_csv():
    from data import get_connection
    from data_quality import repository as repo
    from data_quality.config import get_config

    filters = _filters_from_request()
    cap = get_config().max_export_rows

    def generate():
        conn = get_connection()
        try:
            buf = io.StringIO()
            writer = csv.writer(buf)
            header = ["record_id", "issue_code", "severity", "field",
                      "description", "original_value", "normalized_preview",
                      "status", "detected_at"]
            writer.writerow(header)
            yield _flush(buf)
            if not repo.dq_tables_exist(conn):
                return
            for row in repo.iter_issues_for_export(conn, filters, cap):
                writer.writerow([
                    row["record_id"],
                    _csv_safe(row["issue_code"]),
                    _csv_safe(row["severity"]),
                    _csv_safe(row["field_name"]),
                    _csv_safe(row["description"]),
                    _csv_safe(row["original_value"]),
                    _csv_safe(row["normalized_value"]),
                    _csv_safe(row["status"]),
                    row["created_at"].isoformat() if row["created_at"] else "",
                ])
                yield _flush(buf)
        finally:
            conn.close()

    return Response(
        generate(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=data_quality_issues.csv"})


def _flush(buf: io.StringIO) -> str:
    data = buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    return data


def _csv_safe(value) -> str:
    """Neutralise CSV/formula injection: prefix a leading =,+,-,@ with a quote so
    spreadsheet apps don't execute it. Also strip control characters."""
    if value is None:
        return ""
    s = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if s and s[0] in ("=", "+", "-", "@"):
        s = "'" + s
    return s
