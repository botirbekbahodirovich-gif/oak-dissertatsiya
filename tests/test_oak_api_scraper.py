"""oak.uz JSON API manbasi (2026-11-12 dan): scraper.py.

Offline: tests/fixtures/oak_api/ — real /api/elonlar javoblari (2026-11-12).
Har bir DB ulanishi soxta.

Run:
    python3 -m unittest tests.test_oak_api_scraper -v
"""
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
os.environ.setdefault("SESSION_SECRET", "oak-api-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import scraper  # noqa: E402
from services import oak_notify  # noqa: E402
from tests.test_oak_notify_single_path import FakeConn, _fake_modules  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "oak_api"


def fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


LIST8 = fx("elonlar_list8_p1.json")
ITEM_2026 = fx("elon_30574.json")
ITEM_2015 = fx("elon_134.json")


def record(api_item):
    it = scraper.api_list_item(api_item)
    parsed = scraper.parse_announcement(it["raw_text"])
    return it, parsed, scraper._build_record(it, parsed)


class ApiParsingTests(unittest.TestCase):
    def test_fields_become_label_value_lines(self):
        text = scraper.api_item_text(ITEM_2026)
        self.assertIn("Расмий оппонентлар: ", text)
        self.assertEqual(text.count("\n") + 1, len(ITEM_2026["fields"]))

    def test_link_is_the_new_site_announcement_page(self):
        it = scraper.api_list_item(ITEM_2026)          # yakka javobda href yo'q
        self.assertEqual(it["id"], 30574)
        self.assertEqual(it["link"], "https://oak.uz/himoya-elonlari/30574")
        listed = scraper.api_list_item(LIST8["items"][0])   # ro'yxatda href bor
        self.assertEqual(listed["link"], "https://oak.uz" + LIST8["items"][0]["href"])

    def test_2026_record_has_every_column(self):
        _, parsed, rec = record(ITEM_2026)
        for k in ("Olim", "Daraja", "Mavzu", "Ixtisoslik shifrlari", "Royxat raqami",
                  "Ilmiy rahbar", "Bajarilgan muassasa", "IK muassasa",
                  "Yetakchi tashkilot", "Yonalish"):
            self.assertTrue(rec[k], k)
        self.assertEqual(rec["Daraja"], "PhD")
        self.assertTrue(rec["Opponentlar"])
        self.assertFalse(parsed["opponentlar_ishonchsiz"])

    def test_opponents_match_the_label_value(self):
        raw = next(f["value"] for f in ITEM_2026["fields"]
                   if f["label"].startswith("Расмий оппонент"))
        _, parsed, rec = record(ITEM_2026)
        self.assertEqual(rec["Opponentlar"], scraper.split_opponents(raw))

    def test_values_do_not_leak_into_the_next_field(self):
        _, _, rec = record(ITEM_2026)
        yetakchi = next(f["value"] for f in ITEM_2026["fields"]
                        if f["label"].startswith("Етакчи"))
        self.assertEqual(rec["Yetakchi tashkilot"], scraper._clean(yetakchi))
        for opp in rec["Opponentlar"]:
            self.assertNotIn("Етакчи", opp)

    def test_2015_format_maslahatchi_and_no_opponents(self):
        labels = [f["label"] for f in ITEM_2015["fields"]]
        self.assertFalse(any(l.startswith("Расмий оппонент") for l in labels))
        self.assertTrue(any("маслаҳатчи" in l for l in labels))
        _, parsed, rec = record(ITEM_2015)
        self.assertEqual(rec["Opponentlar"], [])
        self.assertTrue(rec["Ilmiy rahbar"])          # "Илмий маслаҳатчи…" → ilmiy_rahbar
        self.assertTrue(rec["Bajarilgan muassasa"])
        self.assertTrue(rec["Olim"])

    def test_every_list_item_parses(self):
        for raw in LIST8["items"]:      # 1000000+ va eski raqamlash aralash
            if not raw.get("fields") or len(raw["fields"]) < 2:
                continue                # lotin yangi format — alohida test
            it, parsed, rec = record(raw)
            self.assertTrue(rec["Olim"], raw["id"])
            self.assertIn(rec["Daraja"], ("PhD", "DSc"), raw["id"])
            values = scraper._item_values(rec)
            self.assertEqual(len(values), scraper._INSERT_SQL.count("%s"))

    def test_person_is_a_fallback_for_olim(self):
        it = scraper.api_list_item({"id": "1", "title": "Эълон", "person": "Каримов Карим",
                                    "fields": []})
        rec = scraper._build_record(it, scraper.parse_announcement(it["raw_text"]))
        self.assertEqual(rec["Olim"], "Каримов Карим")


class StateTests(unittest.TestCase):
    """last_state.json — faqat ma'lumot; aniqlashda ishlatilmaydi."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(scraper, "STATE_FILE", os.path.join(self.tmp, "state.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_old_keys_are_kept_untouched(self):
        with open(scraper.STATE_FILE, "w") as f:
            json.dump({"last_id_page8": 35181, "last_id_page31": 35141, "last_run": "x"}, f)
        self.assertEqual(scraper.load_state()["last_id_page8"], 35181)

    def test_no_file_or_broken_file(self):
        self.assertEqual(scraper.load_state(), {})
        with open(scraper.STATE_FILE, "w") as f:
            f.write("{buzilgan")
        with self.assertLogs("scraper", level="WARNING"):
            self.assertEqual(scraper.load_state(), {})


def _api_page(ids, start_date="12.11.2026", last_page=None):
    """Minimal API elementlari (fields bilan)."""
    page = {"items": [{"id": str(i), "date": start_date, "degree": "PhD",
                       "href": f"/himoya-elonlari/{i}",
                       "title": f"Олим{i} Каримовнинг фалсафа доктори (PhD) диссертацияси",
                       "fields": [{"label": "Диссертация бажарилган муассаса номи",
                                   "value": "Тошкент давлат университети"}]}
                      for i in ids]}
    if last_page is not None:
        page["lastPage"] = last_page
    return page


class MainApiTests(unittest.TestCase):
    """"Yangi" = DB ga haqiqatan qo'shilgan; watermark yo'q."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(scraper, "STATE_FILE", os.path.join(self.tmp, "s.json")),
                  mock.patch.object(scraper, "OUTPUT_FILE", os.path.join(self.tmp, "n.json")),
                  mock.patch.object(scraper, "DELAY", 0),
                  mock.patch.object(scraper, "MIN_PAGES", 5),
                  mock.patch.object(scraper, "STOP_EMPTY_PAGES", 3),
                  mock.patch.object(scraper, "SOURCES", [{"list": 8, "nomi": "Oddiy"}])):
            p.start()
            self.addCleanup(p.stop)
        self.existing = set(range(30000, 35182))   # DB'da bor (eski raqamlash)
        self.next_id = 500

    def _conn(self):
        def insert(params):
            oak_id = int(params[0])
            if oak_id in self.existing:              # ON CONFLICT DO NOTHING
                return []
            self.existing.add(oak_id)
            self.next_id += 1
            return [(self.next_id,)]

        def exists(params):
            return [(1,)] if int(params[0]) in self.existing else []
        return FakeConn(rules=[("INSERT INTO dissertations", insert),
                               ("SELECT 1 FROM dissertations WHERE oak_id", exists)])

    def _run(self, pages, env=None, dry_run=False, conn=True):
        c = self._conn() if conn else None
        calls = []
        modules = _fake_modules(lambda: c) if conn else _fake_modules(
            lambda: (_ for _ in ()).throw(RuntimeError("db yo'q")))
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(scraper, "fetch_list_page",
                                  lambda lst, page: calls.append(page) or pages.get(
                                      page, {"items": []} if page > 1 else None)), \
                mock.patch.object(scraper, "run_notifications") as notify, \
                mock.patch.object(scraper, "mark_silently") as mark, \
                mock.patch.dict(os.environ, env or {}, clear=False):
            if env is None:
                os.environ.pop("OAK_NOTIFY_ENABLED", None)
            items = scraper.main(dry_run=dry_run)
        return items, calls, notify, mark

    @staticmethod
    def _old_pages(start, first_id):
        """start-sahifadan boshlab eski (DB'da bor) e'lonlar sahifalari."""
        return {p: _api_page(range(first_id - (p - start) * 10, first_id - (p - start) * 10 - 10, -1))
                for p in range(start, start + 12)}

    def test_mixed_numbering_new_ids_first_then_old(self):
        # 2026-11-12 holati: 1000000+ yangi raqamlash ro'yxat boshida, keyin eski.
        pages = {1: _api_page([1000008, 1000007, 1000006, 1000005, 1000004, 1000003,
                               1000002, 1000001, 1000000, 35181])}
        pages.update(self._old_pages(2, 35180))
        items, calls, _, _ = self._run(pages)
        self.assertEqual(sorted(i["ID"] for i in items), list(range(1000000, 1000009)))
        self.assertEqual(calls, [1, 2, 3, 4, 5])          # MIN_PAGES=5, 3 bo'sh sahifa
        self.assertEqual(items[0]["Havola"], "https://oak.uz/himoya-elonlari/1000008")
        self.assertEqual(scraper.LAST_RUN_ERRORS, [])

    def test_late_old_numbering_item_between_new_ones_is_found(self):
        # Eski raqamlashda kech chiqqan 35182 yangi ID'lar orasida — watermark
        # bo'lganda ham, bo'lmaganda ham DB tekshiruvi uni topadi.
        pages = {1: _api_page([1000002, 35182, 1000001, 35181, 35180])}
        pages.update(self._old_pages(2, 35179))
        items, _, _, _ = self._run(pages)
        self.assertEqual(sorted(i["ID"] for i in items), [35182, 1000001, 1000002])

    def test_gap_deeper_than_min_pages_is_reached(self):
        # Uzilish: 4 sahifa yangi, keyin eski — ketma-ket 3 bo'sh sahifada to'xtaydi.
        new = list(range(1000039, 999999, -1))
        pages = {p + 1: _api_page(new[p * 10:(p + 1) * 10]) for p in range(4)}
        pages.update(self._old_pages(5, 35181))
        items, calls, _, _ = self._run(pages)
        self.assertEqual(len(items), 40)
        self.assertEqual(calls, [1, 2, 3, 4, 5, 6, 7])

    def test_new_item_resets_the_empty_streak(self):
        pages = self._old_pages(1, 35181)
        pages[3] = _api_page([35160, 35159, 1000050])          # 1 ta yangi 3-sahifada
        items, calls, _, _ = self._run(pages)
        self.assertEqual([i["ID"] for i in items], [1000050])
        self.assertEqual(calls, [1, 2, 3, 4, 5, 6])

    def test_last_page_stops(self):
        items, calls, _, _ = self._run({1: _api_page([1000000], last_page=1)})
        self.assertEqual(calls, [1])
        self.assertEqual(len(items), 1)
        self.assertEqual(scraper.LAST_RUN_STATS["8"]["toxtash"], "ro'yxat oxiri (lastPage)")

    def test_page_cap_while_still_finding_new_is_an_error(self):
        new = list(range(1000029, 999999, -1))
        pages = {p + 1: _api_page(new[p * 10:(p + 1) * 10]) for p in range(3)}
        with mock.patch.object(scraper, "MAX_PAGES", 2):
            items, calls, _, _ = self._run(pages)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(items), 20)
        self.assertTrue(any("--max-pages" in e for e in scraper.LAST_RUN_ERRORS))

    def test_failed_later_page_is_an_error(self):
        pages = {1: _api_page([1000000]), 2: None}
        items, _, _, _ = self._run(pages)
        self.assertEqual(len(items), 1)
        self.assertTrue(scraper.LAST_RUN_ERRORS)

    def test_no_db_is_an_error_and_fetches_nothing(self):
        items, calls, _, _ = self._run({1: _api_page([1000000])}, conn=False)
        self.assertEqual((items, calls), ([], []))
        self.assertTrue(any("DB ulanmagan" in e for e in scraper.LAST_RUN_ERRORS))

    def test_dry_run_uses_exists_check_and_writes_nothing(self):
        pages = {1: _api_page([1000001, 1000000, 35181])}
        pages.update(self._old_pages(2, 35180))
        items, _, notify, mark = self._run(pages, dry_run=True)
        self.assertEqual(sorted(i["ID"] for i in items), [1000000, 1000001])
        self.assertFalse(os.path.exists(scraper.STATE_FILE))
        notify.assert_not_called()
        mark.assert_not_called()

    def test_state_file_is_informational(self):
        pages = {1: _api_page([1000000], last_page=1)}
        self._run(pages)
        with open(scraper.STATE_FILE) as f:
            s = json.load(f)
        self.assertEqual(s["last_run_lists"]["8"]["yangi"], 1)
        self.assertNotIn("last_id_list8", s)

    def test_max_pages_cli_flag(self):
        with mock.patch.object(scraper, "main", lambda dry_run=False: []), \
                mock.patch.object(scraper, "LAST_RUN_ERRORS", []), \
                mock.patch.object(scraper, "MAX_PAGES", 60):
            scraper.cli(["--max-pages", "200", "--dry-run"])
            self.assertEqual(scraper.MAX_PAGES, 200)

    def test_empty_api_reports_error(self):
        items, calls, _, _ = self._run({})
        self.assertEqual((items, calls), ([], [1]))
        self.assertTrue(any("API bo'sh" in e for e in scraper.LAST_RUN_ERRORS))
        with open(scraper.STATE_FILE) as f:
            self.assertTrue(json.load(f)["last_run_errors"])

    def test_cli_exits_nonzero_on_api_failure(self):
        with mock.patch.object(scraper, "main", lambda dry_run=False: []), \
                mock.patch.object(scraper, "LAST_RUN_ERRORS", ["x"]):
            with self.assertRaises(SystemExit) as cm:
                scraper.cli([])
        self.assertEqual(cm.exception.code, 1)

    def test_notify_disabled_by_default_marks_silently(self):
        items, _, notify, mark = self._run({1: _api_page([1000000], last_page=1)})
        notify.assert_not_called()
        mark.assert_called_once_with([501])

    def test_notify_enabled_sends(self):
        items, _, notify, mark = self._run({1: _api_page([1000000], last_page=1)},
                                           env={"OAK_NOTIFY_ENABLED": "1"})
        notify.assert_called_once_with(ids=[501])
        mark.assert_not_called()


