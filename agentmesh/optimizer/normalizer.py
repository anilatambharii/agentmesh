"""
Prompt Normalizer - strips noise before cache key generation and embedding.

Why this matters for cache hit rate:
  Without normalization:
    "You are a senior software architect. Review this..." != "Review this..."
    "Please summarise Q3 results" != "Summarise Q3 results"
    "def login(user, pwd):" != "def login(username, password):"
    "**Bold** header" != "Bold header"
    "June 13, 2026" != "2026-06-13"
    "colour" != "color"

  With normalization - all of the above hash to the same cache key.

Normalization pipeline (applied in order):
  1. Persona strip     - "You are a senior SWE." -> ""
  2. Polite strip      - "Please can you review" -> "review"
  3. Markdown strip    - "**bold**" -> "bold", "# Header" -> "Header"
  4. Date normalize    - "June 13, 2026" -> "2026-06-13"
  5. Number normalize  - "1,000,000" -> "1000000"
  6. Code canon        - variable names -> canonical placeholders
  7. Spelling norm     - British -> American (colour->color, etc.)
  8. Whitespace        - collapse runs, strip edges
  9. Punctuation       - curly quotes, dashes -> ASCII equivalents
  10. Lowercase
"""

from __future__ import annotations

import re
from typing import Dict, List


# -- 1. Persona / role prefix patterns -----------------------------------------
#
# These introductory sentences carry zero semantic content for cache matching.
# Strip any leading sentence that matches.

_PERSONA_PATTERNS: List[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"^you are (?:a |an |the )[\w ,/-]+[.!]?\s*",
    r"^act as (?:a |an |the )[\w ,/-]+[.!]?\s*",
    r"^as (?:a |an )?(?:senior|junior|expert|experienced|seasoned|principal|staff|lead) [\w ]+,\s*",
    r"^i (?:want|need) you to\s+",
    r"^your task is to\s+",
    r"^your job is to\s+",
]]

# -- 2. Polite / filler word patterns ------------------------------------------

_FILLER_RE = re.compile(
    r"\b(?:please|kindly|could you|can you|would you mind|help me to|"
    r"i would like you to|feel free to)\b[\s,]*",
    re.IGNORECASE,
)

# -- 3. Markdown stripping -----------------------------------------------------

_MD_CODE_FENCE_RE  = re.compile(r"```[\w]*\n?|```", re.MULTILINE)
_MD_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_MD_BOLD_RE        = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_ITALIC_RE      = re.compile(r"\*(.+?)\*|_(.+?)_")
_MD_HEADER_RE      = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LINK_RE        = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_BLOCKQUOTE_RE  = re.compile(r"^\s*>\s+", re.MULTILINE)
_MD_HR_RE          = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MD_LIST_RE        = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    text = _MD_CODE_FENCE_RE.sub(" ", text)
    text = _MD_INLINE_CODE_RE.sub(lambda m: m.group(0)[1:-1], text)
    text = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_HEADER_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    text = _MD_HR_RE.sub(" ", text)
    text = _MD_LIST_RE.sub("", text)
    return text


# -- 4. Date normalization -----------------------------------------------------

_MONTHS: Dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_WRITTEN_RE = re.compile(
    r"\b(?P<a>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))"
    r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})"
    r"|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<a2>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))"
    r"\s+(?P<y2>\d{4})",
    re.IGNORECASE,
)
_DATE_NUMERIC_RE = re.compile(
    r"\b(?P<m>\d{1,2})[/.-](?P<d>\d{1,2})[/.-](?P<y>\d{4})\b"
)


def _normalize_dates(text: str) -> str:
    def _written_sub(m: re.Match) -> str:
        if m.group("a"):
            mon_str, day, year = m.group("a"), m.group("d"), m.group("y")
        else:
            mon_str, day, year = m.group("a2"), m.group("d2"), m.group("y2")
        mon = _MONTHS.get(mon_str.lower()[:3], 0)
        if not mon:
            return m.group(0)
        return f"{year}-{mon:02d}-{int(day):02d}"

    def _numeric_sub(m: re.Match) -> str:
        return f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"

    text = _DATE_WRITTEN_RE.sub(_written_sub, text)
    text = _DATE_NUMERIC_RE.sub(_numeric_sub, text)
    return text


# -- 5. Number normalization ---------------------------------------------------

_NUMBER_COMMA_RE = re.compile(r"\b(\d{1,3})(?:,(\d{3}))+\b")


def _normalize_numbers(text: str) -> str:
    return _NUMBER_COMMA_RE.sub(lambda m: m.group(0).replace(",", ""), text)


