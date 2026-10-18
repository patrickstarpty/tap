"""Shared redaction regex patterns for egress and export redactors."""

import re

PRIVATE_START = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
SECRET = re.compile(
    r"(?i)(?<![\w-])(?:authorization\s*:\s*bearer\s+|"
    r"(?P<keyquote>[\"']?)(?:api[_-]key|access_token|client_secret|password)(?P=keyquote)\s*[:=]\s*)"
)
ASSIGNMENT_CANDIDATE = re.compile(
    r"(?i)(?<![\w-])[\"']?(?:api[_-]key|access_token|client_secret|password)[\"']?\s*[:=]\s*"
)
EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
    r"(?![\w.-])"
)

# Same character class as `EMAIL`'s local part (the text before `@`), plus
# `@` itself: used by `DraftBatcher` (egress streaming) to widen its
# holdback margin for a trailing run of email-local-or-`@`-capable
# characters that has not yet been terminated by some other character --
# that run could still be an arbitrarily long, in-progress local part
# (possibly already past its own unterminated `@` into a partially-streamed
# domain, whose characters are a subset of this same class), longer than any
# fixed holdback. `@` must stay in this class: without it, a local part
# followed by a still-arriving `@` breaks the trailing run right at the `@`,
# so the match fails entirely and the holdback silently falls back to the
# fixed `holdback_chars` margin -- too short to cover an arbitrarily long
# local part.
#
# This is a `frozenset` plus `email_local_trailing_run_start` (a backward
# scan), not a `$`-anchored regex: a regex `search()` for `"[...]+$"` tries
# matching starting at every position in the text, and since this character
# class matches nearly anything, each failed attempt scans almost to the end
# before the `$` anchor rejects it -- O(len(text)**2) on a long unbroken run
# of these characters (e.g. base64/hex). The backward scan is O(run length).
EMAIL_LOCAL_TRAILING_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.!#$%&'*+/=?^_`{|}~@-"
)


def email_local_trailing_run_start(text: str) -> int | None:
    """The start index of the trailing run of `EMAIL_LOCAL_TRAILING_CHARS`
    at the end of `text`, or `None` if `text` is empty or does not end with
    at least one such character (no trailing run to hold back)."""
    if not text or text[-1] not in EMAIL_LOCAL_TRAILING_CHARS:
        return None
    index = len(text) - 1
    while index > 0 and text[index - 1] in EMAIL_LOCAL_TRAILING_CHARS:
        index -= 1
    return index


# Egress-only patterns (not used by the trace-export redactor): mainland
# China mobile numbers and resident ID numbers. The resident ID pattern must
# be applied before the mobile pattern so an 18-digit ID is never misread as
# an embedded 11-digit mobile number.
CN_MOBILE = re.compile(r"(?<![\dA-Za-z])(?:\+?86[-\s]?)?1[3-9]\d{9}(?![\dA-Za-z])")
CN_RESIDENT_ID = re.compile(r"(?<![\dA-Za-z])\d{17}[\dXx](?![\dA-Za-z])")
