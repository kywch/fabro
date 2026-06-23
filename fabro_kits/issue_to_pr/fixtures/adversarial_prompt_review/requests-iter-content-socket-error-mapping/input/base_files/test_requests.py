from __future__ import division

import collections
import json
import os
import pickle
import unittest

import requests
from requests.auth import HTTPDigestAuth, _basic_auth_str
from requests.compat import (
    Morsel, cookielib, getproxies, str, urljoin, urlparse, is_py3, builtin_str)
from requests.cookies import cookiejar_from_dict, morsel_to_cookie
from requests.exceptions import InvalidURL, MissingSchema
from requests.models import PreparedRequest
from requests.structures import CaseInsensitiveDict
from requests.sessions import SessionRedirectMixin


class RequestsTestCase(unittest.TestCase):
    def test_iter_content_decode_unicode(self):
        r = requests.Response()
        chunks = r.iter_content(decode_unicode=True)
        assert all(isinstance(chunk, str) for chunk in chunks)

    def test_request_and_response_are_pickleable(self):
        r = requests.get(httpbin('get'))
