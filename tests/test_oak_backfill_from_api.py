"""scripts/oak_backfill_from_api.py va lotin opponentlarni ajratish (P1+P2, P3).

Offline: API yozuvlari qo'lda quriladi; DB ulanishi soxta.
    python3 -m unittest tests.test_oak_backfill_from_api -v
"""
import csv
import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "oak-backfill-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import scraper  # noqa: E402
from scripts import oak_backfill_from_api as bf  # noqa: E402
from tests.test_oak_notify_single_path import FakeConn  # noqa: E402

A = "Алиев Али Алиевич, техника фанлари доктори, профессор"
B = "Каримов Карим Каримович, техника фанлари доктори, доцент"
C = "Салимов Салим Салимович, техника фанлари номзоди"
X = "Юсупов Юсуф Юсупович, тарих фанлари доктори"          # qo'shni e'lon opponenti
Y = "Носиров Носир Носирович, тарих фанлари доктори"


def rec(opps, ishonchsiz=False, **kw):
    r = {"Opponentlar": list(opps), "ishonchsiz": ishonchsiz, "IK muassasa": "", "IK raqami": "",
         "Yetakchi tashkilot": "", "Yonalish": ""}
    r.update(kw)
    return r


def row(rid, oak_id, o1="", o2="", o3="", **kw):
    r = {"id": str(rid), "oak_id": str(oak_id), "opponent_1": o1, "opponent_2": o2, "opponent_3": o3,
         "ilmiy_kengash": "", "ilmiy_kengash_raqami": "", "yetakchi_tashkilot": "", "yonalish": ""}
    r.update(kw)
    return r


# 100 → [A,B], 101 → [X,Y] (qo'shni +1), 102 → [] (2015 formati), 103 → [C]
API = {100: rec([A, B]), 101: rec([X, Y]), 102: rec([]), 103: rec([C])}


def plan_for(r, api=API):
    return bf.plan_opponents(r, api[int(r["oak_id"])], bf.Neighbours(api))


class NameKeyTests(unittest.TestCase):
    def test_cyrillic_and_latin_give_the_same_key(self):
        self.assertEqual(bf.name_key("Алиев Али Алиевич, т.ф.д., профессор"),
                         bf.name_key("Aliev Ali Alievich, texnika fanlari doktori"))

    def test_degree_words_are_ignored(self):
        self.assertEqual(bf.name_key("техника фанлари доктори, профессор Алиев Али Алиевич"),
                         bf.name_key("Алиев Али Алиевич"))


class OpponentPlanTests(unittest.TestCase):
    def test_neighbour_values_are_replaced(self):
        ch, status, reason = plan_for(row(1, 100, X, Y))
        self.assertEqual(status, "replace")
        self.assertIn("qo'shni +1", reason)
        self.assertEqual([(c, n) for c, o, n, a, r, src in ch],
                         [("opponent_1", A), ("opponent_2", B)])
        self.assertTrue(all(a == "replace" for c, o, n, a, r, src in ch))

    def test_own_values_are_left_alone(self):
        ch, status, _ = plan_for(row(1, 100, A, B))
        self.assertEqual((ch, status), ([], "mos"))

    def test_own_values_with_other_formatting_are_left_alone(self):
        ch, status, _ = plan_for(row(1, 100, "Алиев Али Алиевич, т.ф.д.", "Каримов Карим Каримович"))
        self.assertEqual((ch, status), ([], "mos"))

    def test_own_first_opponent_only_fills_the_empty_slot(self):
        ch, status, _ = plan_for(row(1, 100, A))
        self.assertEqual(status, "fill")
        self.assertEqual([(c, o, n, a) for c, o, n, a, r, src in ch], [("opponent_2", "", B, "fill")])

    def test_own_first_but_foreign_second_is_replaced(self):
        ch, status, _ = plan_for(row(1, 100, A, X))
        self.assertEqual(status, "replace")
        self.assertEqual([(c, n) for c, o, n, a, r, src in ch], [("opponent_2", B)])

    def test_empty_row_is_filled(self):
        ch, status, _ = plan_for(row(1, 100))
        self.assertEqual(status, "fill")
        self.assertEqual([n for c, o, n, a, r, src in ch], [A, B])

    def test_header_fragment_is_replaced(self):
        ch, status, reason = plan_for(row(1, 100, "нинг Ф.И.Ш"))
        self.assertEqual(status, "replace")
        self.assertIn("sarlavha", reason)

    def test_unsplit_cell_is_replaced_by_split_values(self):
        ch, status, _ = plan_for(row(1, 100, A + ", " + B))
        self.assertEqual(status, "replace")
        self.assertEqual([n for c, o, n, a, r, src in ch], [A, B])

    def test_no_api_opponents_and_proven_neighbour_is_cleared(self):
        # 102 da opponent yo'q; DB'da 101 (−1) niki
        ch, status, reason = plan_for(row(1, 102, X, Y))
        self.assertEqual(status, "clear")
        self.assertIn("qo'shni -1", reason)
        self.assertEqual([(c, n, a) for c, o, n, a, r, src in ch],
                         [("opponent_1", "", "clear"), ("opponent_2", "", "clear")])

    def test_no_api_opponents_and_unproven_is_untouched(self):
        ch, status, _ = plan_for(row(1, 102, "Бошқа Одам Одамович, т.ф.д."))
        self.assertEqual((ch, status), ([], "isbotlanmagan"))

    def test_no_api_opponents_and_empty_db(self):
        self.assertEqual(plan_for(row(1, 102))[:2], ([], "bosh"))

    def test_unreliable_api_without_v3_is_left_for_review(self):
        api = {**API, 100: rec(["ф.-м.ф.д. Ж.Р. Раимбаев ҳамда ф.-м.ф.д. Т.Р. Ахунов"], ishonchsiz=True)}
        ch, status, reason = plan_for(row(1, 100, X, Y), api)
        self.assertEqual((ch, status), ([], "ehtiyot"))
        self.assertIn("ishonchsiz", reason)


