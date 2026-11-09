"""Text normalization shared by the keyword index, queries and match scores.

- NFKC: folds ligatures (ﬁ → fi) and Arabic presentation forms (ﻣﺮﺎﺑﺤﺔ → مرابحة) that PDF extraction produces.
- Arabic: strips harakat/Quranic marks/tatweel and unifies alef, ya and ta-marbuta variants, then applies
  Light10-style prefix/suffix stripping (Larkey, Ballesteros & Connell, SIGIR 2002; light10 in Lemur).
  SQLite's unicode61 tokenizer treats combining marks as separators, so without this vocalized Arabic
  (كَتَبَ) is indexed as single letters and can never be found.
- Latin words of 6+ letters additionally match by prefix at query time (terminate ~ termination).
"""
import re
import unicodedata

WORD = re.compile(r"\w+", re.UNICODE)
AR_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭـ]")
AR_LETTER = re.compile("[ء-ي]")
AR_FOLD = str.maketrans({"آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا",
                         "ى": "ي", "ة": "ه"})
AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
AR_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")  # after folding ة→ه, ى→ي
STEM = 6

STOP = set("""a an and are as at be by can do does for from has have how i in is it its me my of on or
our that the their this to was what when where which who why will with you your about into than then
there these those were been being please tell give show much many need needed some any all also just
في من على الي عن ما ماذا هل هي هو كم التي الذي الذين ان او مع هذا هذه كيف متي لماذا اين عند كل ثم""".split())


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return AR_MARKS.sub("", text).translate(AR_FOLD)


def stem(word: str) -> str:
    """Light10-style stem for Arabic words; other scripts are returned unchanged."""
    if not AR_LETTER.match(word):
        return word
    for p in AR_PREFIXES:
        if word.startswith(p) and len(word) - len(p) >= 2:
            word = word[len(p):]
            break
    if word.startswith("و") and len(word) >= 4:
        word = word[1:]
    for s in AR_SUFFIXES:
        if word.endswith(s) and len(word) - len(s) >= 2:
            word = word[:-len(s)]
    return word


CASE_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
SPLITTABLE = re.compile(r"_|[a-z][A-Z]|[A-Z]{2}[a-z]")


def identifier_parts(word: str) -> list[str]:
    """getUserName → get, User, Name; refresh_access_token → refresh, access, token; HTTPServer → HTTP, Server.
    Split before case folding; callers keep the whole identifier too (arXiv 2605.18561)."""
    if not SPLITTABLE.search(word):
        return []
    parts = [p for piece in word.split("_") for p in CASE_PARTS.findall(piece)]
    return parts if len(parts) > 1 else []


def _words(text: str) -> list[str]:
    # Marks are removed before splitting: Python's \w does not match Arabic harakat.
    return WORD.findall(AR_MARKS.sub("", unicodedata.normalize("NFKC", text)))


def _fold(word: str) -> str:
    return word.casefold().translate(AR_FOLD)


def tokens(text: str) -> list[str]:
    out = []
    for w in _words(text):
        out.append(stem(_fold(w)))
        out.extend(stem(_fold(p)) for p in identifier_parts(w))
    return out


def index_text(text: str) -> str:
    """What the FTS5 index stores for a passage."""
    return " ".join(tokens(text))


def terms(question: str) -> list[str]:
    """Distinct meaningful query terms (stop words removed before stemming)."""
    out = []
    for w in _words(question):
        folded = _fold(w)
        if len(folded) > 1 and folded not in STOP:
            out.append(stem(folded))
        out.extend(stem(_fold(p)) for p in identifier_parts(w) if _fold(p) not in STOP)
    return list(dict.fromkeys(t for t in out if len(t) > 1))


def fts_query(question: str) -> str:
    # Terms are \w-only (no quotes), so quoting is safe; "abc"* is FTS5's prefix-token syntax.
    return " OR ".join(f'"{t[:STEM]}"*' if len(t) >= STEM and not AR_LETTER.match(t) else f'"{t}"'
                       for t in terms(question))


def coverage(query_terms: list[str], text: str) -> float:
    """Share of the question's terms found in the passage."""
    if not query_terms:
        return 0.0
    words = set(tokens(text))
    prefixes = {w[:STEM] for w in words if len(w) >= STEM}
    return sum(t in words or (len(t) >= STEM and t[:STEM] in prefixes) for t in query_terms) / len(query_terms)
