import os
import tempfile
import unittest
from unittest.mock import patch

import config
from core import accounts, database as db
from core import deps


class DepsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, deps.PKG_DIR, deps.CACHE_DIR)
        config.DATA_DIR = self.tmp.name
        config.DB_PATH = f"{self.tmp.name}/t.db"
        config.SECRET_KEY_PATH = f"{self.tmp.name}/s.key"
        deps.PKG_DIR = f"{self.tmp.name}/pylibs"
        deps.CACHE_DIR = f"{self.tmp.name}/cache"
        accounts._KEY = None
        db.init_db()
        deps._state.update(state="idle", attempt=0, error="")

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.SECRET_KEY_PATH, deps.PKG_DIR, deps.CACHE_DIR = self.saved
        self.tmp.cleanup()

    def test_retries_until_success_then_swaps_stage(self):
        calls = []

        def fake_pip(args):
            calls.append(args)
            if len(calls) < 3:
                return 2, "ProtocolError: Connection broken: IncompleteRead"
            stage = args[args.index("--target") + 1]
            os.makedirs(stage)
            open(os.path.join(stage, "marker.txt"), "w").close()
            return 0, ""

        with patch.object(deps, "_pip", fake_pip), patch.object(deps, "WAITS", [0]), \
                patch("core.net.online", return_value=True), patch.object(deps.time, "sleep"):
            deps._run(["instagrapi"], False)
        self.assertEqual(deps._state["state"], "done")
        self.assertEqual(len(calls), 3)
        self.assertTrue(os.path.exists(os.path.join(deps.PKG_DIR, "marker.txt")))
        self.assertFalse(os.path.exists(deps.PKG_DIR + ".stage"))
        self.assertNotEqual(db.get_setting("deps_checked_at", ""), "")

    def test_permanent_error_stops_early(self):
        with patch.object(deps, "_pip", lambda a: (1, "ERROR: No matching distribution found for x")), \
                patch.object(deps, "WAITS", [0]), patch("core.net.online", return_value=True), \
                patch.object(deps.time, "sleep"):
            deps._run(["x"], False)
        self.assertEqual(deps._state["state"], "failed")

    def test_failed_install_keeps_previous_libs(self):
        os.makedirs(deps.PKG_DIR)
        open(os.path.join(deps.PKG_DIR, "old.txt"), "w").close()
        with patch.object(deps, "_pip", lambda a: (2, "timeout")):
            ok, _ = deps._install_once(["instagrapi"])
        self.assertFalse(ok)
        self.assertTrue(os.path.exists(os.path.join(deps.PKG_DIR, "old.txt")))

    def test_start_is_single_flight(self):
        deps._state["state"] = "installing"
        self.assertFalse(deps.start(upgrade=True))


if __name__ == "__main__":
    unittest.main()
