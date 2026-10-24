import json
import os
import re
import unittest

import config
from core import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class UiShellTests(unittest.TestCase):
    def test_versions_agree(self):
        html = _read("static", "index.html")
        self.assertIn(f'version:"{config.APP_VERSION}"', html)
        self.assertEqual(json.loads(_read("version.json"))["version"], config.APP_VERSION)
        self.assertIn(f'sm-{config.APP_VERSION}', _read("static", "sw.js"))

    def test_legacy_ids_survive(self):
        html = _read("static", "index.html")
        for i in ("page-dash", "page-accounts", "page-content", "page-queue", "page-settings",
                  "platform-chips", "credential-fields", "set-persona", "set-key", "q-badge", "toast"):
            self.assertIn(f'id="{i}"', html)

    def test_csp_has_no_foreign_script_origin(self):
        csp = re.search(r'Content-Security-Policy" content="([^"]+)"', _read("static", "index.html")).group(1)
        script = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
        self.assertNotIn("http", script)

    def test_pwa_files_served_and_others_blocked(self):
        c = server.app.test_client()
        for name in ("manifest.webmanifest", "sw.js", "icon.svg"):
            self.assertEqual(c.get("/" + name).status_code, 200, name)
        self.assertEqual(c.get("/secret.key").status_code, 404)
        self.assertEqual(c.get("/..%2Fconfig.py").status_code, 404)


if __name__ == "__main__":
    unittest.main()
