"""OAK import → notification single path (scraper.py + services/oak_notify.py).

Offline: parsing runs against real oak.uz list-page blocks saved in
tests/fixtures/oak_list_real_2026-11-09.html; every DB connection is a fake.

Run:
    python3 -m unittest tests.test_oak_notify_single_path -v
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "oak-notify-test-secret")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

import scraper  # noqa: E402
from services import oak_notify  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixtures" / "oak_list_real_2026-11-09.html").read_text(
    encoding="utf-8")


def _parsed_by_id():
    out = {}
    for item in scraper.parse_list_page(FIXTURE):
        parsed = scraper.parse_announcement(item["raw_text"])
        out[item["id"]] = (item, parsed, scraper._build_record(item, parsed))
    return out


PARSED = _parsed_by_id()


class FakeCursor:
    """Records every execute; answers fetches from (substring, rows) rules."""

    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.conn.log.append(("execute", q, params))
        if self.conn.fail_on and self.conn.fail_on in q:
            raise RuntimeError("boom")
        self._rows = []
        for needle, rows in self.conn.rules:
            if needle in q:
                self._rows = list(rows(params) if callable(rows) else rows)
                break

    @property
    def rowcount(self):
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, rules=(), fail_on=None, log=None):
        self.rules = list(rules)
        self.fail_on = fail_on
        self.log = log if log is not None else []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.log.append(("close",))

    def sql(self):
        return [e[1] for e in self.log if e[0] == "execute"]


# ── parsing (real OAK pages) ────────────────────────────────────────────────
class OpponentParsingTests(unittest.TestCase):
    def opps(self, oak_id):
        return PARSED[oak_id][1]["opponentlar_list"]

    def test_fixture_has_all_sample_announcements(self):
        self.assertEqual(sorted(PARSED),
                         [34719, 34720, 34796, 34797, 34798, 34838, 34845])

    def test_semicolon_separator_is_the_common_case(self):
        opps = self.opps(34845)
        self.assertEqual(len(opps), 2)
        self.assertTrue(opps[0].startswith("Ҳайитов Ойбек Эшбоевич"))
        self.assertTrue(opps[1].startswith("Тангриев Алишер Жуманазарович"))
        self.assertTrue(opps[1].endswith("дотсент"))

    def test_value_is_not_cut_at_ilmiy_inside_the_text(self):
        # old extract_field stopped at "илмий" → "…, катта"
        opps = self.opps(34797)
        self.assertEqual(len(opps), 3)
        self.assertTrue(opps[2].endswith("катта илмий ҳодим"), opps[2])

    def test_one_opponent_per_line_with_dash(self):
        opps = self.opps(34798)
        self.assertEqual(len(opps), 2)
        self.assertTrue(opps[0].startswith("Солиев Баходир Камилович –"))
        self.assertTrue(opps[1].startswith("Мардиев Уткир Бердиевич –"))
        self.assertIn("(04.00.04)", opps[1])

    def test_opponents_without_semicolon_split_on_full_name(self):
        opps = self.opps(34720)
        self.assertEqual(len(opps), 2)
        self.assertTrue(opps[0].startswith("Рахматов Учқун Эргашевич"))
        self.assertTrue(opps[1].startswith("Уббиниязова Жамила Комекбаевна"))

    def test_comma_separated_opponents(self):
        opps = self.opps(34719)
        self.assertEqual(len(opps), 2)
        self.assertTrue(opps[0].endswith("катта илмий ходим"), opps[0])
        self.assertTrue(opps[1].startswith("Арепбаев Исламбек Муратбаевич"))

    def test_degree_first_entries_stay_whole(self):
        opps = self.opps(34838)
        self.assertEqual(len(opps), 2)
        self.assertTrue(opps[0].endswith("Йўлдошева Холида Қўзиевна"))

    def test_header_with_colon_on_next_line(self):
        self.assertEqual(len(self.opps(34796)), 2)
        self.assertEqual(PARSED[34796][1]["yetakchi_tashkilot"],
                         "Қарши давлат техника университети")

    def test_split_opponents_limits_to_three_columns(self):
        self.assertEqual(scraper.split_opponents("А; Б; В; Г"), ["А", "Б", "В"])
        self.assertEqual(scraper.split_opponents(""), [])


class FieldParsingTests(unittest.TestCase):
    def test_codes_come_from_the_mavzu_section_only(self):
        # 34798's opponents carry their own codes (06.01.02, 04.00.04)
        self.assertEqual(PARSED[34798][2]["Ixtisoslik shifrlari"], "11.00.05")

    def test_codes_are_normalized_sorted_and_comma_joined(self):
        self.assertEqual(scraper.normalize_codes("13.00.04 – …, 05.01.01; 13.00.04"),
                         "05.01.01, 13.00.04")

    def test_yonalish_is_the_announcement_field_not_the_source(self):
        for oak_id in (34845, 34798, 34796):
            self.assertEqual(PARSED[oak_id][2]["Yonalish"],
                             "назарий ва амалий аҳамиятга молик")

    def test_mavzu_is_the_title_only(self):
        rec = PARSED[34845][2]
        self.assertTrue(rec["Mavzu"].startswith("Юқори малакали юнон-рим"))
        self.assertTrue(rec["Mavzu"].endswith("такомиллаштириш"))
        self.assertTrue(PARSED[34720][2]["Mavzu"].startswith("Биология фанини"))
        self.assertTrue(PARSED[34798][2]["Mavzu"].endswith("(Гулистон шаҳри мисолида)"))

    def test_muassasa_is_not_cut_at_tadqiqot(self):
        self.assertTrue(PARSED[34845][2]["Bajarilgan muassasa"].endswith(
            "илмий тадқиқотлар институти"))

    def test_rahbar_header_with_long_label(self):
        self.assertTrue(PARSED[34798][2]["Ilmiy rahbar"].startswith(
            "Абдуллаев Ботиржон Дадажонович"))
        self.assertTrue(PARSED[34797][2]["Ilmiy rahbar"].startswith("Махкамов"))

    def test_item_values_match_insert_columns(self):
        rec = PARSED[34797][2]
        values = scraper._item_values(rec, institution_id=7)
        self.assertEqual(len(values), scraper._INSERT_SQL.count("%s"))
        self.assertEqual(values[0], "34797")
        self.assertEqual(values[10], 7)
        self.assertEqual(values[13:16], tuple(rec["Opponentlar"]))
        self.assertEqual(values[-1], "назарий ва амалий аҳамиятга молик")

    def test_insert_returns_id_and_never_overwrites(self):
        sql = " ".join(scraper._INSERT_SQL.split())
        self.assertIn("ON CONFLICT (oak_id) DO NOTHING RETURNING id", sql)
        self.assertNotIn("DO UPDATE", sql)


class DegreeTests(unittest.TestCase):
    def test_phd_announcement_with_dsc_opponents_is_phd(self):
        # 34845: PhD defence, both opponents are "(DSc)"
        self.assertIn("(DSc)", PARSED[34845][1]["opponentlar"])
        self.assertEqual(PARSED[34845][2]["Daraja"], "PhD")

    def test_dsc_title_is_dsc(self):
        self.assertEqual(PARSED[34797][2]["Daraja"], "DSc")

    def test_every_fixture_record_matches_its_title(self):
        for oak_id, (item, parsed, rec) in PARSED.items():
            want = "DSc" if "(DSc)" in item["title"] else "PhD"
            self.assertEqual(rec["Daraja"], want, oak_id)

    def test_body_detection_ignores_rahbar_ik_and_opponents(self):
        # IK code "DSc.03/…" and opponents' "(DSc)" must not decide the degree
        self.assertEqual(PARSED[34798][1]["daraja"], "PhD")

    def test_degree_from_title(self):
        f = scraper.degree_from_title
        self.assertEqual(f("Каримов Каримнинг фалсафа доктори (PhD) диссертацияси"), "PhD")
        self.assertEqual(f("Реджепов Шовкатнинг Фан доктори (DSc) диссертацияси"), "DSc")
        self.assertEqual(f("Karimov Karimning falsafa doktori (PhD) dissertatsiyasi"), "PhD")
        self.assertEqual(f("Эълон"), "")


class AuthorFromTitleTests(unittest.TestCase):
    """Real oak.uz titles (2026-11); 18 of 200 had no "-нинг" suffix."""
    CASES = [
        ("Олимова Хуршида Вайдиллаевна фалсафа доктори (PhD) диссертацияси ҳимояси "
         "ҳақида эълон", "Олимова Хуршида Вайдиллаевна"),
        ("Содиқова Умидахон Бахтиёр қизи Педагогика фанлари бўйича фалсафа доктори "
         "(PhD) диссертацияси ҳимояси ҳақида эълон", "Содиқова Умидахон Бахтиёр қизи"),
        ("Мамутов Қуwанишбай Толибаевич фалсафа доктори (PhD) диссертацияси ҳимояси "
         "ҳақида эълон", "Мамутов Қуwанишбай Толибаевич"),
        ("Махкамов Кодиржон Одилжонович фан доктори (DSc) диссертацияси ҳимояси "
         "ҳақида эълон", "Махкамов Кодиржон Одилжонович"),
        ("Дедабоев Жобир Исмоил ўғли  фалсафа доктори (PhD) диссертацияси ҳимояси "
         "ҳақида эълон", "Дедабоев Жобир Исмоил ўғли"),
        # "-нинг" + capitalised "Фан" must keep working
        ("Реджепов Шовкат Бахтияровичнинг Фан доктори (DSc) диссертацияси ҳимояси "
         "ҳақида эълон", "Реджепов Шовкат Бахтиярович"),
        ("Темирова Хосият Фарход қизининг Фан доктори (DSc) диссертацияси ҳимояси "
         "ҳақида эълон", "Темирова Хосият Фарход қизи"),
        ("Тахиржанов Нурсултан Кайрат ўғлининг фалсафа доктори (PhD) диссертацияси "
         "ҳимояси ҳақида эълон", "Тахиржанов Нурсултан Кайрат ўғли"),
    ]

    def test_real_titles(self):
        for title, want in self.CASES:
            self.assertEqual(scraper.extract_author_from_title(title), want, title)

    def test_fixture_has_no_empty_olim(self):
        for oak_id, (item, parsed, rec) in PARSED.items():
            self.assertTrue(rec["Olim"], oak_id)


# ── DB writes ───────────────────────────────────────────────────────────────
def _fake_modules(conn_factory):
    data = types.ModuleType("data")
    data.get_connection = conn_factory
    ident = types.ModuleType("institution_identity")
    ident.resolve_institution = lambda cur, name, source=None: 5
    return {"data": data, "institution_identity": ident}


class DbSaveTests(unittest.TestCase):
    def setUp(self):
        self.rec = PARSED[34845][2]

    def test_added_returns_new_id(self):
        conn = FakeConn(rules=[("INSERT INTO dissertations", [(42,)])])
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)):
            self.assertEqual(scraper.db_save_one(conn, self.rec), ("added", 42))
        self.assertIn(("commit",), conn.log)

    def test_existing_row_is_skipped(self):
        conn = FakeConn()
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)):
            self.assertEqual(scraper.db_save_one(conn, self.rec), ("skip", None))

    def test_error_rolls_back(self):
        conn = FakeConn(fail_on="INSERT INTO dissertations")
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)):
            self.assertEqual(scraper.db_save_one(conn, self.rec), ("error", None))
        self.assertIn(("rollback",), conn.log)


class ScraperMainTests(unittest.TestCase):
    """main(): only rows the DB really inserted reach the notifier."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patches = [
            mock.patch.object(scraper, "STATE_FILE", os.path.join(self.tmp, "state.json")),
            mock.patch.object(scraper, "OUTPUT_FILE", os.path.join(self.tmp, "new.json")),
            mock.patch.object(scraper, "DELAY", 0),
            mock.patch.object(scraper, "SOURCES",
                              [{"url": "https://oak.uz/page/8", "nomi": "Oddiy"}]),
            mock.patch.object(scraper, "fetch",
                              lambda url: FIXTURE if "page=" not in url else None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.inserted = {"34845": 101, "34797": 102}   # others already exist

    def _conn(self):
        def insert_rows(params):
            new_id = self.inserted.get(params[0])
            return [(new_id,)] if new_id else []
        return FakeConn(rules=[
            ("INSERT INTO dissertations", insert_rows),
            ("SELECT 1 FROM dissertations WHERE oak_id",
             lambda p: [] if p[0] in self.inserted else [(1,)]),
        ])

    def test_notifies_only_inserted_ids(self):
        conn = self._conn()
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)), \
                mock.patch.object(scraper, "run_notifications") as notify:
            items = scraper.main()
        self.assertEqual(sorted(i["ID"] for i in items), [34797, 34845])
        notify.assert_called_once_with(ids=[101, 102])
        self.assertTrue(os.path.exists(scraper.STATE_FILE))

    def test_dry_run_writes_nothing(self):
        conn = self._conn()
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)), \
                mock.patch.object(scraper, "run_notifications") as notify:
            items = scraper.main(dry_run=True)
        self.assertEqual(len(items), 2)
        self.assertFalse(any("INSERT" in q for q in conn.sql()))
        self.assertFalse(os.path.exists(scraper.STATE_FILE))
        self.assertFalse(os.path.exists(scraper.OUTPUT_FILE))
        records = notify.call_args.kwargs["records"]
        self.assertEqual({r["ixtisoslik"] for r in records}, {"13.00.04", "05.01.04"})
        self.assertTrue(all(r["id"] is None for r in records))

    def test_notification_failure_does_not_stop_the_scraper(self):
        conn = self._conn()
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)), \
                mock.patch.object(oak_notify, "notify_new_dissertations",
                                  side_effect=RuntimeError("telegram down")), \
                self.assertLogs("scraper", level="ERROR") as logs:
            items = scraper.main()
        self.assertEqual(len(items), 2)
        self.assertIn("Bildirishnomalar yuborilmadi", logs.output[0])
        with open(scraper.STATE_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["last_id_page8"], 34845)


