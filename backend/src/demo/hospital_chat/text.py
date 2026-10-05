"""Accent-insensitive matching for Vietnamese names ("tim mach" ~ "Tim mạch")."""

import re
import unicodedata


def fold(value: object) -> str:
    """Lower-case, strip diacritics (đ -> d) and collapse whitespace."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    return " ".join(text.split())


def tokens(value: object) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", fold(value)))


def best_match(requested: object, candidates: dict[str, list[str]]) -> str | None:
    """Pick the candidate key whose aliases match `requested`.

    Tries, in order: exact key, exact alias ignoring case/accents, then a unique
    candidate whose alias tokens contain every requested token.
    """
    if requested in candidates:
        return str(requested)
    wanted = fold(requested)
    if not wanted:
        return None
    for key, aliases in candidates.items():
        if any(fold(a) == wanted for a in [key, *aliases] if a):
            return key
    want_tokens = tokens(requested)
    hits = [
        key
        for key, aliases in candidates.items()
        if want_tokens
        and want_tokens <= set().union(*(tokens(a) for a in [key, *aliases] if a))
    ]
    return hits[0] if len(hits) == 1 else None
