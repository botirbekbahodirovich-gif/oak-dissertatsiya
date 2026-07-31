"""Static safety/contract checks for migration 006; no database is touched."""
import os
import re
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(
    ROOT, "migrations", "006_create_research_dataset_schema.sql"
)


def strip_comments(sql):
    lines = []
    for line in sql.splitlines():
        lines.append(line.split("--", 1)[0])
    return "\n".join(lines)


class ResearchDatasetMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MIGRATION, encoding="utf-8") as handle:
            cls.raw = handle.read()
        cls.code = strip_comments(cls.raw)
        cls.upper = cls.code.upper()

    def test_single_transaction(self):
        self.assertEqual(self.upper.count("BEGIN;"), 1)
        self.assertEqual(self.upper.count("COMMIT;"), 1)
        self.assertLess(self.upper.index("BEGIN;"), self.upper.index("COMMIT;"))

    def test_all_expected_tables_exist(self):
        expected = (
            "research_import_batches",
            "research_organizations",
            "canonical_researchers",
            "researcher_aliases",
            "researcher_external_profiles",
            "researcher_affiliations",
            "researcher_metric_snapshots",
            "researcher_match_candidates",
        )
        for table in expected:
            self.assertIn(
                f"CREATE TABLE IF NOT EXISTS {table}".upper(), self.upper, table
            )

    def test_create_statements_are_idempotent(self):
        tables = re.findall(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS)?", self.upper)
        indexes = re.findall(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(IF\s+NOT\s+EXISTS)?",
            self.upper,
        )
        self.assertTrue(tables)
        self.assertTrue(indexes)
        self.assertTrue(all(tables), "every CREATE TABLE needs IF NOT EXISTS")
        self.assertTrue(all(indexes), "every CREATE INDEX needs IF NOT EXISTS")

    def test_no_data_or_destructive_statements(self):
        for forbidden in (
            "DROP TABLE",
            "DROP COLUMN",
            "TRUNCATE",
            "DELETE FROM",
            "UPDATE ",
            "INSERT INTO",
        ):
            self.assertNotIn(forbidden, self.upper)
        self.assertNotIn("ALTER TABLE USERS", self.upper)

    def test_existing_fk_types_are_compatible(self):
        self.assertRegex(
            self.code,
            r"university_id\s+INTEGER\s+REFERENCES\s+universities\s*\(id\)",
        )
        self.assertRegex(
            self.code,
            r"olim_profile_id\s+INTEGER\s+REFERENCES\s+olim_profiles\s*\(id\)",
        )
        self.assertRegex(
            self.code,
            r"created_by\s+INTEGER\s+REFERENCES\s+users\s*\(id\)",
        )

    def test_batch_idempotency_and_nonnegative_counts(self):
        self.assertIn("uq_research_batch_file_dataset", self.code)
        self.assertIn("LOWER(file_sha256)", self.code)
        for column in ("total_rows", "inserted_rows", "skipped_rows", "error_rows"):
            self.assertRegex(self.code, rf"{column}\s+INTEGER[^\n]+CHECK \({column} >= 0\)")

    def test_orcid_format_and_partial_uniqueness(self):
        self.assertGreaterEqual(
            self.raw.count("^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$"),
            2,
        )
        self.assertRegex(
            self.code,
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_researchers_orcid[\s\S]+?WHERE orcid IS NOT NULL",
        )

    def test_metric_null_status_and_duplicate_rules(self):
        self.assertRegex(
            self.code, r"h_index\s+INTEGER\s+CHECK \(h_index IS NULL OR h_index >= 0\)"
        )
        for status in ("reported", "source_na", "missing", "invalid"):
            self.assertIn(f"'{status}'", self.code)
        self.assertIn(
            "UNIQUE (external_profile_id, reporting_period, snapshot_date)",
            self.code,
        )
        self.assertRegex(
            self.code,
            r"CREATE UNIQUE INDEX IF NOT EXISTS uq_external_profile_source_author[\s\S]+?\(source, source_author_id\)",
        )

    def test_candidate_review_is_never_implicitly_approved(self):
        self.assertIn("DEFAULT 'pending'", self.code)
        self.assertIn("'high_confidence_candidate'", self.code)
        self.assertIn(
            "UNIQUE (scopus_profile_id, scholar_profile_id)", self.code
        )

    def test_ranking_links_are_nullable_and_guarded(self):
        self.assertIn("IF to_regclass('h_index_institutions') IS NOT NULL", self.code)
        self.assertIn("ADD COLUMN IF NOT EXISTS research_organization_id BIGINT", self.code)
        self.assertIn("IF to_regclass('h_index_scholars') IS NOT NULL", self.code)
        self.assertIn("ADD COLUMN IF NOT EXISTS canonical_researcher_id BIGINT", self.code)
        self.assertNotIn("research_organization_id BIGINT NOT NULL", self.code)
        self.assertNotIn("canonical_researcher_id BIGINT NOT NULL", self.code)


if __name__ == "__main__":
    unittest.main()
