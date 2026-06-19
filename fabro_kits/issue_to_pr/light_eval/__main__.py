"""Command entry point for the lightweight issue-to-PR eval package."""

from __future__ import annotations

import sys

from .cli import main

raise SystemExit(main(sys.argv[1:]))