class FillTests(unittest.TestCase):
    def test_only_empty_columns_are_filled(self):
        r = row(1, 100, A, B, ilmiy_kengash="Мавжуд кенгаш")
        api = rec([A, B], **{"IK muassasa": "Янги", "IK raqami": "DSc.03/2025.27.12.Fil.07.03",
                             "Yetakchi tashkilot": "ТДУ", "Yonalish": "амалий"})
        ch = bf.plan_fill(r, api)
        self.assertEqual({c for c, *_ in ch}, {"ilmiy_kengash_raqami", "yetakchi_tashkilot", "yonalish"})
        self.assertTrue(all(a == "fill" and o == "" for c, o, n, a, rr, src in ch))


class BuildPlanTests(unittest.TestCase):
    def test_counts_and_rows(self):
        rows = [row(1, 100, X, Y), row(2, 101, X, Y), row(3, 102, X, Y), row(4, 999, A)]
        plan, unproven, stats, caution = bf.build_plan(rows, API)
        self.assertEqual(stats["opp:replace"], 1)
        self.assertEqual(stats["opp:mos"], 1)
        self.assertEqual(stats["opp:clear"], 1)
        self.assertEqual(stats["API'da yo'q"], 1)
        self.assertEqual({p["dissertation_id"] for p in plan}, {"1", "3"})
        self.assertEqual(unproven, [])