class FieldFixTests(unittest.TestCase):
    """34884: "ИК рақами" alohida maydon; yangi/lotin format; bo'sh label."""

    def _rec(self, fields, sections=None, title="Каримов Каримнинг фалсафа доктори (PhD) "
                                               "диссертацияси ҳимояси ҳақида эълон"):
        return record({"id": "1", "title": title, "fields": fields, "sections": sections or []})[2]

    def test_ik_raqami_field_does_not_leak_into_ik_muassasa(self):  # 34884
        rec = self._rec([
            {"label": "ИК фаолият кўрсатаётган муассаса номи", "value": "Қарши давлат университети."},
            {"label": "ИК рақами", "value": "Қарши давлат университети, DSc.03/2025.27.12.Fill.11.05."},
            {"label": "Етакчи ташкилот", "value": "Термиз давлат университети."}])
        self.assertEqual(rec["IK muassasa"], "Қарши давлат университети")
        self.assertEqual(rec["IK raqami"], "DSc.03/2025.27.12.Fill.11.05")
        self.assertEqual(rec["Yetakchi tashkilot"], "Термиз давлат университети")

    def test_combined_ik_label_still_works(self):
        rec = self._rec([{"label": "ИК фаолият кўрсатаётган муассаса номи, ИК рақами",
                          "value": "Андижон давлат университети, DSc.03/2025.27.12.Fil.07.03"}])
        self.assertEqual(rec["IK muassasa"],
                         "Андижон давлат университети, DSc.03/2025.27.12.Fil.07.03")
        self.assertEqual(rec["IK raqami"], "DSc.03/2025.27.12.Fil.07.03")

    def test_new_label_variants(self):
        rec = self._rec([
            {"label": "Илмий рахбар", "value": "Каримов Карим, тиббиёт фанлари доктори"},
            {"label": "Илмий тадқиқот иши бажарилган муассаса номи", "value": "Тошкент тиббиёт академияси"},
            {"label": "Илмий кенгаш фаолият кўрсатаётган муассаса номи ва рақами",
             "value": "Тошкент давлат тиббиёт университети, DSc.06/2025.27.12.Tib.01.02"},
            {"label": "Диссертациянинг йўналиши", "value": "амалий аҳамиятга молик"}])
        self.assertTrue(rec["Ilmiy rahbar"].startswith("Каримов Карим"))
        self.assertEqual(rec["Bajarilgan muassasa"], "Тошкент тиббиёт академияси")
        self.assertEqual(rec["IK raqami"], "DSc.06/2025.27.12.Tib.01.02")
        self.assertEqual(rec["Yonalish"], "амалий аҳамиятга молик")

    def test_empty_label_continues_previous_value(self):
        rec = self._rec([{"label": "Расмий оппонентлар", "value": "Каримов Карим Каримович, т.ф.д.;"},
                         {"label": "", "value": "Алиев Али Алиевич, т.ф.д."}])
        self.assertEqual(len(rec["Opponentlar"]), 2)

    def test_real_new_numbering_item_1000000(self):
        rec = record(fx("elon_1000000.json"))[2]
        self.assertEqual(rec["Daraja"], "DSc")
        self.assertEqual(len(rec["Opponentlar"]), 3)
        self.assertTrue(rec["Yonalish"])
        self.assertTrue(rec["IK raqami"].startswith("DSc.06/2025"))

    def test_real_latin_item_1000001_general_section(self):
        it, parsed, rec = record(fx("elon_1000001.json"))
        self.assertEqual(rec["Daraja"], "PhD")
        for k in ("Royxat raqami", "Ilmiy rahbar", "Bajarilgan muassasa", "IK muassasa",
                  "Yetakchi tashkilot"):
            self.assertTrue(rec[k], k)
        self.assertTrue(rec["Opponentlar"])
        # mavzu fields'dagi (kirill) birinchi qiymatdan olinadi
        mavzu_field = fx("elon_1000001.json")["fields"][0]["value"]
        self.assertTrue(rec["Mavzu"])
        self.assertIn(rec["Mavzu"][:20], mavzu_field)


class NotifySwitchTests(unittest.TestCase):
    def test_flag_values(self):
        for v, want in (("1", True), ("true", True), ("ON", True), ("0", False),
                        ("", False), ("no", False)):
            with mock.patch.dict(os.environ, {"OAK_NOTIFY_ENABLED": v}):
                self.assertEqual(scraper.notify_enabled(), want, v)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(scraper.notify_enabled())

    def test_mark_without_notify_sql(self):
        conn = FakeConn(rules=[("UPDATE dissertations SET notified_at", [(1,), (2,)])])
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)):
            n = oak_notify.mark_without_notify([2, 1, 1, None])
        self.assertEqual(n, 2)
        q, params = conn.log[0][1], conn.log[0][2]
        self.assertIn("SET notified_at = NOW() WHERE id = ANY(%s) AND notified_at IS NULL", q)
        self.assertNotIn("RETURNING", q)
        self.assertEqual(params, ([1, 2],))
        self.assertIn(("commit",), conn.log)

    def test_mark_without_notify_no_ids(self):
        with mock.patch.dict(sys.modules, _fake_modules(lambda: self.fail("no conn"))):
            self.assertEqual(oak_notify.mark_without_notify([]), 0)


if __name__ == "__main__":
    unittest.main()
