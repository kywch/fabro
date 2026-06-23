import os
import unittest

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import override_settings


class FileStoragePermissions(unittest.TestCase):
    def test_file_upload_permissions(self):
        self.storage = FileSystemStorage(self.storage_dir, file_permissions_mode=0o654)
        name = self.storage.save("the_file", ContentFile("data"))
        actual_mode = os.stat(self.storage.path(name))[0] & 0o777
        self.assertEqual(actual_mode, 0o654)

    @override_settings(FILE_UPLOAD_PERMISSIONS=None)
    def test_file_upload_default_permissions(self):
        self.storage = FileSystemStorage(self.storage_dir)
        fname = self.storage.save("some_file", ContentFile("data"))
        mode = os.stat(self.storage.path(fname))[0] & 0o777
        self.assertEqual(mode, 0o666 & ~self.umask)