class ApplyUndoTests(unittest.TestCase):
    PLAN = [{"dissertation_id": "7", "oak_id": "100", "column": "opponent_1", "old_value": X,
             "new_value": A, "action": "replace", "reason": "qo'shni +1 e'lonniki"},
            {"dissertation_id": "8", "oak_id": "103", "column": "opponent_1", "old_value": "",
             "new_value": C, "action": "fill", "reason": "bo'sh"}]

    def test_apply_updates_only_unchanged_rows_and_logs_each(self):
        updated = {"7"}
        conn = FakeConn(rules=[("UPDATE dissertations SET opponent_1",
                                lambda p: [(int(p[1]),)] if str(p[1]) in updated else [])])
        c = bf.apply_plan(conn, self.PLAN, "b1")
        self.assertEqual(dict(c), {"updated": 1, "stale": 1})
        sql = conn.sql()
        self.assertIn("UPDATE dissertations SET opponent_1 = %s WHERE id = %s AND "
                      "opponent_1 IS NOT DISTINCT FROM %s RETURNING id", sql[0])
        self.assertNotIn("COALESCE", sql[0])
        logs = [e for e in conn.log if e[0] == "execute" and "dissertation_field_changes" in e[1]]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2][:7], ("b1", 7, "100", "opponent_1", X, A, "replace"))
        self.assertIn(("commit",), conn.log)

    def test_apply_rejects_unknown_columns(self):
        bad = [dict(self.PLAN[0], column="olim")]
        with self.assertRaises(ValueError):
            bf.apply_plan(FakeConn(), bad, "b1")

    def test_undo_restores_where_new_value_still_present(self):
        log_rows = [(11, 7, "opponent_1", X, A), (12, 8, "opponent_1", "", C)]
        conn = FakeConn(rules=[("FROM dissertation_field_changes WHERE batch_id", log_rows),
                               ("UPDATE dissertations SET opponent_1",
                                lambda p: [(p[1],)] if p[1] == 7 else [])])
        c = bf.undo_batch(conn, "b1")
        self.assertEqual(dict(c), {"qaytarildi": 1, "o'zgargan (tegilmadi)": 1})
        restore = [e for e in conn.log if e[0] == "execute" and e[1].startswith("UPDATE dissertations")]
        self.assertEqual(restore[0][2], (X, 7, A))
        marks = [e for e in conn.log if e[0] == "execute" and "SET undone_at" in e[1]]
        self.assertEqual([m[2] for m in marks], [(11,)])

    # --- NULL va '' farqi (staging 2026-11-13: undo NULL ni '' qilib qaytargan edi)
    def test_plan_keeps_null_distinct_from_empty(self):
        api = {**API}
        rows = [row(1, 100, None, None, None), row(2, 100, "", "", "")]
        for r in rows:
            r["yonalish"] = None if r["id"] == "1" else ""
        api[100] = rec([A, B], Yonalish="амалий")
        plan, _, _, _ = bf.build_plan(rows, api)
        by = {(p["dissertation_id"], p["column"]): p for p in plan}
        self.assertEqual(by[("1", "opponent_1")]["old_is_null"], "1")
        self.assertEqual(by[("1", "yonalish")]["old_is_null"], "1")
        self.assertEqual(by[("2", "opponent_1")]["old_is_null"], "")
        self.assertEqual(by[("2", "opponent_1")]["old_value"], "")

    def test_apply_passes_none_for_null_and_logs_null(self):
        plan = [dict(self.PLAN[1], old_is_null="1"), dict(self.PLAN[1], dissertation_id="9", old_is_null="")]
        conn = FakeConn(rules=[("UPDATE dissertations SET opponent_1", lambda p: [(int(p[1]),)])])
        bf.apply_plan(conn, plan, "b2")
        upd = [e[2] for e in conn.log if e[0] == "execute" and e[1].startswith("UPDATE dissertations")]
        self.assertEqual([u[2] for u in upd], [None, ""])          # NULL ≠ ''
        logs = [e[2] for e in conn.log if e[0] == "execute" and "dissertation_field_changes" in e[1]]
        self.assertEqual([l[4] for l in logs], [None, ""])          # log old_value

    def test_undo_restores_null_as_null(self):
        conn = FakeConn(rules=[("FROM dissertation_field_changes WHERE batch_id", [(21, 8, "opponent_1", None, C)]),
                               ("UPDATE dissertations SET opponent_1", lambda p: [(p[1],)])])
        bf.undo_batch(conn, "b2")
        restore = [e for e in conn.log if e[0] == "execute" and e[1].startswith("UPDATE dissertations")]
        self.assertEqual(restore[0][2], (None, 8, C))


