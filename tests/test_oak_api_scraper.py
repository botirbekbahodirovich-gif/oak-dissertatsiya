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

    def test_link_keeps_the_db_format(self):
        it = scraper.api_list_item(ITEM_2026)
        self.assertEqual(it["id"], 30574)
        self.assertEqual(it["link"], "https://oak.uz/pages/30574")

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
        for raw in LIST8["items"]:
            if int(raw["id"]) >= scraper.ID_CEILING:
                continue
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
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(scraper, "STATE_FILE", os.path.join(self.tmp, "state.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_old_page_keys_migrate_to_list_keys(self):
        with open(scraper.STATE_FILE, "w") as f:
            json.dump({"last_id_page8": 35181, "last_id_page31": 35141, "last_run": "x"}, f)
        s = scraper.load_state()
        self.assertEqual((s["last_id_list8"], s["last_id_list31"]), (35181, 35141))

    def test_oldest_single_key_format(self):
        with open(scraper.STATE_FILE, "w") as f:
            json.dump({"last_id": 100}, f)
        self.assertEqual(scraper.load_state()["last_id_list8"], 100)

    def test_no_file(self):
        s = scraper.load_state()
        self.assertEqual((s["last_id_list8"], s["last_id_list31"]), (0, 0))


def _api_page(ids, start_date="12.11.2026"):
    """Minimal API elementlari (fields bilan)."""
    return {"items": [{"id": str(i), "date": start_date, "degree": "PhD",
                       "title": f"Олим{i} Каримовнинг фалсафа доктори (PhD) диссертацияси",
                       "fields": [{"label": "Диссертация бажарилган муассаса номи",
                                   "value": "Тошкент давлат университети"}]}
                      for i in ids]}


class MainApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(scraper, "STATE_FILE", os.path.join(self.tmp, "s.json")),
                  mock.patch.object(scraper, "OUTPUT_FILE", os.path.join(self.tmp, "n.json")),
                  mock.patch.object(scraper, "DELAY", 0),
                  mock.patch.object(scraper, "SOURCES", [{"list": 8, "nomi": "Oddiy"}])):
            p.start()
            self.addCleanup(p.stop)
        self.inserted = {}
        self.next_id = 500

    def _conn(self):
        def insert(params):
            if int(params[0]) <= 35181:      # bazada bor — ON CONFLICT DO NOTHING
                return []
            self.next_id += 1
            self.inserted[params[0]] = self.next_id
            return [(self.next_id,)]
        return FakeConn(rules=[("INSERT INTO dissertations", insert)])

    def _run(self, pages, state=None, env=None, dry_run=False):
        if state is not None:
            with open(scraper.STATE_FILE, "w") as f:
                json.dump(state, f)
        conn = self._conn()
        calls = []
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)), \
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

    def test_ceiling_ids_are_skipped_and_do_not_move_watermark(self):
        pages = {1: _api_page([1000001, 1000000, 35183, 35182])}
        items, _, _, _ = self._run(pages, state={"last_id_page8": 35181})
        self.assertEqual(sorted(i["ID"] for i in items), [35182, 35183])
        with open(scraper.STATE_FILE) as f:
            self.assertEqual(json.load(f)["last_id_list8"], 35183)

    def test_catches_up_missed_days_across_pages(self):
        # 25 ta yangi (3 sahifa) + eski — smart-stop eski 4 tadan keyin.
        new = list(range(35206, 35181, -1))
        pages = {1: _api_page(new[:10]), 2: _api_page(new[10:20]),
                 3: _api_page(new[20:] + [35181, 35180, 35179, 35178, 35177]),
                 4: _api_page([35176])}
        items, calls, _, _ = self._run(pages, state={"last_id_list8": 35181})
        self.assertEqual(len(items), 25)
        self.assertEqual(calls, [1, 2, 3])

    def test_page_cap_before_watermark_does_not_move_it(self):
        # 2 sahifa chegarasi, watermark'gacha yetilmaydi → oradagilar yo'qolmasin.
        pages = {1: _api_page(range(35230, 35220, -1)), 2: _api_page(range(35220, 35210, -1)),
                 3: _api_page(range(35210, 35200, -1))}
        with mock.patch.object(scraper, "MAX_PAGES", 2):
            items, calls, _, _ = self._run(pages, state={"last_id_list8": 35181})
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(items), 20)          # o'qilganlari baribir yoziladi
        with open(scraper.STATE_FILE) as f:
            self.assertEqual(json.load(f)["last_id_list8"], 35181)
        self.assertTrue(any("yetilmadi" in e for e in scraper.LAST_RUN_ERRORS))

    def test_failed_later_page_does_not_move_watermark(self):
        pages = {1: _api_page(range(35195, 35185, -1)), 2: None}
        items, calls, _, _ = self._run(pages, state={"last_id_list8": 35181})
        with open(scraper.STATE_FILE) as f:
            self.assertEqual(json.load(f)["last_id_list8"], 35181)

    def test_last_page_counts_as_reaching_old(self):
        page = _api_page([35183, 35182]); page["lastPage"] = 1
        items, calls, _, _ = self._run({1: page}, state={"last_id_list8": 35181})
        self.assertEqual(calls, [1])
        with open(scraper.STATE_FILE) as f:
            self.assertEqual(json.load(f)["last_id_list8"], 35183)

    def test_end_of_list_counts_as_reaching_old(self):
        pages = {1: _api_page([35183, 35182]), 2: {"items": []}}
        items, calls, _, _ = self._run(pages, state={"last_id_list8": 35181})
        with open(scraper.STATE_FILE) as f:
            self.assertEqual(json.load(f)["last_id_list8"], 35183)
        self.assertEqual(scraper.LAST_RUN_ERRORS, [])

    def test_max_pages_cli_flag(self):
        with mock.patch.object(scraper, "main", lambda dry_run=False: []), \
                mock.patch.object(scraper, "LAST_RUN_ERRORS", []), \
                mock.patch.object(scraper, "MAX_PAGES", 60):
            scraper.cli(["--max-pages", "200", "--dry-run"])
            self.assertEqual(scraper.MAX_PAGES, 200)

    def test_empty_api_keeps_watermark_and_reports_error(self):
        items, _, _, _ = self._run({}, state={"last_id_list8": 35181})
        self.assertEqual(items, [])
        with open(scraper.STATE_FILE) as f:
            self.assertEqual(json.load(f)["last_id_list8"], 35181)
        self.assertTrue(scraper.LAST_RUN_ERRORS)

    def test_cli_exits_nonzero_on_api_failure(self):
        with mock.patch.object(scraper, "main", lambda dry_run=False: []), \
                mock.patch.object(scraper, "LAST_RUN_ERRORS", ["x"]):
            with self.assertRaises(SystemExit) as cm:
                scraper.cli([])
        self.assertEqual(cm.exception.code, 1)

    def test_notify_disabled_by_default_marks_silently(self):
        items, _, notify, mark = self._run({1: _api_page([35182])},
                                           state={"last_id_list8": 35181})
        notify.assert_not_called()
        mark.assert_called_once_with([501])

    def test_notify_enabled_sends(self):
        items, _, notify, mark = self._run({1: _api_page([35182])},
                                           state={"last_id_list8": 35181},
                                           env={"OAK_NOTIFY_ENABLED": "1"})
        notify.assert_called_once_with(ids=[501])
        mark.assert_not_called()

    def test_dry_run_disabled_neither_sends_nor_marks(self):
        items, _, notify, mark = self._run({1: _api_page([35182])},
                                           state={"last_id_list8": 35181}, dry_run=True)
        self.assertEqual(len(items), 1)
        notify.assert_not_called()
        mark.assert_not_called()


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
