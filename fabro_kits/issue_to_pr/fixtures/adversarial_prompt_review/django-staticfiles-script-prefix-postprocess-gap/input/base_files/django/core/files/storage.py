class FileSystemStorage:
    def __init__(self, base_url="/media/"):
        self.base_url = base_url

    def url(self, name):
        return self.base_url + name
