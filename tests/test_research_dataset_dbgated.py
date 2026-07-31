"""DB integration checks for migration 006, gated to named test/staging DBs."""
import os
import unittest
import uuid
from urllib.parse import urlparse


TEST_DB = os.environ.get("TEST_DATABASE_URL")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(
    ROOT, "migrations", "006_create_research_dataset_schema.sql"
)


def safe_database_name(url):
    if not url:
        return False
    try:
        name = urlparse(url).path.rsplit("/", 1)[-1].lower()
    except (AttributeError, ValueError):
        return False
    return bool(name) and any(mark in name for mark in ("test", "staging", "stage", "stg"))


SAFE_TEST_DB = safe_database_name(TEST_DB)
SKIP_REASON = (
    "TEST_DATABASE_URL is unset or its database name lacks "
    "test/staging/stage/stg"
)


@unittest.skipUnless(TEST_DB and SAFE_TEST_DB, SKIP_REASON)
class ResearchDatasetDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        from psycopg2 import sql

        cls.psycopg2 = psycopg2
        cls.sql = sql
        cls.conn = psycopg2.connect(TEST_DB)
        cls.conn.autocommit = True
        cls.schema = f"research_schema_test_{uuid.uuid4().hex[:12]}"
        with cls.conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
            cur.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(cls.schema))
            )
            cur.execute("CREATE TABLE users (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE universities (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE olim_profiles (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE h_index_institutions (id SERIAL PRIMARY KEY)")
            cur.execute("CREATE TABLE h_index_scholars (id SERIAL PRIMARY KEY)")
        with open(MIGRATION, encoding="utf-8") as handle:
            cls.migration_sql = handle.read()
        with cls.conn.cursor() as cur:
            cur.execute(cls.migration_sql)

    @classmethod
    def tearDownClass(cls):
        if not getattr(cls, "conn", None):
            return
        try:
            with cls.conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(
                    cls.sql.SQL("DROP SCHEMA {} CASCADE").format(
                        cls.sql.Identifier(cls.schema)
                    )
                )
        finally:
            cls.conn.close()

    def token(self):
        return uuid.uuid4().hex

    def new_researcher(self, orcid=None):
        token = self.token()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO canonical_researchers "
                "(display_name, normalized_name, orcid) VALUES (%s,%s,%s) RETURNING id",
                (f"Researcher {token}", f"researcher {token}", orcid),
            )
            return cur.fetchone()[0]

    def new_profile(self, researcher_id, source="scopus"):
        author_id = f"author-{self.token()}"
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO researcher_external_profiles "
                "(researcher_id, source, source_author_id) VALUES (%s,%s,%s) RETURNING id",
                (researcher_id, source, author_id),
            )
            return cur.fetchone()[0], author_id

    def test_expected_tables_and_ranking_links_exist(self):
        expected = {
            "research_import_batches",
            "research_organizations",
            "canonical_researchers",
            "researcher_aliases",
            "researcher_external_profiles",
            "researcher_affiliations",
            "researcher_metric_snapshots",
            "researcher_match_candidates",
        }
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s",
                (self.schema,),
            )
            tables = {row[0] for row in cur.fetchall()}
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name IN "
                "('h_index_institutions', 'h_index_scholars')",
                (self.schema,),
            )
            columns = set(cur.fetchall())
        self.assertTrue(expected.issubset(tables))
        self.assertIn(
            ("h_index_institutions", "research_organization_id"), columns
        )
        self.assertIn(("h_index_scholars", "canonical_researcher_id"), columns)

    def test_migration_is_idempotent(self):
        with self.conn.cursor() as cur:
            cur.execute(self.migration_sql)

    def test_orcid_format_and_uniqueness(self):
        self.new_researcher("0000-0002-1825-0097")
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            self.new_researcher("0000-0002-1825-0097")
        with self.assertRaises(self.psycopg2.errors.CheckViolation):
            self.new_researcher("not-an-orcid")

    def test_source_na_h_index_is_stored_as_null(self):
        researcher_id = self.new_researcher()
        profile_id, _ = self.new_profile(researcher_id)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO researcher_metric_snapshots "
                "(researcher_id, external_profile_id, source, h_index, "
                "hindex_status, reporting_period, snapshot_date) "
                "VALUES (%s,%s,'scopus',NULL,'source_na','2026-Q2','2026-06-30') "
                "RETURNING h_index, hindex_status",
                (researcher_id, profile_id),
            )
            self.assertEqual(cur.fetchone(), (None, "source_na"))

    def test_external_profile_and_metric_duplicates_are_rejected(self):
        researcher_id = self.new_researcher()
        profile_id, author_id = self.new_profile(researcher_id)
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO researcher_external_profiles "
                    "(researcher_id, source, source_author_id) VALUES (%s,'scopus',%s)",
                    (researcher_id, author_id),
                )
        values = (researcher_id, profile_id)
        statement = (
            "INSERT INTO researcher_metric_snapshots "
            "(researcher_id, external_profile_id, source, h_index, hindex_status, "
            "reporting_period, snapshot_date) "
            "VALUES (%s,%s,'scopus',12,'reported','2026-Q2','2026-06-30')"
        )
        with self.conn.cursor() as cur:
            cur.execute(statement, values)
        with self.assertRaises(self.psycopg2.errors.UniqueViolation):
            with self.conn.cursor() as cur:
                cur.execute(statement, values)


if __name__ == "__main__":
    unittest.main()
