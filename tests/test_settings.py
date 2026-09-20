import unittest

from app.config.settings import Settings


class SettingsTest(unittest.TestCase):
    def test_imports_and_has_defaults_without_env_file(self):
        s = Settings(_env_file=None)
        self.assertEqual(s.kafka_topic_transactions, "transactions")
        self.assertLessEqual(s.replay_rate_per_sec, s.replay_max_rate_per_sec)
        self.assertTrue(s.model_dir.name == "models")

    def test_env_override(self):
        import os

        os.environ["REPLAY_RATE_PER_SEC"] = "5"
        try:
            self.assertEqual(Settings(_env_file=None).replay_rate_per_sec, 5.0)
        finally:
            del os.environ["REPLAY_RATE_PER_SEC"]


if __name__ == "__main__":
    unittest.main()