# -- 6. Code variable canonicalization ----------------------------------------

_FUNC_SIG_RE = re.compile(r"(def\s+\w+\s*\()([^)]*?)(\))", re.MULTILINE)


def _canon_func_args(match: re.Match) -> str:
    prefix, args, suffix = match.group(1), match.group(2), match.group(3)
    canon = ", ".join(f"arg{i}" for i, _ in enumerate(args.split(",")) if args.strip())
    return f"{prefix}{canon}{suffix}"


# -- 7. British -> American spelling ------------------------------------------

_SPELLING_MAP: Dict[str, str] = {
    "colour": "color", "colours": "colors", "coloured": "colored", "colouring": "coloring",
    "favour": "favor", "favours": "favors", "favourite": "favorite", "favourites": "favorites",
    "behaviour": "behavior", "behaviours": "behaviors", "behavioural": "behavioral",
    "optimise": "optimize", "optimises": "optimizes", "optimised": "optimized", "optimising": "optimizing",
    "organisation": "organization", "organisations": "organizations", "organise": "organize",
    "recognise": "recognize", "recognises": "recognizes", "recognised": "recognized",
    "summarise": "summarize", "summarises": "summarizes", "summarised": "summarized",
    "analyse": "analyze", "analyses": "analyzes", "analysed": "analyzed", "analysing": "analyzing",
    "authorise": "authorize", "authorises": "authorizes", "authorised": "authorized",
    "catalogue": "catalog", "catalogues": "catalogs",
    "centre": "center", "centres": "centers", "centred": "centered",
    "defence": "defense", "defences": "defenses",
    "licence": "license", "licences": "licenses",
    "practise": "practice",
    "programme": "program", "programmes": "programs",
    "travelling": "traveling", "travelled": "traveled", "traveller": "traveler",
    "labelling": "labeling", "labelled": "labeled",
    "modelling": "modeling", "modelled": "modeled",
    "serialise": "serialize", "serialised": "serialized", "serialising": "serializing",
    "initialise": "initialize", "initialised": "initialized", "initialising": "initializing",
    "tokenise": "tokenize", "tokenised": "tokenized", "tokenising": "tokenizing",
}

_SPELLING_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SPELLING_MAP) + r")\b",
    re.IGNORECASE,
)


def _normalize_spelling(text: str) -> str:
    return _SPELLING_RE.sub(lambda m: _SPELLING_MAP[m.group(0).lower()], text)


# -- 8-10. Whitespace / punctuation / case ------------------------------------

# Unicode curly quotes -> straight ASCII quote; backtick -> nothing meaningful
_QUOTE_RE = re.compile("[“”‘’`]")
_DASH_RE  = re.compile("[–—―]")  # en-dash, em-dash, horizontal bar
_WS_RE    = re.compile(r"\s+")


# -- Public API ----------------------------------------------------------------

def normalize_prompt(text: str, strip_code: bool = True) -> str:
    """
    Full normalization pipeline. Returns a cleaned string suitable for:
      - Exact cache key generation (SHA-256)
      - Embedding computation
    """
    if not text:
        return ""

    # 1. Persona strip
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept: List[str] = []
    for sent in sentences:
        stripped = sent.strip()
        is_persona = any(p.match(stripped) for p in _PERSONA_PATTERNS)
        if not is_persona:
            kept.append(stripped)
    text = " ".join(kept)

    # 2. Filler words
    text = _FILLER_RE.sub(" ", text)

    # 3. Markdown strip
    text = _strip_markdown(text)

    # 4. Date normalization
    text = _normalize_dates(text)

    # 5. Number normalization
    text = _normalize_numbers(text)

    # 6. Code variable canonicalization
    if strip_code:
        text = _FUNC_SIG_RE.sub(_canon_func_args, text)

    # 7. British -> American spelling
    text = _normalize_spelling(text)

    # 8. Punctuation
    text = _QUOTE_RE.sub('"', text)
    text = _DASH_RE.sub("-", text)

    # 9. Whitespace collapse
    text = _WS_RE.sub(" ", text).strip()

    # 10. Lowercase
    text = text.lower()

    return text


def extract_core_question(text: str) -> str:
    """
    Pull out the main question / instruction from a prompt.
    Returns the last sentence that ends with '?' or the last sentence overall.
    """
    norm = normalize_prompt(text, strip_code=False)
    sentences = re.split(r"(?<=[.!?])\s+", norm)
    questions = [s for s in sentences if s.endswith("?")]
    if questions:
        return questions[-1].strip()
    return sentences[-1].strip() if sentences else norm
