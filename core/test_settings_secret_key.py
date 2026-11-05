import ast
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.secret_key import resolve_secret_key


class ResolveSecretKeyTests(SimpleTestCase):
    def test_configured_value_is_used_everywhere(self):
        for debug, argv in ((False, ["manage.py", "migrate"]), (True, ["manage.py", "runserver"])):
            with self.subTest(debug=debug):
                self.assertEqual(resolve_secret_key("configured", debug=debug, argv=argv), "configured")

    def test_missing_value_stops_a_normal_start(self):
        for value in (None, "", "   "):
            for argv in (
                ["gunicorn"],
                ["manage.py", "migrate"],
                ["manage.py", "runserver", "test"],
                ["manage.py"],
            ):
                with self.subTest(value=value, argv=argv):
                    with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_SECRET_KEY"):
                        resolve_secret_key(value, debug=False, argv=argv)

    def test_local_debug_gets_a_fresh_random_key(self):
        first = resolve_secret_key("", debug=True, argv=["manage.py", "runserver"])
        second = resolve_secret_key("", debug=True, argv=["manage.py", "runserver"])
        self.assertGreaterEqual(len(first), 50)
        self.assertNotEqual(first, second)

    def test_test_command_gets_a_fresh_random_key(self):
        first = resolve_secret_key("", debug=False, argv=["manage.py", "test", "core"])
        second = resolve_secret_key("", debug=False, argv=["manage.py", "test", "core"])
        self.assertGreaterEqual(len(first), 50)
        self.assertNotEqual(first, second)


class SettingsSecretKeySourceTests(SimpleTestCase):
    def test_settings_keeps_no_key_in_source(self):
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
        assignments = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SECRET_KEY" for target in node.targets)
        ]
        self.assertEqual(len(assignments), 1)
        literals = [
            node.value
            for node in ast.walk(assignments[0].value)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        self.assertEqual(literals, ["DJANGO_SECRET_KEY"])
