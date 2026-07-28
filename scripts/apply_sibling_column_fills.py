#!/usr/bin/env python3
"""FILL 1 — muassasa <- ilmiy_kengash (docs/sibling_column_analysis.md ga asosan).

    python scripts/apply_sibling_column_fills.py                 # dry-run (default)
    python scripts/apply_sibling_column_fills.py --confirm        # bazaga yozadi
    python scripts/apply_sibling_column_fills.py --limit 50       # tez sinov

QOIDA (faqat shu — boshqa hech qanday sibling-fill bu skriptda YO'Q):
    `muassasa` bo'sh VA `ilmiy_kengash` to'ldirilgan VA `ilmiy_kengash`
    "ҳузуридаги" so'zi bilan TUGAMASA ("Тошкент давлат аграр университети" —
    ko'chiriladi; "Чирчиқ давлат педагогика университети ҳузуридаги" —
    o'tkazib yuboriladi, chunki bu ilmiy kengash nomi, muassasa emas).

    Qiymat TO'G'RIDAN-TO'G'RI ko'chiriladi — hech qanday fuzzy/institution_map
    moslashtirish YO'Q (data-loss xavfi past bo'lishi uchun ataylab shunday).

Guarantees (apply_data_quality_fixes.py bilan bir xil naqsh)
    * DRY-RUN DEFAULT. --confirm bo'lmasa hech narsa yozilmaydi.
    * Har bir yozuv uchun BITTA tranzaksiya: data_quality_fix_log qatori
      UPDATE dan OLDIN yoziladi, keyin UPDATE, ikkalasi birga commit bo'ladi
      yoki hech biri (xato bo'lsa to'liq rollback).
    * Yozishdan oldin `muassasa` HOZIR ham bo'shligi tranzaksiya ichida qayta
      tekshiriladi — mavjud qiymat hech qachon ustidan yozilmaydi.
    * Barcha o'zgarishlar bitta run_type='sibling_fill' run'ni bo'lishadi;
      oxirida rollback komandasi chop etiladi
      (scripts/rollback_data_quality_fixes.py — umumiy, run_type ga bog'liq emas).
    * Mos keladigan ochiq MISSING_ORG issue topilsa — unga bog'lanadi va
      'fixed' deb belgilanadi (rollback uni qayta 'open' qiladi). Topilmasa
      ham fix baribir qo'llanadi — issue faqat izoh uchun, shart emas.

DB ga ulanmadi — yozildi, staging/production da operator ishlatadi.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from typing import Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_quality import repository as repo  # noqa: E402
from data_quality.cli_common import resolve_connection, truncate  # noqa: E402

RUN_TYPE = "sibling_fill"
FIX_TABLE = "dissertations"
SCOPE = repo.SCOPE_SIBLING_FILL
ISSUE_CODE = "MISSING_ORG"
FIELD = "muassasa"
SOURCE_COLUMN = "ilmiy_kengash"

# The one exclusion the rule is built around: a value that names a KENGASH
# (scientific council), not the institution the dissertation was done at.
_COUNCIL_SUFFIX = "ҳузуридаги"

APPLIED = "APPLIED"
SKIPPED_NOT_EMPTY, SKIPPED_GONE, SKIPPED_COUNCIL, FAILED = (
    "SKIPPED_NOT_EMPTY", "SKIPPED_GONE", "SKIPPED_COUNCIL", "FAILED")


def log(msg: str) -> None:
    from datetime import datetime
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def is_council_not_institution(value) -> bool:
    """True when ``ilmiy_kengash`` names the council rather than the
    institution — the exact, only exclusion in the approved rule."""
    if _blank(value):
        return False
    return str(value).strip().rstrip(" .,;").lower().endswith(_COUNCIL_SUFFIX)


def is_fillable(muassasa, ilmiy_kengash) -> bool:
    return (_blank(muassasa) and not _blank(ilmiy_kengash)
            and not is_council_not_institution(ilmiy_kengash))


def _default_actor() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:              # cron/konteynerda login nomi bo'lmasligi mumkin
        return "cli"


def _require_manual_transactions(conn) -> None:
    """Atomicity depends on explicit commits — refuse to run on an autocommit
    connection, where the fix and its log row would land separately."""
    if getattr(conn, "autocommit", False):
        try:
            conn.autocommit = False
        except Exception:
            raise SystemExit("Refusing to apply: connection is in autocommit mode "
                             "(fix + fix-log could not be made atomic).")


# ── ma'lumotlar bazasi (faqat kandidatlarni o'qish) ─────────────────────────
def iter_candidates(conn, limit: Optional[int] = None,
                    record_id: Optional[int] = None,
                    batch_size: int = 1000) -> Iterator[Dict]:
    """`muassasa` bo'sh, `ilmiy_kengash` to'ldirilgan yozuvlarni oqim
    sifatida qaytaradi (server-side cursor — 1600+ qatorni xotiraga to'liq
    yuklamaslik uchun). "ҳузуридаги" istisnosi bu yerda EMAS, chaqiruvchida
    (``is_fillable``) qo'llanadi — SQL faqat "ikkalasi ham bor" darajasida
    filtrlaydi. Faqat SELECT."""
    keys = ("id", FIELD, SOURCE_COLUMN)
    cols = ", ".join(keys)
    where = (f"WHERE ({FIELD} IS NULL OR TRIM({FIELD}) = '') "
             f"AND {SOURCE_COLUMN} IS NOT NULL AND TRIM({SOURCE_COLUMN}) <> ''")
    if record_id is not None:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {cols} FROM dissertations WHERE id = %s",
                       (record_id,))
            row = cur.fetchone()
            if row:
                yield dict(zip(keys, row))
        return

    sql = f"SELECT {cols} FROM dissertations {where} ORDER BY id"
    params: Tuple = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)

    cur = conn.cursor(name="sibling_fill_cursor")
    cur.itersize = batch_size
    try:
        cur.execute(sql, params)
        for row in cur:
            yield dict(zip(keys, row))
    finally:
        cur.close()


def fetch_open_missing_org_issues(conn, record_ids: List[int]) -> Dict[int, int]:
    """record_id -> issue_id, faqat status='open' MISSING_ORG uchun. Faqat
    izoh maqsadida (fix issue'ga bog'lanadi va yopiladi); topilmasa ham fix
    baribir qo'llanadi. Faqat SELECT."""
    if not record_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT record_id, id FROM data_quality_issues "
            "WHERE issue_code = %s AND record_id = ANY(%s) AND status = 'open' "
            "ORDER BY id",
            (ISSUE_CODE, list(record_ids)))
        out: Dict[int, int] = {}
        for rid, issue_id in cur.fetchall():
            out.setdefault(rid, issue_id)          # birinchi (eng eski) ochiq issue
        return out


