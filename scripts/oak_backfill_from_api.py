"""OAK e'lonlaridan (oak.uz /api/elonlar) dissertations ustunlarini tuzatish.

Muammo (2026-11-13 tahlili): prod'dagi opponent_1/2/3 ning ~81% i QO'SHNI
e'londan (+1) kelgan — eski HTML scraper/import siljishi. Rahbar, muassasa,
IK, yetakchi tashkilot siljimagan.

Qoidalar
  Opponentlar (manba — API, scraper.py parseri bilan):
    replace  DB'dagi opponent ismlari o'z e'loniga mos emas (qo'shniniki, sarlavha
             bo'lagi, bo'linmagan) → uchala ustun API qiymati bilan almashtiriladi.
    fill     DB'dagi ismlar o'z e'loniga mos, faqat bo'sh ustunlar to'ldiriladi.
    clear    API'da opponent yo'q, DB qiymati esa qo'shni (±1/±2) e'lonniki ekani
             isbotlangan → bo'shatiladi.
    (tegilmaydi) DB o'z e'loniga to'liq mos; yoki API'da yo'q va qo'shniniki
             ekani isbotlanmagan (isbotlanmagan.csv ga chiqadi).
    Ishonchsiz matn (split_opponents bo'lmagan) — to'liq matn opponent_1 ga,
    reason="ishonchsiz".
  Boshqa ustunlar (ilmiy_kengash, ilmiy_kengash_raqami, yetakchi_tashkilot,
  yonalish) — FAQAT bo'sh bo'lsa to'ldiriladi.

Buyruqlar
  fetch  --api-dir DIR                     ro'yxat sahifalarini yuklash (1 so'rov/s, resume)
  plan   --api-dir DIR --out DIR [--rows-csv F] [--v3 XLSX]
         dry-run: DB faqat o'qiladi (yoki --rows-csv nusxa); plan.csv, summary.json,
         isbotlanmagan.csv, v3_farq.csv. Hech narsa yozilmaydi.
  apply  --plan plan.csv --batch ID --yes  har o'zgarish: log + UPDATE … WHERE eski
         qiymat o'zgarmagan bo'lsa; 500 tadan tranzaksiya.
  undo   --batch ID --yes                  yangi qiymat hali turgan joylarda eskisini qaytaradi.

DB: data.get_connection() (DATABASE_URL). Migratsiya 025 (dissertation_field_changes) kerak.
"""
import argparse
import csv
import glob
import gzip
import json
import os
import re
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scraper  # noqa: E402  — e'lon parseri (split_opponents va h.k.)
from institution_identity import normalize_institution_identity as _norm  # noqa: E402

csv.field_size_limit(10 ** 9)
SOURCE = "oak_api"
OPP_COLS = ("opponent_1", "opponent_2", "opponent_3")
FILL_COLS = {  # DB ustuni → _build_record kaliti
    "ilmiy_kengash": "IK muassasa",
    "ilmiy_kengash_raqami": "IK raqami",
    "yetakchi_tashkilot": "Yetakchi tashkilot",
    "yonalish": "Yonalish",
}
ALLOWED_COLS = set(OPP_COLS) | set(FILL_COLS)
NEIGHBOUR_OFFSETS = (1, -1, 2, -2)
_TITLE_SKIP = re.compile(r"(?i)^(profes|dots|доц|проф|phd|dsc|md|akad|акад|v\.b|в\.б)")


# ----------------------------------------------------------------- yordamchi
def name_key(value):
    """Ism kaliti: bosh harfli so'zlar (daraja/unvon so'zlari kichik harfli),
    translit + normallashtirilgan, 30 belgi. Kirill/lotin bir xil kalit beradi."""
    toks = re.findall(r"[А-ЯЁЎҚҒҲA-Z][^\s,.;:()]+", value or "")
    toks = [_norm(t) for t in toks if not _TITLE_SKIP.match(t)]
    return " ".join(t for t in toks if len(t) > 2)[:30]


def _blank(v):
    return not (v or "").strip()


# ----------------------------------------------------------------- API
def load_api(api_dir):
    """{oak_id: rec} — rec: scraper._build_record + 'ishonchsiz'."""
    out = {}
    for path in sorted(glob.glob(os.path.join(api_dir, "*.json.gz"))):
        data = json.loads(gzip.open(path).read())
        for raw in (data.get("items") or [data]):
            try:
                it = scraper.api_list_item(raw)
            except (KeyError, TypeError, ValueError):
                continue
            parsed = scraper.parse_announcement(it["raw_text"])
            rec = scraper._build_record(it, parsed)
            rec["ishonchsiz"] = bool(parsed.get("opponentlar_ishonchsiz"))
            out[it["id"]] = rec
    return out


