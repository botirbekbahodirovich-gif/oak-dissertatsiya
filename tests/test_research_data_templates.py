"""Validate that research CSV templates contain exactly their contract header."""
import csv
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "data_templates")

EXPECTED = {
    "research_organizations_template.csv": [
        "slug", "name", "normalized_name", "rank", "national_value",
        "scopus_value", "wos_value", "scholar_value", "metric_status",
        "reporting_period", "scraped_at", "source_url",
    ],
    "canonical_researchers_template.csv": [
        "canonical_researcher_id", "display_name", "normalized_name", "orcid",
        "primary_org_slug", "canonical_status",
    ],
    "researcher_aliases_template.csv": [
        "canonical_researcher_id", "alias", "normalized_alias", "language", "source",
    ],
    "researcher_external_profiles_template.csv": [
        "canonical_researcher_id", "source", "source_author_id",
        "internal_profile_url", "external_profile_url", "orcid",
    ],
    "researcher_affiliations_template.csv": [
        "canonical_researcher_id", "org_slug", "source", "organization_rank",
        "reporting_period", "is_primary",
    ],
    "researcher_metric_snapshots_template.csv": [
        "canonical_researcher_id", "source", "source_author_id", "h_index",
        "hindex_status", "rank", "reporting_period", "snapshot_date",
        "source_url", "scraped_at",
    ],
    "researcher_match_candidates_template.csv": [
        "organization_slug", "scopus_source_author_id", "scholar_source_author_id",
        "name_similarity", "signals", "decision", "review_status",
    ],
}


class ResearchDataTemplateTests(unittest.TestCase):
    def test_exact_template_set(self):
        actual = {
            name for name in os.listdir(TEMPLATE_DIR)
            if name.startswith(("research_", "researcher_", "canonical_researchers"))
            and name.endswith("_template.csv")
        }
        self.assertEqual(actual, set(EXPECTED))

    def test_templates_have_header_only(self):
        for filename, expected_header in EXPECTED.items():
            with self.subTest(filename=filename):
                path = os.path.join(TEMPLATE_DIR, filename)
                with open(path, newline="", encoding="utf-8") as handle:
                    rows = list(csv.reader(handle))
                self.assertEqual(rows, [expected_header])


if __name__ == "__main__":
    unittest.main()
