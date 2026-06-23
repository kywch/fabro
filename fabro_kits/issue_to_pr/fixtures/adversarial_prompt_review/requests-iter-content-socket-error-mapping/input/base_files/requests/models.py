"""This module contains the primary objects that power Requests."""

import collections
import datetime

from io import BytesIO, UnsupportedOperation
from .hooks import default_hooks
from .packages.urllib3.util import parse_url
from .packages.urllib3.exceptions import DecodeError
from .exceptions import (
    HTTPError, RequestException, MissingSchema, InvalidURL,
    ChunkedEncodingError, ContentDecodingError)
from .utils import (
    guess_filename, get_auth_from_url, requote_uri,
    stream_decode_response_unicode, to_key_val_list, parse_header_links,
)


class Response(object):
    def iter_content(self, chunk_size=1, decode_unicode=False):
        def generate():
            if hasattr(self.raw, 'stream'):
                try:
                    for chunk in self.raw.stream(chunk_size, decode_content=True):
                        yield chunk
                except ProtocolError as e:
                    raise ChunkedEncodingError(e)
                except DecodeError as e:
                    raise ContentDecodingError(e)
            except AttributeError:
                while True:
                    chunk = self.raw.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        chunks = generate()
        if decode_unicode:
            chunks = stream_decode_response_unicode(chunks, self)
        return chunks
