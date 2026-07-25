import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

import create_admin


class FakeStdin:
    def __init__(self, interactive):
        self.interactive = interactive

    def isatty(self):
        return self.interactive


class CreateAdminSecurityTests(unittest.TestCase):
    def test_parser_has_no_password_argument_or_default(self):
        password_actions = [
            action
            for action in create_admin.build_parser()._actions
            if action.dest == "password"
        ]
        self.assertEqual(password_actions, [])

    def test_environment_password_is_used_without_prompting(self):
        prompt = MagicMock(side_effect=AssertionError("prompt must not run"))
        password = create_admin.resolve_admin_password(
            {"ADMIN_PASSWORD": "environment-secret"},
            prompt=prompt,
        )
        self.assertEqual(password, "environment-secret")
        prompt.assert_not_called()

    def test_interactive_password_requires_matching_confirmation(self):
        answers = iter(("interactive-secret", "interactive-secret"))
        password = create_admin.resolve_admin_password(
            {},
            prompt=lambda _message: next(answers),
            stdin=FakeStdin(True),
        )
        self.assertEqual(password, "interactive-secret")

    def test_missing_password_stops_in_non_interactive_mode(self):
        with self.assertRaisesRegex(RuntimeError, "ADMIN_PASSWORD is required"):
            create_admin.resolve_admin_password({}, stdin=FakeStdin(False))

    def test_empty_or_mismatched_interactive_password_stops(self):
        with self.assertRaisesRegex(RuntimeError, "must not be empty"):
            create_admin.resolve_admin_password(
                {},
                prompt=lambda _message: "",
                stdin=FakeStdin(True),
            )
        answers = iter(("first-secret", "second-secret"))
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            create_admin.resolve_admin_password(
                {},
                prompt=lambda _message: next(answers),
                stdin=FakeStdin(True),
            )

    def test_main_never_logs_password(self):
        password = "must-never-appear"
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        connection = MagicMock()
        connection.cursor.return_value = cursor
        output = io.StringIO()
        with (
            patch.object(create_admin.psycopg2, "connect", return_value=connection),
            patch.object(
                create_admin.bcrypt,
                "hashpw",
                return_value=b"hashed-password",
            ),
            contextlib.redirect_stdout(output),
        ):
            result = create_admin.main(
                ["--user", "admin", "--email", "admin@example.test"],
                environ={
                    "DATABASE_URL": "postgresql://localhost/test",
                    "ADMIN_PASSWORD": password,
                },
            )
        self.assertEqual(result, 0)
        self.assertNotIn(password, output.getvalue())
        self.assertIn("created", output.getvalue())


if __name__ == "__main__":
    unittest.main()