# ── qo'llash ─────────────────────────────────────────────────────────────────
def apply_fills(conn, candidates: List[Dict], *, target: str, actor: str,
                issues: Dict[int, int]) -> Dict:
    """Har bir kandidatni bitta tranzaksiyada qo'llaydi: fix_log UPDATE dan
    oldin, ikkalasi birga commit bo'ladi yoki hech biri."""
    stats = {"considered": len(candidates), "applied": 0, "skipped": 0,
             "run_id": None}
    fix_run_id = None
    run_uncommitted = False

    for cand in candidates:
        record_id = cand["id"]
        try:
            found, current = repo.fetch_current_value(
                conn, record_id, FIELD, table_name=FIX_TABLE, scope=SCOPE)
            if not found:
                cand["apply_status"] = SKIPPED_GONE
                log(f"  !! {record_id}: yozuv topilmadi — o'tkazildi")
                stats["skipped"] += 1
                continue
            if not _blank(current):
                # Hisobotdan keyin (yoki shu run ichida) to'ldirilgan.
                cand["apply_status"] = SKIPPED_NOT_EMPTY
                log(f"  !! {record_id}.{FIELD}: endi bo'sh emas "
                    f"({truncate(current, 40)!r}) — o'tkazildi")
                stats["skipped"] += 1
                continue

            # Manba ustuni (ilmiy_kengash) yozish-scope'ga kirmaydi (faqat
            # o'qiladi), shuning uchun repo.fetch_current_value orqali emas,
            # kandidat SELECT'ida allaqachon olingan qiymatdan foydalanamiz.
            # Poyga holati (kimdir shu oraliqda ilmiy_kengash'ni o'zgartirgan
            # bo'lishi ehtimoli) muassasa'ning o'zi kabi keng emas — bu ustun
            # boshqa hech qanday yozuvchi tomonidan tegilmaydi (docs/
            # data_quality_inventory.md: "out of Phase-1 scope").
            kengash_now = cand["ilmiy_kengash"]
            if is_council_not_institution(kengash_now):
                cand["apply_status"] = SKIPPED_COUNCIL
                stats["skipped"] += 1
                continue

            new_value = kengash_now

            if fix_run_id is None:
                fix_run_id = repo.create_run(
                    conn, RUN_TYPE, total_records=len(candidates),
                    metadata={"mode": "apply", "rule": "muassasa<-ilmiy_kengash",
                              "target": target, "applied_by": actor})
                run_uncommitted = True

            issue_id = issues.get(record_id)
            print(f"  [{record_id}] {FIELD}: "
                  f"{truncate(current)!r} -> {truncate(new_value)!r}")

            # Audit FIRST, in the same transaction as the UPDATE.
            repo.log_fix(conn, run_id=fix_run_id, issue_id=issue_id,
                        record_id=record_id, table_name=FIX_TABLE,
                        column_name=FIELD, value_before=current,
                        value_after=new_value, issue_code=ISSUE_CODE,
                        applied_by=actor, scope=SCOPE)
            affected = repo.update_record_field(
                conn, record_id, FIELD, new_value, table_name=FIX_TABLE, scope=SCOPE)
            if affected != 1:
                raise RuntimeError(f"{affected} qator o'zgardi, 1 kutilgandi")
            if issue_id is not None:
                repo.record_issue_values(conn, issue_id, current, new_value)
                repo.mark_issue_fixed(conn, issue_id)
            conn.commit()
            run_uncommitted = False
            cand["apply_status"] = APPLIED
            stats["applied"] += 1
        except Exception as exc:                 # noqa: BLE001
            conn.rollback()
            if run_uncommitted:
                fix_run_id, run_uncommitted = None, False
            cand["apply_status"] = FAILED
            stats["skipped"] += 1
            logging.error("fill failed for record %s: %s",
                          record_id, exc.__class__.__name__)

    if fix_run_id is not None:
        repo.complete_run(conn, fix_run_id, status="completed",
                          checked_records=stats["considered"],
                          issue_count=stats["applied"], duplicate_count=0)
        conn.commit()
    stats["run_id"] = fix_run_id
    return stats


