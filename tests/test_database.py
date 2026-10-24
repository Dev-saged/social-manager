import concurrent.futures
import tempfile
import unittest

import config
from core import database as db


class DatabaseBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = config.DB_PATH
        config.DB_PATH = f"{self.temp_dir.name}/test.db"
        db.init_db()

    def tearDown(self):
        config.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_rate_limit_is_shared_between_adapter_instances(self):
        self.assertTrue(db.reserve_rate_slot(7, "comments", 1, now=10000))
        self.assertFalse(db.reserve_rate_slot(7, "comments", 1, now=10001))
        self.assertTrue(db.reserve_rate_slot(7, "comments", 1, now=13601))

    def test_concurrent_rate_reservations_do_not_exceed_the_limit(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: db.reserve_rate_slot(7, "comments", 1, now=10000),
                range(8),
            ))
        self.assertEqual(sum(results), 1)

    def test_rate_limited_approval_is_deferred_until_a_slot_opens(self):
        comment_id = db.add_to_queue(7, "instagram", "target", "post-1", "", {}, "hello")
        with db._conn() as conn:
            conn.execute("UPDATE comment_queue SET status='approved', release_at=1 WHERE id=?", (comment_id,))
        self.assertTrue(db.reserve_rate_slot(7, "comments", 1, now=19000))

        self.assertTrue(db.defer_rate_limited_comment(comment_id, now=20000))

        with db._conn() as conn:
            row = conn.execute("SELECT status, release_at FROM comment_queue WHERE id=?", (comment_id,)).fetchone()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["release_at"], 22600)

    def test_queue_deduplication_is_scoped_to_account_and_platform(self):
        self.assertIsNotNone(db.add_to_queue(1, "instagram", "a", "post-1", "", {}, ""))
        self.assertIsNone(db.add_to_queue(1, "instagram", "a", "post-1", "", {}, ""))
        self.assertIsNotNone(db.add_to_queue(2, "instagram", "b", "post-1", "", {}, ""))
        self.assertIsNotNone(db.add_to_queue(1, "x", "a", "post-1", "", {}, ""))

    def test_account_deletion_removes_owned_work(self):
        db.add_target(5, "target")
        db.schedule_post(5, "content", None, 1000)
        db.add_to_queue(5, "instagram", "target", "post-1", "", {}, "")
        db.reserve_rate_slot(5, "comments", 1, now=10000)

        db.delete_account(5)

        with db._conn() as conn:
            for table in ("targets", "scheduled_posts", "comment_queue", "rate_events"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id=5").fetchone()[0], 0)

    def test_comment_can_only_be_approved_or_rejected_once(self):
        comment_id = db.add_to_queue(7, "instagram", "target", "post-1", "", {}, "draft")

        self.assertTrue(db.approve_comment(comment_id, proposed_comment="edited"))
        self.assertFalse(db.approve_comment(comment_id))
        self.assertFalse(db.reject_comment(comment_id))

        with db._conn() as conn:
            row = conn.execute("SELECT status, proposed_comment FROM comment_queue WHERE id=?", (comment_id,)).fetchone()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["proposed_comment"], "edited")

    def test_rejected_comment_cannot_be_approved(self):
        comment_id = db.add_to_queue(7, "instagram", "target", "post-1", "", {}, "draft")

        self.assertTrue(db.reject_comment(comment_id))
        self.assertFalse(db.approve_comment(comment_id))


if __name__ == "__main__":
    unittest.main()