from django.conf import settings

class ManifestStaticFilesStorage:
    def url_converter(self, name):
        def converter(match):
            matched_url = match.group(1)
            if matched_url.startswith(settings.STATIC_URL):
                path = matched_url[len(settings.STATIC_URL):]
                return "url(%s%s)" % (settings.STATIC_URL, self.hashed_name(path))
            return "url(%s)" % matched_url
        return converter

    def hashed_name(self, path):
        return path.replace(".css", ".123.css")
