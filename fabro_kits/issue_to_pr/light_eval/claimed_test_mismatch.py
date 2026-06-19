"""Claimed-test-mismatch synthetic case facts."""

from __future__ import annotations


def claimed_test_mismatch_patch_text() -> str:
    return (
        "diff --git a/src/greeting.py b/src/greeting.py\n"
        "--- a/src/greeting.py\n"
        "+++ b/src/greeting.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def greeting(name):\n"
        "-    return f\"hello {name}\"\n"
        "+    return f\"hello, {name}\"\n"
    )