class Neighbours:
    """E'lonlar ID tartibida (siljish shu tartibda bo'lgan)."""

    def __init__(self, api):
        self.api = api
        self.ids = sorted(api)
        self.pos = {i: k for k, i in enumerate(self.ids)}

    def opp_keys(self, oak_id, offset):
        p = self.pos.get(oak_id)
        if p is None or not 0 <= p + offset < len(self.ids):
            return set()
        return {name_key(o) for o in self.api[self.ids[p + offset]]["Opponentlar"]} - {""}

    def find(self, oak_id, key):
        for off in NEIGHBOUR_OFFSETS:
            if key and key in self.opp_keys(oak_id, off):
                return off
        return None


# ----------------------------------------------------------------- qarorlar
def plan_opponents(db_row, rec, nb):
    """→ (changes [(col, old, new, action, reason)], status, reason)."""
    oak_id = int(db_row["oak_id"])
    db = [db_row.get(c) or "" for c in OPP_COLS]
    filled = [v for v in db if not _blank(v)]
    db_keys = [k for k in (name_key(v) for v in filled) if k]
    unnamed = len(filled) - len(db_keys)      # sarlavha bo'lagi va h.k. — ismsiz qiymat
    own = list(rec["Opponentlar"])

    if not own:
        if not filled:
            return [], "bosh", ""
        off = nb.find(oak_id, db_keys[0] if db_keys else "")
        if off is not None:
            reason = f"API'da opponent yo'q; DB qiymati qo'shni {off:+d} e'lonniki"
            return ([(c, db[i], "", "clear", reason) for i, c in enumerate(OPP_COLS) if not _blank(db[i])],
                    "clear", reason)
        return [], "isbotlanmagan", "API'da opponent yo'q; DB qiymati qo'shniniki ekani isbotlanmadi"

    if rec.get("ishonchsiz"):
        target, base_reason = [own[0], "", ""], "ishonchsiz"
    else:
        target, base_reason = (own + ["", "", ""])[:3], ""
    own_keys = {name_key(o) for o in own} - {""}

    if db_keys and not unnamed and all(k in own_keys for k in db_keys):
        # o'z e'loniga mos — faqat bo'sh ustunlar
        reason = "; ".join(x for x in ("bo'sh ustun to'ldirildi", base_reason) if x)
        ch = [(c, db[i], target[i], "fill", reason) for i, c in enumerate(OPP_COLS)
              if _blank(db[i]) and target[i] and name_key(target[i]) not in db_keys]
        return ch, ("fill" if ch else "mos"), reason

    if not filled:
        reason = "; ".join(x for x in ("opponentlar bo'sh edi", base_reason) if x)
        ch = [(c, db[i], target[i], "fill", reason) for i, c in enumerate(OPP_COLS) if target[i]]
        return ch, "fill", reason

    off = nb.find(oak_id, db_keys[0]) if db_keys else None
    why = (f"qo'shni {off:+d} e'lonniki" if off is not None else
           "sarlavha bo'lagi/ismsiz" if unnamed else "o'z e'loniga mos emas")
    reason = "; ".join(x for x in (why, base_reason) if x)
    ch = [(c, db[i], target[i], "replace", reason) for i, c in enumerate(OPP_COLS)
          if (db[i] or "").strip() != target[i].strip()]
    return ch, "replace", reason


def plan_fill(db_row, rec):
    ch = []
    for col, key in FILL_COLS.items():
        new = (rec.get(key) or "").strip()
        if _blank(db_row.get(col)) and new:
            ch.append((col, db_row.get(col) or "", new, "fill", "bo'sh ustun to'ldirildi"))
    return ch


def build_plan(rows, api):
    nb = Neighbours(api)
    plan, unproven, stats = [], [], Counter()
    for r in rows:
        try:
            oak_id = int(str(r.get("oak_id") or "").strip())
        except ValueError:
            stats["oak_id yo'q"] += 1
            continue
        rec = api.get(oak_id)
        if rec is None:
            stats["API'da yo'q"] += 1
            continue
        ch_o, status, reason = plan_opponents(r, rec, nb)
        stats["opp:" + status] += 1
        if status == "isbotlanmagan":
            unproven.append({"dissertation_id": r["id"], "oak_id": oak_id, "opponent_1": r.get("opponent_1", ""),
                             "reason": reason})
        ch = ch_o + plan_fill(r, rec)
        for col, old, new, action, why in ch:
            raw_old = r.get(col)                     # None = NULL (CSV'da "1" belgisi)
            plan.append({"dissertation_id": r["id"], "oak_id": oak_id, "column": col,
                         "old_value": "" if raw_old is None else raw_old,
                         "old_is_null": "1" if raw_old is None else "",
                         "new_value": new, "action": action, "reason": why})
        stats["qator o'zgaradi" if ch else "qator tegilmaydi"] += 1
    for p in plan:
        stats[f"{p['action']}:{p['column']}"] += 1
    return plan, unproven, stats


