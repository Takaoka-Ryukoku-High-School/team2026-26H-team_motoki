import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import main


class AccountStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(main, "AUTH_DB", root / "accounts.sqlite3"))
        self.patches.enter_context(patch.object(main, "USER_DATA_DIR", root / "users"))
        self.patches.enter_context(patch.object(main, "DATA_FILE", root / "legacy.json"))
        self.patches.enter_context(patch.object(main, "ASSET_DIR", root / "legacy_images"))
        self.patches.enter_context(patch.object(main, "PASSWORD_ITERATIONS", 1_000))
        main.initialize_auth_store()

    def tearDown(self):
        self.patches.close()
        self.temporary_directory.cleanup()

    def test_accounts_have_separate_data_and_passwords(self):
        password = "a-long-test-password"
        self.assertTrue(main.create_account("owner@example.com", password))
        self.assertTrue(main.create_account("guest@example.com", password))

        owner_file, _ = main.user_storage_paths("owner@example.com")
        guest_file, _ = main.user_storage_paths("guest@example.com")
        owner_data = {"items": [{"name": "owner item"}], "events": [], "goals": []}
        main.save_data(owner_data, owner_file)

        self.assertEqual(main.load_data(owner_file), owner_data)
        self.assertEqual(main.load_data(guest_file), main.default_data())
        self.assertTrue(main.authenticate_account("OWNER@example.com", password))
        self.assertFalse(main.authenticate_account("owner@example.com", "wrong-password"))
        self.assertFalse(main.create_account("OWNER@example.com", password))

    def test_first_account_receives_legacy_data_and_its_image(self):
        main.ASSET_DIR.mkdir()
        legacy_image = main.ASSET_DIR / "legacy.jpg"
        legacy_image.write_bytes(b"image bytes")
        legacy_data = {
            "items": [{"name": "legacy item", "image_path": str(legacy_image)}],
            "events": [],
            "goals": [],
        }
        main.DATA_FILE.write_text(json.dumps(legacy_data), encoding="utf-8")

        self.assertTrue(main.create_account("owner@example.com", "a-long-test-password"))
        data_file, _ = main.user_storage_paths("owner@example.com")
        migrated = main.load_data(data_file)
        migrated_image = Path(migrated["items"][0]["image_path"])

        self.assertEqual(migrated["items"][0]["name"], "legacy item")
        self.assertEqual(migrated_image.read_bytes(), b"image bytes")
        self.assertTrue(main.DATA_FILE.exists())


if __name__ == "__main__":
    unittest.main()
