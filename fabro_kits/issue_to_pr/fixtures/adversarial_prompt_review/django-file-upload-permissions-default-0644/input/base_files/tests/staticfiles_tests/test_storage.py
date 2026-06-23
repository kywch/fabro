import os

from django.conf import settings
from django.core.management import call_command
from django.test import override_settings


class TestStaticFilePermissions(CollectionTestCase):
    @override_settings(FILE_UPLOAD_PERMISSIONS=0o655, FILE_UPLOAD_DIRECTORY_PERMISSIONS=0o765)
    def test_collect_static_files_permissions(self):
        call_command('collectstatic', **self.command_params)
        test_file = os.path.join(settings.STATIC_ROOT, "test.txt")
        test_dir = os.path.join(settings.STATIC_ROOT, "subdir")
        file_mode = os.stat(test_file)[0] & 0o777
        dir_mode = os.stat(test_dir)[0] & 0o777
        self.assertEqual(file_mode, 0o655)
        self.assertEqual(dir_mode, 0o765)

    @override_settings(
        FILE_UPLOAD_PERMISSIONS=None,
        FILE_UPLOAD_DIRECTORY_PERMISSIONS=None,
    )
    def test_collect_static_files_default_permissions(self):
        call_command('collectstatic', **self.command_params)
        test_file = os.path.join(settings.STATIC_ROOT, "test.txt")
        test_dir = os.path.join(settings.STATIC_ROOT, "subdir")
        file_mode = os.stat(test_file)[0] & 0o777
        dir_mode = os.stat(test_dir)[0] & 0o777
        self.assertEqual(file_mode, 0o666 & ~self.umask)
        self.assertEqual(dir_mode, 0o777 & ~self.umask)
