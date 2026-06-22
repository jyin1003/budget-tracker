"""
ingestion/merchant_match.py — Merchant name normalisation and fuzzy matching.

Normalises raw bank descriptions by stripping noise (numbers, location codes,
legal suffixes, Australian state/territory codes) and then ranks existing
merchants by match confidence using a scoring system.

Merchants are fetched ordered by transaction frequency (most common first),
allowing early exit when a high-confidence match is found.

Public API:
    normalise(raw)                          → clean title-cased string
    find_candidates(conn, raw, limit, min_score)
                                            → list[(merchant_id, name, category_id, score)]
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
    (re.compile(_AIRPORT),                    " "),
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

    s = _NON_ALPHA.sub(" ", s)
    s = _SPACES.sub(" ", s).strip().title()

    return s if s else raw.strip().title()


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _significant_words(text: str) -> list[str]:
    """
    Return meaningful words from a string (>= 4 chars, not stop words).
    """
    _STOP = {"the", "and", "for", "via", "from", "with", "ltd", "pty"}
    return [
        w for w in text.lower().split()
        if len(w) >= 4 and w not in _STOP
    ]


def _score(raw: str, candidate_norm: str, mname: str, mname_norm: str) -> int:
    """
    Return a confidence score 0–100 for how well a merchant matches a raw
    description.

    Scoring tiers:
      100  full merchant name (raw)  found verbatim in raw string
       90  full merchant name (raw)  found in normalised candidate
       80  full merchant name (norm) found in raw string
       70  full merchant name (norm) found in normalised candidate
       60  normalised candidate is a substring of merchant name
      0–50 word overlap, weighted by coverage (proportion of words matched)
    """
    raw_lower  = raw.lower()

    if mname in raw_lower:
        return 100
    if mname in candidate_norm:
        return 90
    if mname_norm in raw_lower:
        return 80
    if mname_norm in candidate_norm:
        return 70
    if candidate_norm and candidate_norm in mname_norm:
        return 60

    # Word overlap: score proportional to coverage in both directions
    sig_cand  = _significant_words(candidate_norm)
    sig_merch = _significant_words(mname_norm)

    if not sig_cand or not sig_merch:
        return 0

    matched_in_merch = sum(1 for w in sig_cand  if w in mname_norm)
    matched_in_cand  = sum(1 for w in sig_merch if w in candidate_norm)

    # Harmonic-mean-style coverage: both directions must agree
    cov_a = matched_in_merch / len(sig_cand)
    cov_b = matched_in_cand  / len(sig_merch)
    coverage = (cov_a + cov_b) / 2

    return int(50 * coverage) if coverage > 0 else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_candidates(
    conn: sqlite3.Connection,
    raw: str,
    limit: int = 5,
    min_score: int = 30,
) -> list[tuple[int, str, int, int]]:
    """
    Return the top `limit` merchant candidates for a raw description, ranked
    by match score descending.

    Merchants are fetched ordered by transaction frequency (most common first)
    so that high-confidence early-exit hits are likely to be the most relevant.
    Exits as soon as a score-100 match is found.

    Args:
        conn:      DB connection
        raw:       raw bank description string
        limit:     maximum number of candidates to return (default 5)
        min_score: minimum score to include in results (default 30)

    Returns:
        list of (merchant_id, merchant_name, category_id, score)
        sorted by score descending, empty list if no matches above min_score.
    """
    # Fetch merchants ordered by transaction frequency (most-used first).
    # This makes early exit on score=100 more likely to return the right merchant.
    merchants = conn.execute("""
        SELECT m.id, m.name, m.category_id,
               COUNT(t.id) AS tx_count
        FROM merchants m
        LEFT JOIN transactions t ON t.merchant_id = m.id
        GROUP BY m.id
        ORDER BY tx_count DESC, m.name ASC
    """).fetchall()

    if not merchants:
        return []

    candidate_norm = normalise(raw).lower()
    raw_lower      = raw.lower()

    scored: list[tuple[int, str, int, int]] = []

    for m in merchants:
        mname      = m["name"].lower()
        mname_norm = normalise(m["name"]).lower()

        s = _score(raw_lower, candidate_norm, mname, mname_norm)

        if s < min_score:
            continue

        scored.append((m["id"], m["name"], m["category_id"], s))

        # Early exit: a perfect verbatim match won't be beaten
        if s == 100:
            break

    # Sort by score descending, then by tx_count descending for ties
    # (tx_count isn't in `scored` so we re-sort by score only; frequency
    # ordering from the DB query already broke ties naturally)
    scored.sort(key=lambda x: x[3], reverse=True)

    return scored[:limit]