def compare_v3(plan_rows_by_id, rows, api, v3_path, max_id=30600):
    """Rejadagi yakuniy opponentlar ↔ v3 Excel (oak_id ≤ max_id)."""
    from openpyxl import load_workbook
    ws = load_workbook(v3_path, read_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h) for h in next(it)]
    ix = {h: i for i, h in enumerate(hdr)}
    v3 = {}
    for r in it:
        try:
            v3.setdefault(int(float(r[ix["ID"]])), [str(r[ix[c]] or "") for c in ("1_oponent", "2_oponent", "3_oponent")])
        except (TypeError, ValueError):
            continue
    out, c = [], Counter()
    for r in rows:
        try:
            oak_id = int(r["oak_id"])
        except (TypeError, ValueError):
            continue
        if oak_id > max_id or oak_id not in v3 or oak_id not in api:
            continue
        final = [r.get(col) or "" for col in OPP_COLS]
        for p in plan_rows_by_id.get(str(r["id"]), []):
            if p["column"] in OPP_COLS:
                final[OPP_COLS.index(p["column"])] = p["new_value"]
        a = {name_key(x) for x in final if x.strip()} - {""}
        b = {name_key(x) for x in v3[oak_id] if x.strip()} - {""}
        if a == b:
            c["mos"] += 1
        else:
            k = "v3 bo'sh" if not b else "yakuniy bo'sh" if not a else "farq"
            c[k] += 1
            out.append({"oak_id": oak_id, "dissertation_id": r["id"], "turi": k,
                        "yakuniy": " | ".join(x for x in final if x), "v3": " | ".join(x for x in v3[oak_id] if x)})
    return out, c


# ----------------------------------------------------------------- DB
def _connect(readonly):
    from data import get_connection
    conn = get_connection()
    if readonly and hasattr(conn, "set_session"):
        conn.set_session(readonly=True)
    return conn


ROW_SQL = ("SELECT id, oak_id, opponent_1, opponent_2, opponent_3, ilmiy_kengash, "
           "ilmiy_kengash_raqami, yetakchi_tashkilot, yonalish FROM dissertations "
           "WHERE oak_id IS NOT NULL AND merged_into_id IS NULL ORDER BY id")
ROW_COLS = ("id", "oak_id", "opponent_1", "opponent_2", "opponent_3", "ilmiy_kengash",
            "ilmiy_kengash_raqami", "yetakchi_tashkilot", "yonalish")