class CliTests(unittest.TestCase):
    def test_plan_from_csv_writes_reports_and_no_db(self):
        tmp = tempfile.mkdtemp()
        api_dir = os.path.join(tmp, "api"); os.makedirs(api_dir)
        items = [{"id": "100", "date": "01.01.2026", "title": "Олим Олимовнинг фалсафа доктори (PhD)",
                  "fields": [{"label": "Расмий оппонентлар", "value": A + "; " + B}]},
                 {"id": "101", "date": "01.01.2026", "title": "Бошқа Олимнинг фалсафа доктори (PhD)",
                  "fields": [{"label": "Расмий оппонентлар", "value": X + "; " + Y}]}]
        with gzip.open(os.path.join(api_dir, "list8_p1.json.gz"), "wb") as f:
            f.write(json.dumps({"items": items}, ensure_ascii=False).encode())
        rows_csv = os.path.join(tmp, "rows.csv")
        with open(rows_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=bf.ROW_COLS); w.writeheader()
            w.writerow(row(1, 100, X, Y)); w.writerow(row(2, 101, X, Y))
        out = os.path.join(tmp, "out")
        with mock.patch.object(bf, "_connect", side_effect=AssertionError("DB kerak emas")):
            self.assertEqual(bf.main(["plan", "--api-dir", api_dir, "--rows-csv", rows_csv, "--out", out]), 0)
        plan = list(csv.DictReader(open(os.path.join(out, "plan.csv"), encoding="utf-8-sig")))
        self.assertEqual([(p["dissertation_id"], p["column"], p["action"]) for p in plan],
                         [("1", "opponent_1", "replace"), ("1", "opponent_2", "replace")])
        summary = json.load(open(os.path.join(out, "summary.json"), encoding="utf-8"))
        self.assertEqual(summary["opp:mos"], 1)

    def test_apply_requires_yes(self):
        with self.assertRaises(SystemExit):
            bf.main(["apply", "--plan", "x.csv", "--batch", "b1"])


class MigrationTests(unittest.TestCase):
    def test_025_log_table(self):
        sql = (ROOT / "migrations" / "025_dissertation_field_changes.sql").read_text()
        for s in ("CREATE TABLE IF NOT EXISTS dissertation_field_changes", "batch_id         TEXT NOT NULL",
                  "old_value        TEXT", "undone_at        TIMESTAMPTZ",
                  "CHECK (action IN ('replace', 'fill', 'clear'))"):
            self.assertIn(s, sql)
        down = (ROOT / "migrations" / "rollback" / "025_dissertation_field_changes.down.sql").read_text()
        self.assertIn("DROP TABLE IF EXISTS dissertation_field_changes", down)


class LatinOpponentSplitTests(unittest.TestCase):
    """P3: lotin opponent ro'yxatlari (prod 1000000+ qatorlari, 2026-11-12)."""
    CASES = {
        1000192: ("iqtisodiyot fanlari doktori, professor Abduraxmanova Gulnora Kalandarovna, "
                  "iqtisodiyot fanlari doktori, professor Bo’riyev Xakim Toshimovich",
                  ["Abduraxmanova Gulnora Kalandarovna", "Bo’riyev Xakim Toshimovich"]),
        1000190: ("Saidov Shavkat Jumaboyevich, tarix fanlari doktori, professor. Adigezalov Azer "
                  "Nadir ogli, tarix fanlari doktori",
                  ["Saidov Shavkat Jumaboyevich", "Adigezalov Azer Nadir ogli"]),
        1000189: ("Umarxanova Dildora Sharipxanovna yuridik fanlar doktori, professor Amirov Zafar "
                  "Aktamovich yuridik fanlar doktori, professor",
                  ["Umarxanova Dildora Sharipxanovna", "Amirov Zafar Aktamovich"]),
        1000185: ("Yunusova Manzuraxon Pazliddinovna, falsafa fanlari doktori (DSc), dotsent. Tashanov "
                  "Abduxoliq Do‘sanovich, falsafa fanlari doktori (DSc), professor",
                  ["Yunusova Manzuraxon Pazliddinovna", "Tashanov Abduxoliq Do‘sanovich"]),
        0: ("Karimov Bobur Alisher o‘g‘li, texnika fanlari doktori va Rahimova Nodira Akmal qizi, "
            "texnika fanlari nomzodi",
            ["Karimov Bobur Alisher o‘g‘li", "Rahimova Nodira Akmal qizi"]),
    }

    def test_latin_lists_are_split_on_full_names(self):
        for oak_id, (text, names) in self.CASES.items():
            parts = scraper.split_opponents(text)
            self.assertEqual(len(parts), len(names), (oak_id, parts))
            for part, name in zip(parts, names):
                self.assertIn(name, part, oak_id)

    def test_latin_initials_only_text_is_unreliable(self):
        text = "f.-m.f.d. A.A.Tursunov (Opava universiteti), f.-m.f.d. B.B.Axmedov"
        self.assertTrue(scraper.opponents_unreliable(text))
        self.assertEqual(scraper.split_opponents(text), [scraper._clean(text)])

    def test_latin_initials_inside_cyrillic_text_keep_old_behaviour(self):  # 34004, 33874
        self.assertFalse(scraper.opponents_unreliable(
            "Қорахонова Лобар Мусохоновна, T.N.Qori Ниёзий номидаги институт бўлим мудири; "
            "Дилшод Хамидович Турдибоев, педагогика фанлари доктори"))
        self.assertFalse(scraper.opponents_unreliable(
            "Sh.R.Baratov психология фанлари доктори (DSc), профессор; E.A.Asrarxanova, психология "
            "фанлари бўйича фалсафа доктори (PhD), дотсент;"))