# ── CLI ─────────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FILL 1: muassasa <- ilmiy_kengash "
                    "(faqat 'ҳузуридаги' bilan tugamagan qatorlar)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="Ko'rsatadi, hech narsa yozmaydi (default)")
    g.add_argument("--confirm", action="store_true",
                   help="Bazaga haqiqatan yozadi")
    p.add_argument("--limit", type=int, default=None,
                   help="faqat birinchi N nomzod (tez sinov uchun)")
    p.add_argument("--record-id", type=int, default=None, help="faqat bitta yozuv")
    p.add_argument("--applied-by", default=None,
                   help="fix_log dagi ijrochi yorlig'i (default: cli:<user>)")
    p.add_argument("--database-url", default=None)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    confirm = bool(args.confirm)
    actor = args.applied_by or _default_actor()
    conn, target = resolve_connection(args.database_url)
    print(f"→ Baza: {target}   mode={'CONFIRM' if confirm else 'DRY-RUN'}")
    if not confirm:
        print("  (dry-run: bazaga hech narsa yozilmaydi)")
    else:
        _require_manual_transactions(conn)

    candidates = list(iter_candidates(conn, limit=args.limit,
                                      record_id=args.record_id))
    # SQL WHERE allaqachon "muassasa bo'sh, ilmiy_kengash to'ldirilgan"ni
    # tanlaydi; "ҳузуридаги" istisnosi Python tomonda — kelajakda boshqa
    # kengash-yorliqlari qo'shilsa ham SQL o'zgarmasin.
    candidates = [c for c in candidates
                 if is_fillable(c["muassasa"], c["ilmiy_kengash"])]
    conn.rollback()          # o'qish tranzaksiyasini yopamiz

    log(f"{len(candidates)} ta nomzod topildi (muassasa bo'sh, ilmiy_kengash "
        f"to'ldirilgan, 'ҳузуридаги' bilan tugamaydi)")

    if confirm:
        issues = fetch_open_missing_org_issues(conn, [c["id"] for c in candidates])
        conn.rollback()
        stats = apply_fills(conn, candidates, target=target, actor=actor,
                            issues=issues)
    else:
        # Preview only — nothing is "skipped" here, it is all un-applied yet.
        stats = {"considered": len(candidates), "applied": 0,
                 "skipped": 0, "run_id": None}
        for c in candidates:
            print(f"  [{c['id']}] {FIELD}: {truncate(c['muassasa'])!r} -> "
                  f"{truncate(c['ilmiy_kengash'])!r}")

    try:
        conn.close()
    except Exception:
        pass

    print("\n" + "=" * 50)
    print(f"considered : {stats['considered']}")
    print(f"{'applied' if confirm else 'would apply'} : "
          f"{stats['applied'] if confirm else stats['considered']}")
    print(f"skipped    : {stats['skipped']}")
    if not confirm:
        print("\n(bazaga hech narsa yozilmadi — --confirm bilan qayta ishga tushiring)")
    elif stats["run_id"] is not None:
        print(f"sibling_fill run id : {stats['run_id']}")
        print(f"\nqaytarish:  python3 scripts/rollback_data_quality_fixes.py "
              f"--run-id {stats['run_id']} --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
