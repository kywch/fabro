    return isinstance(x, str)


def isdict(x):
    return isinstance(x, dict)

    explanation = None
    try:
        if op == "==":
            if istext(left) and istext(right):
                explanation = _diff_text(left, right, verbose)
            else:
                if issequence(left) and issequence(right):
    def escape_for_readable_diff(binary_text):
        """
        Ensures that the internal string is always valid unicode, converting any bytes safely to valid unicode.
        This is done using repr() which then needs post-processing to fix the encompassing quotes and un-escape
        newlines and carriage returns (#429).
        """
        r = str(repr(binary_text)[1:-1])
        r = r.replace(r"\n", "\n")
        r = r.replace(r"\r", "\r")
        return r
