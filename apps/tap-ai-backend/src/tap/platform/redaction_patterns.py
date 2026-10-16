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

# Egress-only patterns (not used by the trace-export redactor): mainland
# China mobile numbers and resident ID numbers. The resident ID pattern must
# be applied before the mobile pattern so an 18-digit ID is never misread as
# an embedded 11-digit mobile number.
CN_MOBILE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
CN_RESIDENT_ID = re.compile(r"(?<![\dA-Za-z])\d{17}[\dXx](?![\dA-Za-z])")