def load_rows_db():
    """NULL lar None bo'lib qoladi — reja va undo ularni '' dan farqlaydi."""
    conn = _connect(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(ROW_SQL)
            rows = [dict(zip(ROW_COLS, [None if v is None else str(v) for v in r])) for r in cur.fetchall()]
        conn.rollback()
    finally:
        conn.close()
    return rows


def load_rows_csv(path):
    """Tahlil uchun (CSV NULL ni '' dan farqlamaydi) — apply uchun reja DB'dan tuziladi;
    CSV rejasi qo'llansa, NULL katakli qatorlar 'stale' bo'lib o'tkaziladi."""
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r.get("merged_into_id"):
            continue
        rows.append({c: r.get(c, "") or "" for c in ROW_COLS})
    return rows


# IS NOT DISTINCT FROM: NULL va '' alohida — reja tuzilgandan keyin o'zgargan
# (yoki NULL/'' farqi bor) katak yozilmaydi.
UPDATE_SQL = ("UPDATE dissertations SET {col} = %s WHERE id = %s "
              "AND {col} IS NOT DISTINCT FROM %s RETURNING id")


def _old(p):
    return None if p.get("old_is_null") == "1" else (p.get("old_value") or "")
LOG_SQL = ("INSERT INTO dissertation_field_changes (batch_id, dissertation_id, oak_id, column_name, "
           "old_value, new_value, action, reason, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")


def apply_plan(conn, plan, batch_id, chunk=500):
    """→ Counter(updated, stale). stale = qiymat reja tuzilgandan keyin o'zgargan."""
    c = Counter()
    for start in range(0, len(plan), chunk):
        part = plan[start:start + chunk]
        with conn.cursor() as cur:
            for p in part:
                col = p["column"]
                if col not in ALLOWED_COLS:
                    raise ValueError(f"ruxsat etilmagan ustun: {col}")
                old = _old(p)
                cur.execute(UPDATE_SQL.format(col=col), (p["new_value"], int(p["dissertation_id"]), old))
                if cur.fetchone() is None:
                    c["stale"] += 1
                    continue
                cur.execute(LOG_SQL, (batch_id, int(p["dissertation_id"]), str(p["oak_id"]), col,
                                      old, p["new_value"], p["action"], p["reason"], SOURCE))
                c["updated"] += 1
        conn.commit()
    return c


UNDO_SELECT = ("SELECT id, dissertation_id, column_name, old_value, new_value FROM dissertation_field_changes "
               "WHERE batch_id = %s AND undone_at IS NULL ORDER BY id DESC")
UNDO_MARK = "UPDATE dissertation_field_changes SET undone_at = NOW() WHERE id = %s"


def undo_batch(conn, batch_id):
    c = Counter()
    with conn.cursor() as cur:
        cur.execute(UNDO_SELECT, (batch_id,))
        rows = cur.fetchall()
        for log_id, diss_id, col, old, new in rows:
            if col not in ALLOWED_COLS:
                raise ValueError(f"ruxsat etilmagan ustun: {col}")
            # log'dagi NULL aynan NULL bo'lib qaytadi
            cur.execute(UPDATE_SQL.format(col=col), (old, diss_id, new))
            if cur.fetchone() is None:
                c["o'zgargan (tegilmadi)"] += 1
                continue
            cur.execute(UNDO_MARK, (log_id,))
            c["qaytarildi"] += 1
    conn.commit()
    return c


# ----------------------------------------------------------------- fetch
def fetch(api_dir, lists=(8, 31), delay=1.0):
    import requests
    os.makedirs(api_dir, exist_ok=True)
    for lst in lists:
        page, last = 1, None
        while last is None or page <= last:
            path = os.path.join(api_dir, f"list{lst}_p{page}.json.gz")
            if os.path.exists(path):
                data = json.loads(gzip.open(path).read())
            else:
                data = scraper.fetch_list_page(lst, page)
                if data is None:
                    raise SystemExit(f"list={lst} page={page}: javob yo'q — to'xtatildi (qayta ishga tushiring)")
                with gzip.open(path + ".part", "wb") as f:
                    f.write(json.dumps(data, ensure_ascii=False).encode())
                os.replace(path + ".part", path)
                time.sleep(delay)
            last = data.get("lastPage") or page
            page += 1
        print(f"list={lst}: {last} sahifa")


# ----------------------------------------------------------------- CLI
def _write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--api-dir", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--api-dir", required=True); p.add_argument("--out", required=True)
    p.add_argument("--rows-csv", help="DB o'rniga dissertations nusxasi (CSV)")
    p.add_argument("--v3", help="v3 Excel (oak_id ≤ 30600 uchun solishtirish)")
    a = sub.add_parser("apply")
    a.add_argument("--plan", required=True); a.add_argument("--batch", required=True)
    a.add_argument("--yes", action="store_true")
    u = sub.add_parser("undo"); u.add_argument("--batch", required=True); u.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        fetch(args.api_dir)
        return 0

    if args.cmd == "plan":
        os.makedirs(args.out, exist_ok=True)
        api = load_api(args.api_dir)
        rows = load_rows_csv(args.rows_csv) if args.rows_csv else load_rows_db()
        plan, unproven, stats = build_plan(rows, api)
        _write_csv(os.path.join(args.out, "plan.csv"), plan,
                   ["dissertation_id", "oak_id", "column", "old_value", "old_is_null", "new_value",
                    "action", "reason"])
        _write_csv(os.path.join(args.out, "isbotlanmagan.csv"), unproven,
                   ["dissertation_id", "oak_id", "opponent_1", "reason"])
        summary = {"api_elon": len(api), "db_qator": len(rows), **stats}
        if args.v3:
            by_id = {}
            for pr in plan:
                by_id.setdefault(str(pr["dissertation_id"]), []).append(pr)
            diffs, vc = compare_v3(by_id, rows, api, args.v3)
            _write_csv(os.path.join(args.out, "v3_farq.csv"), diffs,
                       ["oak_id", "dissertation_id", "turi", "yakuniy", "v3"])
            summary.update({f"v3:{k}": v for k, v in vc.items()})
        with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
        for k in sorted(summary):
            print(f"{k}: {summary[k]}")
        return 0

    if not args.yes:
        ap.error("--yes kerak (bazaga yozadi)")
    conn = _connect(readonly=False)
    try:
        if args.cmd == "apply":
            plan = list(csv.DictReader(open(args.plan, encoding="utf-8-sig")))
            print(dict(apply_plan(conn, plan, args.batch)))
        else:
            print(dict(undo_batch(conn, args.batch)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