# ── services/oak_notify ─────────────────────────────────────────────────────
ROW = (101, "Тастанов Нуржан", "Мавзу", "13.00.04", "Керимов Фикрат Азизович",
       "https://oak.uz/pages/34845")


class OakNotifyTests(unittest.TestCase):
    def _run(self, rows, dry_run=False, dispatchers=None):
        log = []
        conn = FakeConn(rules=[("UPDATE dissertations SET notified_at", rows),
                               ("FROM dissertations WHERE id = ANY", rows)], log=log)
        calls = []

        def make(name, exc=None):
            def fn(records, dry_run=False, report=None):
                log.append(("dispatch", name))
                calls.append((name, records, dry_run))
                if exc:
                    raise exc
                report.append({"kind": name, "channel": "site", "user_id": 1,
                               "diss_id": records[0]["id"], "text": "x"})
                return 1
            return fn
        dispatchers = dispatchers or [("himoya", make("himoya")),
                                      ("obuna", make("obuna")),
                                      ("kuzatuv", make("kuzatuv"))]
        dispatchers = [(n, f if callable(f) else make(n, f)) for n, f in dispatchers]
        with mock.patch.dict(sys.modules, _fake_modules(lambda: conn)), \
                mock.patch.object(oak_notify, "_dispatchers", lambda: dispatchers):
            result = oak_notify.notify_new_dissertations([101, 101, None], dry_run=dry_run)
        return result, log, calls

    def test_claim_is_committed_before_any_send(self):
        result, log, calls = self._run([ROW])
        kinds = [e[0] if e[0] != "execute" else "claim" for e in log]
        self.assertEqual(kinds[:3], ["claim", "commit", "close"])
        self.assertEqual(kinds[3:], ["dispatch"] * 3)
        claim = log[0]
        self.assertIn("SET notified_at = NOW() WHERE id = ANY(%s) AND notified_at IS NULL",
                      claim[1])
        self.assertEqual(claim[2], ([101],))
        self.assertEqual(result["counts"], {"himoya": 1, "obuna": 1, "kuzatuv": 1})
        self.assertEqual(calls[0][1][0]["ixtisoslik"], "13.00.04")

    def test_already_claimed_rows_send_nothing(self):
        result, log, calls = self._run([])
        self.assertEqual(result["claimed"], 0)
        self.assertEqual(calls, [])

    def test_dry_run_peeks_and_rolls_back(self):
        result, log, calls = self._run([ROW], dry_run=True)
        self.assertNotIn("UPDATE", log[0][1])
        self.assertIn(("rollback",), log)
        self.assertNotIn(("commit",), log)
        self.assertTrue(all(c[2] for c in calls))
        self.assertIn("[DRY-RUN]", oak_notify.format_result(result))

    def test_one_channel_failing_does_not_stop_others(self):
        with self.assertLogs("services.oak_notify", level="ERROR"):
            result, log, calls = self._run(
                [ROW], dispatchers=[("himoya", RuntimeError("x")), ("obuna", None),
                                    ("kuzatuv", None)])
        self.assertEqual(result["errors"], ["himoya"])
        self.assertEqual([c[0] for c in calls], ["himoya", "obuna", "kuzatuv"])

    def test_no_ids_no_connection(self):
        with mock.patch.dict(sys.modules, _fake_modules(
                lambda: self.fail("no connection expected"))):
            self.assertEqual(oak_notify.notify_new_dissertations([])["claimed"], 0)

    def test_import_failure_is_logged(self):
        def broken():
            raise ImportError("no app")
        with mock.patch.object(oak_notify, "_dispatchers", broken), \
                self.assertLogs("services.oak_notify", level="ERROR"):
            result = oak_notify.preview_records([dict(zip(oak_notify._COLUMNS, ROW))])
        self.assertEqual(result["errors"], ["import"])