SECTIONS = json.loads((ROOT / "tests" / "fixtures" / "oak_api" / "opponent_sections.json")
                      .read_text(encoding="utf-8"))


class OpponentInvariantTests(unittest.TestCase):
    """Real "Расмий оппонентлар" bo'limlari (API keshi, 2026-11-12)."""

    def split(self, oak_id):
        return scraper.split_opponents_checked(SECTIONS[str(oak_id)])

    def surnames(self, parts):
        return [p.split()[0].strip(",.–-") for p in parts]

    # 149-turdagi holatlar: avval opponent tushib qolardi
    def test_short_names_without_patronymic_are_split(self):
        parts, unrel = self.split(21203)          # ", Тўхлиев Боқижон, филология …"
        self.assertFalse(unrel)
        self.assertEqual(self.surnames(parts), ["Отажонов", "Тўхлиев", "Сулаймонов"])
        parts, unrel = self.split(4636)           # ", Ўлжаев Эркин, техника …"
        self.assertEqual((self.surnames(parts), unrel), (["Мамаджонов", "Ўлжаев"], False))
        parts, unrel = self.split(9896)
        self.assertEqual((self.surnames(parts), unrel), (["Искандаров", "Индиаминов"], False))

    def test_dot_between_opponents(self):
        parts, unrel = self.split(29878)          # "… профессор. Раҳимов Рустам, …"
        self.assertEqual((self.surnames(parts), unrel), (["Қиямов", "Раҳимов"], False))
        parts, unrel = self.split(26948)          # "… профессор. Рахмонқулова …"
        self.assertEqual((self.surnames(parts), unrel), (["Ҳасанов", "Рахмонқулова"], False))

    def test_numbered_list(self):
        parts, unrel = self.split(25634)          # "1. Зайнобиддинов … 2. Абдуллаева …"
        self.assertEqual((self.surnames(parts), unrel), (["Зайнобиддинов", "Абдуллаева"], False))

    def test_api_without_a_name_is_unreliable(self):  # 26773
        parts, unrel = self.split(26773)
        self.assertTrue(unrel)
        self.assertEqual(len(parts), 1)
        self.assertTrue(parts[0].startswith("кимё фанлари доктори"))

    def test_missed_third_opponent_is_unreliable(self):  # 26074: "Темирова Чарос Хусанова"
        parts, unrel = self.split(26074)
        self.assertTrue(unrel)
        self.assertIn("Темирова", parts[0])

    def test_unsplittable_mix_is_unreliable(self):     # 15488, 21719, 28632
        for oak_id in (15488, 21719, 28632):
            parts, unrel = self.split(oak_id)
            self.assertTrue(unrel, oak_id)
            self.assertEqual(len(parts), 1, oak_id)

    def test_repeated_degree_word_is_counted_once(self):  # 33696: "доктори доктори"
        parts, unrel = self.split(33696)
        self.assertEqual((self.surnames(parts), unrel), (["Матчонов", "Анарбекова"], False))

    def test_lone_title_piece_is_merged(self):  # 17334: "…; доцент; Арипов …"
        parts, unrel = self.split(17334)
        self.assertEqual((self.surnames(parts), unrel), (["Иштаев", "Арипов"], False))
        self.assertIn("доцент", parts[0])

    def test_degree_first_after_a_dash(self):   # 26916: "– ю.ф.д., профессор Хабибуллаев …"
        parts, unrel = self.split(26916)
        self.assertFalse(unrel)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].endswith("Хабибуллаев Давлатжон Юлчибоевич"))
        self.assertIn("Қучқаров", parts[1])

    def test_every_split_keeps_all_names(self):
        for oak_id, sec in SECTIONS.items():
            parts, unrel = scraper.split_opponents_checked(sec)
            if not unrel:
                self.assertTrue(scraper._name_tokens(sec) <= scraper._name_tokens(" ".join(parts)), oak_id)

    def test_parse_announcement_sets_the_flag(self):
        p = scraper.parse_announcement("Расмий оппонентлар: " + SECTIONS["26773"])
        self.assertTrue(p["opponentlar_ishonchsiz"])
        self.assertEqual(len(p["opponentlar_list"]), 1)


