from django.core.files.storage import default_storage
from django.test import SimpleTestCase


class OverrideSettingsTests(SimpleTestCase):
    def test_override_file_upload_permissions(self):
        """
        Overriding FILE_UPLOAD_PERMISSIONS should update
        the file_permissions_mode attribute of
        django.core.files.storage.default_storage.
        """
        self.assertIsNone(default_storage.file_permissions_mode)
        with self.settings(FILE_UPLOAD_PERMISSIONS=0o777):
            self.assertEqual(default_storage.file_permissions_mode, 0o777)
