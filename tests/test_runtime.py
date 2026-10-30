from pathlib import Path
import tempfile
import unittest

import yaml

from codex_mihomo.provider import show
from codex_mihomo.runtime import ensure_include, install
from codex_mihomo.settings import load_settings, repo_root, Settings


PRIVATE = [
    "/Kno" + "win/sim/",
    "ml" + "platform",
    "liang" + "xin",
    "mo" + "jie",
    "7770" + "006",
    "lx" + "yun",
    "124.174." + "74",
    "0f8b0a90-" + "c5ab-48e0-8030-34d4c1f0a328",
    "volc" + "engine-codex-bridge",
    "cfyes." + "777",
]


class RuntimeTests(unittest.TestCase):
    def test_example_settings_load_and_stay_loopback(self):
        settings = load_settings(repo_root() / "settings.example.yaml")
        self.assertEqual(settings.mixed_port, 19082)
        self.assertEqual(settings.controller_port, 19083)
        self.assertEqual(settings.bind_address, "127.0.0.1")
        self.assertTrue(str(settings.home).endswith(".codex-mihomo"))

    def test_public_bind_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.yaml"
            path.write_text("bind_address: 0.0.0.0\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)

    def test_listing_does_not_print_subscription_url(self):
        token = "super-secret-token"
        url = "https://example.invalid/" + token
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            settings = Settings.defaults(home)
            profile = home / "profiles" / "alpha"
            profile.mkdir(parents=True)
            (profile / "url.txt").write_text(url, encoding="utf-8")
            (profile / "subscription.yaml").write_text("proxies: []\n", encoding="utf-8")
            (profile / "metadata.json").write_text('{"nodes": 1, "eligible_nodes": 1}\n', encoding="utf-8")
            (home / "config.yaml").write_text(yaml.safe_dump({
                "proxy-providers": {"subscription": {"url": url}}
            }), encoding="utf-8")
            from contextlib import redirect_stdout
            from io import StringIO
            stream = StringIO()
            with redirect_stdout(stream):
                show(settings)
            text = stream.getvalue()
            self.assertIn("alpha", text)
            self.assertNotIn(token, text)
            self.assertNotIn(url, text)

    def test_include_keeps_existing_files_line(self):
        path = "/tmp/codex-mihomo-supervisor.ini"
        first = ensure_include("[include]\nfiles = /other/*.ini\n", path)
        self.assertEqual(first.count("[include]"), 1)
        self.assertIn("/other/*.ini " + path, first)
        second = ensure_include(first, path)
        self.assertEqual(second, first)

    def test_install_prepares_both_programs_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            settings = Settings.defaults(home)
            shell_rc = Path(tmp) / "bashrc"
            shell_rc.write_text("# existing\n", encoding="utf-8")
            install(settings, shell_rc=shell_rc)
            install(settings, shell_rc=shell_rc)
            ini = (home / "supervisor.ini").read_text(encoding="utf-8")
            self.assertIn("[program:codex-mihomo]\n", ini)
            self.assertIn("[program:codex-mihomo-health]\n", ini)
            self.assertEqual(ini.count("autostart=true"), 2)
            self.assertIn("http://127.0.0.1:19082", (home / "proxy-env.sh").read_text(encoding="utf-8"))
            self.assertIn(str(repo_root()), ini)
            self.assertEqual(shell_rc.read_text(encoding="utf-8").count("[codex-mihomo]"), 1)

    def test_tree_has_no_private_markers(self):
        offenders = []
        for path in repo_root().rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in PRIVATE:
                if marker in text:
                    offenders.append(str(path.relative_to(repo_root())) + ": " + marker)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