class GuardTests(unittest.TestCase):
    """API natijasiga tayanib bo'lmasa — v3 (≤30600) yoki ehtiyot ro'yxati."""
    V3 = ["Отажонов Неъматилло, филология фанлари доктори", "Тўхлиев Боқижон, филология фанлари доктори"]

    def test_unreliable_api_uses_v3_for_old_ids(self):
        api = {**API, 100: rec([SECTIONS["26773"]], ishonchsiz=True)}
        ch, status, reason = bf.plan_opponents(row(1, 100, X, Y), api[100], bf.Neighbours(api), self.V3)
        self.assertEqual(status, "v3_replace")
        self.assertTrue(reason.startswith("manba=v3"))
        self.assertEqual([(c, n, s) for c, o, n, a, r, s in ch],
                         [("opponent_1", self.V3[0], "v3"), ("opponent_2", self.V3[1], "v3")])

    def test_unreliable_api_and_db_equal_v3_is_untouched(self):
        api = {**API, 100: rec(["бўлинмаган матн"], ishonchsiz=True)}
        ch, status, _ = bf.plan_opponents(row(1, 100, *self.V3), api[100], bf.Neighbours(api), self.V3)
        self.assertEqual((ch, status), ([], "v3_mos"))

    def test_api_missing_an_opponent_present_in_raw_text_uses_v3(self):
        # parser faqat bittasini bergan, xom matnda ikkinchisi bor
        api = {**API, 100: rec([self.V3[0]], opp_raw="; ".join(self.V3))}
        ch, status, reason = bf.plan_opponents(row(1, 100, X, Y), api[100], bf.Neighbours(api), self.V3)
        self.assertEqual(status, "v3_replace")
        self.assertIn("kam", reason)

    def test_new_ids_without_v3_go_to_the_caution_list(self):
        api = {**API, 1000005: rec(["бўлинмаган"], ishonchsiz=True)}
        ch, status, _ = bf.plan_opponents(row(1, 1000005, X, Y), api[1000005], bf.Neighbours(api), self.V3)
        self.assertEqual((ch, status), ([], "ehtiyot"))       # v3 faqat ≤ 30600

    def test_api_without_opponents_but_v3_has_them(self):
        ch, status, reason = bf.plan_opponents(row(1, 102), API[102], bf.Neighbours(API), self.V3)
        self.assertEqual(status, "v3_fill")
        self.assertEqual({s for *_, s in ch}, {"v3"})

    def test_reliable_api_with_fewer_opponents_not_in_raw_is_trusted(self):
        # v3 da ortiqcha (xom matnda yo'q) — API ishonchli
        api = {**API, 100: rec([A], opp_raw=A)}
        ch, status, _ = bf.plan_opponents(row(1, 100, X), api[100], bf.Neighbours(api), [A, B])
        self.assertEqual(status, "replace")

    def test_build_plan_reports_caution_and_v3_source(self):
        api = {**API, 100: rec(["x"], ishonchsiz=True), 1000005: rec(["x"], ishonchsiz=True)}
        rows = [row(1, 100, X, Y), row(2, 1000005, X, Y)]
        plan, _, stats, caution = bf.build_plan(rows, api, {100: self.V3})
        self.assertEqual({p["source"] for p in plan}, {"v3"})
        self.assertEqual([c["oak_id"] for c in caution], [1000005])
        self.assertEqual(stats["manba v3 (qator)"], 1)

    def test_apply_logs_the_plan_source(self):
        p = {"dissertation_id": "7", "oak_id": "100", "column": "opponent_1", "old_value": X,
             "old_is_null": "", "new_value": A, "action": "replace", "reason": "manba=v3", "source": "v3"}
        conn = FakeConn(rules=[("UPDATE dissertations SET opponent_1", lambda q: [(7,)])])
        bf.apply_plan(conn, [p], "b3")
        log = [e[2] for e in conn.log if e[0] == "execute" and "dissertation_field_changes" in e[1]][0]
        self.assertEqual(log[-1], "v3")


if __name__ == "__main__":
    unittest.main()
