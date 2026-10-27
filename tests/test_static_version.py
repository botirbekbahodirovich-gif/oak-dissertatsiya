import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "static-version-test-secret")
# Empty values prevent python-dotenv from inheriting a parent workspace's .env.
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

from utils import static_version
from utils.static_version import describe_static_version, resolve_static_version

SHA = "0123456789abcdef0123456789abcdef01234567"
EXPECTED_QUERY = "?v={{ STATIC_VERSION }}"

# Local CSS/JS references. Third-party files under /static/vendor/ carry their own
# library version (pdfjs_version) and are deliberately excluded.
LITERAL_REF = re.compile(
    r"""["'](?P<path>/static/(?!vendor/)[^"'\s?{}]+\.(?:css|js|mjs))"""
    r"""(?P<query>\?v=\{\{ STATIC_VERSION \}\}|\?[^"'\s]*)?(?=["'\s])""")
URL_FOR_REF = re.compile(
    r"""\{\{\s*url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"](?P<path>[^'"]+\.(?:css|js|mjs))['"]\s*\)\s*\}\}"""
    r"""(?P<query>\?v=\{\{ STATIC_VERSION \}\}|\?[^"'\s]*)?(?=["'\s])""")


class ResolveStaticVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="static-version-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_environment_override_wins_over_the_build_file(self):
        self._write("BUILD_VERSION", SHA + "\n")
        self.assertEqual(describe_static_version(str(self.root), {"STATIC_VERSION": "rc-2"}),
                         ("rc-2", "environment"))

    def test_unsafe_environment_value_is_ignored(self):
        self._write("BUILD_VERSION", SHA + "\n")
        for bad in ('"><script>alert(1)</script>', "a b", "x" * 41, ""):
            value, source = describe_static_version(str(self.root), {"STATIC_VERSION": bad})
            self.assertEqual((value, source), (SHA[:10], "build-file"), bad)

    def test_expanded_build_file_gives_the_commit_prefix(self):
        self._write("BUILD_VERSION", SHA + "\n")
        self.assertEqual(describe_static_version(str(self.root), {}), (SHA[:10], "build-file"))

    def test_unexpanded_placeholder_is_not_used(self):
        # `git archive` without export-subst, or a plain checkout, leaves the literal text.
        self._write("BUILD_VERSION", "$Format:%H$\n")
        value, source = describe_static_version(str(self.root), {})
        self.assertNotEqual(source, "build-file")
        self.assertNotIn("$", value)

    def test_git_checkout_is_used_when_there_is_no_build_file(self):
        (self.root / ".git").mkdir()
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="b" * 40 + "\n", stderr="")
        with patch.object(static_version.subprocess, "run", return_value=done) as run:
            self.assertEqual(describe_static_version(str(self.root), {}), ("b" * 10, "git"))
        self.assertEqual(run.call_args[0][0][:3], ["git", "-C", str(self.root)])

    def test_a_failing_git_falls_through_to_the_asset_digest(self):
        (self.root / ".git").mkdir()
        self._write("static/css/a.css", "body{}")
        broken = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="dubious ownership")
        with patch.object(static_version.subprocess, "run", return_value=broken):
            self.assertEqual(describe_static_version(str(self.root), {})[1], "assets")
        with patch.object(static_version.subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(describe_static_version(str(self.root), {})[1], "assets")

    def test_asset_digest_changes_only_when_an_asset_changes(self):
        css = self._write("static/css/a.css", "body{}")
        self._write("static/js/b.js", "1")
        self._write("static/images/ignored.png", "x")
        first = describe_static_version(str(self.root), {})
        self.assertEqual(first[1], "assets")
        self.assertEqual(describe_static_version(str(self.root), {}), first)
        (self.root / "static/images/ignored.png").write_text("changed")
        self.assertEqual(describe_static_version(str(self.root), {}), first)
        css.write_text("body{color:red}")
        self.assertNotEqual(describe_static_version(str(self.root), {})[0], first[0])

    def test_fallback_when_nothing_is_available(self):
        self.assertEqual(describe_static_version(str(self.root), {}), ("dev", "fallback"))

    def test_every_source_returns_an_attribute_safe_value(self):
        self._write("BUILD_VERSION", SHA.upper() + "\n")
        for environ in ({}, {"STATIC_VERSION": "Release_1.2-rc"}):
            value = resolve_static_version(str(self.root), environ)
            self.assertRegex(value, r"^[A-Za-z0-9._-]{1,40}$")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class BuildVersionExportTests(unittest.TestCase):
    """`git archive` must turn BUILD_VERSION into the commit SHA — the deploy relies on it."""

    def test_repository_declares_export_subst_for_build_version(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        self.assertIn("BUILD_VERSION export-subst", [line.strip() for line in attributes])

    def test_build_version_file_holds_the_placeholder_or_a_sha(self):
        content = (ROOT / "BUILD_VERSION").read_text(encoding="ascii").strip()
        self.assertRegex(content, r"^(\$Format:%H\$|[0-9a-f]{40})$")

    def test_git_archive_expands_the_placeholder_to_the_commit_sha(self):
        work = Path(tempfile.mkdtemp(prefix="build-version-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")

        def git(*args):
            return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=work, env=env,
                                  check=True, capture_output=True, text=True).stdout

        git("init", "-q")
        (work / ".gitattributes").write_text("BUILD_VERSION export-subst\n")
        # The placeholder file from this repository (a tree that was itself deployed
        # from an archive already holds a SHA, so fall back to the literal there).
        placeholder = (ROOT / "BUILD_VERSION").read_text(encoding="ascii")
        (work / "BUILD_VERSION").write_text(
            placeholder if placeholder.startswith("$Format:") else "$Format:%H$\n")
        git("add", ".")
        git("commit", "-q", "-m", "build")
        sha = git("rev-parse", "HEAD").strip()
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=work, env=env, check=True,
                                 capture_output=True).stdout
        target = work / "extracted"
        target.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(target, filter="data")
            else:
                tar.extractall(target)
        self.assertEqual((target / "BUILD_VERSION").read_text().strip(), sha)
        self.assertEqual(describe_static_version(str(target), {}), (sha[:10], "build-file"))


class TemplateReferenceTests(unittest.TestCase):
    """Guard against a new template (or an old habit) bringing back an unversioned URL."""

    @classmethod
    def setUpClass(cls):
        cls.templates = sorted((ROOT / "templates").rglob("*.html"))

    def _offenders(self, pattern):
        found, bad = 0, []
        for path in self.templates:
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                found += 1
                if match.group("query") != EXPECTED_QUERY:
                    line = text.count("\n", 0, match.start()) + 1
                    bad.append("%s:%d %s%s" % (path.relative_to(ROOT), line, match.group("path"),
                                               match.group("query") or ""))
        return found, bad

    def test_literal_static_css_and_js_carry_static_version(self):
        found, bad = self._offenders(LITERAL_REF)
        self.assertGreaterEqual(found, 40, "the scan found suspiciously few references")
        self.assertEqual(bad, [], "use ?v={{ STATIC_VERSION }} on every local CSS/JS URL")

    def test_url_for_static_css_and_js_carry_static_version(self):
        found, bad = self._offenders(URL_FOR_REF)
        self.assertGreaterEqual(found, 5, "the scan found suspiciously few references")
        self.assertEqual(bad, [], "use ?v={{ STATIC_VERSION }} on every url_for('static', ...) CSS/JS")

    def test_vendored_libraries_keep_their_own_version(self):
        viewer = (ROOT / "templates" / "maqolalar_oqish.html").read_text(encoding="utf-8")
        self.assertIn("/static/vendor/pdfjs/pdf.min.js?v={{ pdfjs_version }}", viewer)


class RenderedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.app = app_module.app
        cls.version = app_module.STATIC_VERSION
        cls.client = cls.app.test_client()

    def test_version_is_available_to_every_template(self):
        context = {}
        with self.app.test_request_context("/"):
            self.app.update_template_context(context)
        self.assertEqual(context["STATIC_VERSION"], self.version)
        self.assertRegex(self.version, r"^[A-Za-z0-9._-]{1,40}$")

    def test_rendered_pages_version_every_local_css_and_js_url(self):
        for page in ("/", "/login"):
            html = self.client.get(page).get_data(as_text=True)
            self.assertNotIn("{{ STATIC_VERSION }}", html, page)
            urls = re.findall(r"""(?:href|src)=["'](/static/(?!vendor/)[^"']+\.(?:css|js))(\?[^"']*)?["']""", html)
            self.assertGreaterEqual(len(urls), 4, page)
            for path, query in urls:
                self.assertEqual(query, "?v=" + self.version, "%s -> %s" % (page, path))

    def test_design_system_stylesheet_url_changes_with_the_version(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('/static/css/olimlar-design-system.css?v=' + self.version, html)


if __name__ == "__main__":
    unittest.main()
