from django.core.files.storage import FileSystemStorage


class FileStorageTests:
    def test_url(self):
        assert FileSystemStorage(base_url="/media/").url("file.txt") == "/media/file.txt"
