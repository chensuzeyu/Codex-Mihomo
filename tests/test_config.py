"""Offline checks: no real subscription, proxy connection, or service reload."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from codex_mihomo import apply as apply_mod
from codex_mihomo import provider
from codex_mihomo.settings import Settings


def sample(url="https://example.invalid/one"):
    return provider.build({"proxies": [
        {"name": "日本 JP", "type": "http", "server": "example.invalid", "port": 443},
        {"name": "Singapore SG", "type": "http", "server": "example.invalid", "port": 443},
        {"name": "台灣 TW", "type": "http", "server": "example.invalid", "port": 443},
        {"name": "香港 HK", "type": "http", "server": "example.invalid", "port": 443},
    ]}, url)


class ConfigTests(unittest.TestCase):
    def test_region_and_automatic_groups(self):
        config = sample()
        self.assertEqual(len(config["proxies"]), 3)
        self.assertEqual(config["mixed-port"], 19082)
        self.assertEqual(config["external-controller"], "127.0.0.1:19083")
        self.assertEqual(config["proxy-groups"][0]["type"], "url-test")
        self.assertEqual(config["proxy-groups"][1]["type"], "select")
        self.assertEqual(config["proxy-groups"][0]["tolerance"], 150)
        self.assertFalse(config["ipv6"])
        self.assertFalse(config["allow-lan"])
        self.assertTrue(all(item["ip-version"] == "ipv4" for item in config["proxies"]))
        self.assertFalse(config["proxy-providers"]["subscription"]["health-check"]["lazy"])
        self.assertEqual(config["proxy-providers"]["subscription"]["health-check"]["expected-status"], 405)
        self.assertTrue(all(group["interval"] == 10 and group["max-failed-times"] == 2
                            for group in config["proxy-groups"]))

    def test_blacklist_keeps_other_regions(self):
        source = {"proxies": [{"name": name, "type": "http", "server": "example.invalid", "port": 443}
                              for name in ["US-1", "UK-1", "Network", "HK-1", "Hong Kong 1", "香港", "🇭🇰Node", "剩余流量：100GB"]]}
        config = provider.build(source, "https://example.invalid/one")
        self.assertEqual([item["name"] for item in config["proxies"]],
                         ["BOOTSTRAP-US-1", "BOOTSTRAP-UK-1", "BOOTSTRAP-Network"])

    def test_include_filter_is_optional(self):
        settings = Settings.defaults()
        settings.include_filter = provider.REGION_FILTER
        source = {"proxies": [{"name": name, "type": "http", "server": "example.invalid", "port": 443}
                              for name in ["US-1", "日本 JP", "台湾01"]]}
        config = provider.build(source, "https://example.invalid/one", settings)
        self.assertEqual([item["name"] for item in config["proxies"]], ["BOOTSTRAP-日本 JP", "BOOTSTRAP-台湾01"])

    def test_taiwan_names(self):
        for name in ("台湾01", "台灣02", "臺灣03", "Taiwan 1", "TW-1", "🇹🇼Node"):
            self.assertIsNotNone(provider.REGION.search(name), name)
        for name in ("香港 HK", "Network", "TWILIGHT"):
            self.assertIsNone(provider.REGION.search(name), name)

    def test_service_cache_separation(self):
        paths = [sample(url)["proxy-providers"]["subscription"]["path"]
                 for url in ("https://example.invalid/one", "https://example.invalid/two")]
        self.assertNotEqual(*paths)

    def test_provider_names_are_not_predefined(self):
        self.assertEqual(provider.check_name("alpha"), "alpha")
        with self.assertRaises(SystemExit):
            provider.check_name("all")
        with self.assertRaises(SystemExit):
            provider.check_name("../private")

    def exercise(self, valid=True, reachable=True):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            settings = Settings.defaults(home)
            old = b"previous private config\n"
            (home / "config.yaml").write_bytes(old)
            config = sample()
            if not valid:
                config["bind-address"] = "0.0.0.0"
            (home / "config.next.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
            with patch.object(apply_mod, "validate"), \
                 patch.object(apply_mod, "probe", return_value=reachable), \
                 patch.object(apply_mod.time, "sleep"), \
                 patch.object(apply_mod, "request") as api:
                if valid and reachable:
                    apply_mod.apply_candidate(settings)
                    self.assertNotEqual((home / "config.yaml").read_bytes(), old)
                    self.assertEqual((home / "config.previous.yaml").read_bytes(), old)
                    self.assertFalse((home / "config.next.yaml").exists())
                else:
                    with self.assertRaises((AssertionError, RuntimeError)):
                        apply_mod.apply_candidate(settings)
                    self.assertEqual((home / "config.yaml").read_bytes(), old)
                    if not valid:
                        api.assert_not_called()
                    else:
                        self.assertEqual(sum(call.args[1].startswith("/configs") for call in api.call_args_list), 2)

    def test_successful_reload(self):
        self.exercise()

    def test_invalid_listener_is_rejected(self):
        self.exercise(valid=False)

    def test_failed_probe_restores_previous(self):
        self.exercise(reachable=False)


if __name__ == "__main__":
    unittest.main()