class MigrationTests(unittest.TestCase):
    def test_migration_marks_every_existing_row(self):
        sql = (ROOT / "migrations" / "022_dissertations_notified_at.sql").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;", sql)
        self.assertIn("UPDATE dissertations SET notified_at = NOW() WHERE notified_at IS NULL;",
                      sql)
        self.assertNotIn("DEFAULT", sql.split("ADD COLUMN", 1)[1].split(";")[0])


# ── the three dispatchers (dry_run + multi-code matching) ───────────────────
import app  # noqa: E402,F401  — blueprints import `from app import csrf`
import data  # noqa: E402
from blueprints import notifications, olimlar_catalog, reminders, subscriptions  # noqa: E402

REC = {"id": 101, "olim": "Тастанов Нуржан", "mavzu": "Мавзу",
       "ixtisoslik": "05.01.01, 13.00.04", "ilmiy_rahbar": "Керимов Фикрат Азизович",
       "link": "https://oak.uz/pages/34845"}


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        for target in (mock.patch.object(reminders, "_ensure_schema", lambda cur: None),
                       mock.patch.object(notifications, "_ensure_schema", lambda cur: None),
                       mock.patch.object(subscriptions, "_ensure_schema", lambda cur: None),
                       mock.patch.object(olimlar_catalog, "_ensure_schema", lambda cur: None)):
            target.start()
            self.addCleanup(target.stop)
        self.tg = mock.patch.object(reminders, "_send_telegram", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def _himoya(self, dry_run):
        conn = FakeConn(rules=[("SELECT LOWER(email), id FROM users", [("a@x.uz", 7)]),
                               ("SELECT COUNT(*) FROM user_alerts", [(0,)])])
        audience = [{"email": "a@x.uz", "telegram_id": 555, "ixtisoslik": "13.00.04",
                     "prefs": {}}]
        report = []
        with mock.patch.object(data, "get_connection", lambda: conn), \
                mock.patch.object(reminders, "_fetch_audience", lambda cur: audience):
            sent = reminders.notify_himoya_matches([REC], dry_run=dry_run, report=report)
        return sent, report, conn

    def test_himoya_matches_one_of_several_codes(self):
        sent, report, conn = self._himoya(dry_run=False)
        self.assertEqual(sent, 2)
        self.assertTrue(any("INSERT INTO user_alerts" in q for q in conn.sql()))
        self.tg.assert_called_once()
        self.assertEqual({r["channel"] for r in report}, {"site", "telegram"})

    def test_himoya_dry_run_writes_and_sends_nothing(self):
        sent, report, conn = self._himoya(dry_run=True)
        self.assertEqual(sent, 2)
        self.assertFalse(any("INSERT" in q for q in conn.sql()))
        self.tg.assert_not_called()
        self.assertIn(("rollback",), conn.log)
        self.assertEqual(len(report), 2)

    def test_himoya_no_match_for_other_code(self):
        conn = FakeConn(rules=[("SELECT LOWER(email), id FROM users", [("a@x.uz", 7)]),
                               ("SELECT COUNT(*) FROM user_alerts", [(0,)])])
        audience = [{"email": "a@x.uz", "telegram_id": None, "ixtisoslik": "08.00.01",
                     "prefs": {}}]
        with mock.patch.object(data, "get_connection", lambda: conn), \
                mock.patch.object(reminders, "_fetch_audience", lambda cur: audience):
            self.assertEqual(reminders.notify_himoya_matches([REC]), 0)

    def test_himoya_telegram_failure_is_logged(self):
        self.tg.return_value = False
        with self.assertLogs("blueprints.reminders", level="WARNING"):
            sent, report, conn = self._himoya(dry_run=False)
        self.assertEqual(sent, 1)

    def test_subscriptions_dry_run(self):
        conn = FakeConn(rules=[("FROM specialty_subscriptions s",
                                [(3, 7, "05.01.01", True, True, 555, "a@x.uz")]),
                               ("SELECT sent_via", [])])
        report = []
        with mock.patch.object(subscriptions, "get_connection", lambda: conn):
            sent = subscriptions.notify_specialty_subscribers([REC], dry_run=True,
                                                              report=report)
        self.assertEqual(sent, 2)
        self.assertFalse(any("INSERT" in q for q in conn.sql()))
        self.tg.assert_not_called()
        self.assertEqual([r["channel"] for r in report], ["site", "telegram"])

    def test_follows_dry_run(self):
        conn = FakeConn(rules=[("FROM scholar_follows", [("керимов фикрат азизович", 9)]),
                               ("FROM diss_notifications", [])])
        report = []
        with mock.patch.object(olimlar_catalog, "get_connection", lambda: conn):
            created = olimlar_catalog.notify_scholar_follows([REC], dry_run=True,
                                                             report=report)
        self.assertEqual(created, 1)
        self.assertFalse(any("INSERT" in q for q in conn.sql()))
        self.assertEqual(report[0]["user_id"], 9)

    def test_dispatcher_errors_are_logged_not_raised(self):
        conn = FakeConn(fail_on="FROM specialty_subscriptions s")
        with mock.patch.object(subscriptions, "get_connection", lambda: conn), \
                self.assertLogs("blueprints.subscriptions", level="ERROR"):
            self.assertEqual(subscriptions.notify_specialty_subscribers([REC]), 0)


class EndpointRemovedTests(unittest.TestCase):
    def test_import_endpoints_return_410(self):
        client = app.app.test_client()
        for path in ("/api/oak/import", "/api/v1/import-oak"):
            resp = client.post(path, json=[], headers={"X-API-KEY": "x"})
            self.assertEqual(resp.status_code, 410, path)

    def test_send_to_site_and_scraper_workflow_are_gone(self):
        self.assertFalse((ROOT / "send_to_site.py").exists())
        self.assertFalse((ROOT / ".github" / "workflows" / "daily.yml").exists())


if __name__ == "__main__":
    unittest.main()
