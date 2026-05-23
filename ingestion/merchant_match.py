"""
ingestion/merchant_match.py — Merchant name normalisation and fuzzy matching.

Normalises raw bank descriptions by stripping noise (numbers, location codes,
legal suffixes, Australian state/territory codes) and then attempts to fuzzy-
match against existing merchant names using substring containment.

Public API:
    normalise(raw)              → clean title-cased string, e.g. "Woolworths"
    find_fuzzy_match(conn, raw) → (merchant_id, merchant_name, category_id) | None
"""

import re
import sqlite3

# ---------------------------------------------------------------------------
# Noise patterns to strip before normalisation
# ---------------------------------------------------------------------------

# Legal suffixes
_LEGAL = r"\b(pty|ltd|pty\.?\s*ltd|inc|llc|co|corp|corporation|limited|proprietary)\b"

# Australian state/territory codes and common city names
_STATES = r"\b(nsw|vic|qld|sa|wa|tas|act|nt)\b"
_CITIES = r"\b(sydney|melbourne|brisbane|perth|adelaide|hobart|darwin|canberra|auckland)\b"

# Airport/transit codes (3-letter all-caps)
_AIRPORT = r"\b[A-Z]{3}\b"

# Standalone numbers and short codes (including card suffixes like *1234)
_NUMBERS = r"\*?\d[\d\s\-]*"

# Common bank noise words
_NOISE_WORDS = r"\b(au|aus|australia|australian|online|store|shop|#|ref|txn|payment|purchase|debit|credit|tap|paywave|payid|recurring)\b"

# Compile all into one ordered pipeline
_STRIP_PATTERNS = [
    (re.compile(_LEGAL,       re.IGNORECASE), " "),
    (re.compile(_STATES,      re.IGNORECASE), " "),
    (re.compile(_CITIES,      re.IGNORECASE), " "),
    (re.compile(_NOISE_WORDS, re.IGNORECASE), " "),
    (re.compile(_AIRPORT),                    " "),   # after lowercasing guard
    (re.compile(_NUMBERS),                    " "),
]

# Characters that aren't letters, digits, spaces, or ampersands
_NON_ALPHA = re.compile(r"[^a-zA-Z0-9&\s]")

# Collapse multiple spaces
_SPACES = re.compile(r"\s{2,}")


def normalise(raw: str) -> str:
    """
    Strip noise from a raw bank description and return a title-cased name
    suitable for use as a canonical merchant name.

    Examples:
        "WOOLWORTHS 3142 SYDNEY"          → "Woolworths"
        "QANTAS AIRWAYS PTY LTD SYD"      → "Qantas Airways"
        "NETFLIX.COM"                     → "Netflix Com"
        "UBER* TRIP NSW"                  → "Uber Trip"
        "APPLE.COM/BILL"                  → "Apple Com Bill"
    """
    s = raw

    for pattern, replacement in _STRIP_PATTERNS:
        s = pattern.sub(replacement, s)

    # Remove non-alpha characters (dots, slashes, asterisks, etc.)
    s = _NON_ALPHA.sub(" ", s)

    # Collapse whitespace and title-case
    s = _SPACES.sub(" ", s).strip().title()

    # Fall back to title-cased raw if we stripped everything
    return s if s else raw.strip().title()


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def _significant_words(text: str) -> list[str]:
    """
    Return meaningful words from a string (>= 3 chars, not stop words).
    Used to extract the "core" of a raw description for matching.
    """
    _STOP = {"the", "and", "for", "via", "from", "with", "ltd", "pty"}
    return [
        w for w in text.lower().split()
        if len(w) >= 3 and w not in _STOP
    ]


def find_fuzzy_match(
    conn: sqlite3.Connection,
    raw: str,
) -> tuple[int, str, int] | None:
    """
    Try to match a raw description against existing merchant names using
    case-insensitive substring containment.

    Strategy (in order):
      1. Normalise the raw string → candidate
      2. For each known merchant, check:
           a. merchant name is contained in candidate (raw contains merchant)
           b. candidate is contained in merchant name (merchant contains candidate)
           c. any significant word from candidate appears in merchant name
      3. Return the first match as (merchant_id, merchant_name, category_id)
         or None if nothing matched.
    """
    merchants = conn.execute(
        "SELECT id, name, category_id FROM merchants ORDER BY LENGTH(name) DESC"
    ).fetchall()

    if not merchants:
        return None

    candidate = normalise(raw).lower()
    raw_lower  = raw.lower()
    sig_words  = _significant_words(candidate)

    for m in merchants:
        mname = m["name"].lower()

        # a. merchant name substring of raw/candidate  e.g. "woolworths" in "woolworths 3142 sydney"
        if mname in raw_lower or mname in candidate:
            return m["id"], m["name"], m["category_id"]

        # b. candidate substring of merchant name  e.g. "qantas" in "qantas airways"
        if candidate in mname:
            return m["id"], m["name"], m["category_id"]

        # c. any significant word matches  e.g. "netflix" in "netflix com"
        if any(word in mname for word in sig_words):
            return m["id"], m["name"], m["category_id"]

    return None