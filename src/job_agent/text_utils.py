"""Small text helpers shared by source adapters."""

import re

# Different sites spell the same city differently. Map them to one name so
# duplicates are detected and city filters work.
CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "bangalore urban": "Bengaluru",
    "bangalore rural": "Bengaluru",
    "bengaluru urban": "Bengaluru",
    "gurugram": "Gurgaon",
    "gurgaon": "Gurgaon",
    "bombay": "Mumbai",
    "mumbai": "Mumbai",
    "navi mumbai": "Mumbai",
    "madras": "Chennai",
    "chennai": "Chennai",
    "new delhi": "Delhi",
    "delhi": "Delhi",
    "noida": "Noida",
    "greater noida": "Noida",
    "pune": "Pune",
    "hyderabad": "Hyderabad",
    "secunderabad": "Hyderabad",
}


def normalise_city(city: str | None) -> str | None:
    if not city:
        return None
    cleaned = city.strip()
    return CITY_ALIASES.get(cleaned.lower(), cleaned)


def is_known_city(name: str | None) -> bool:
    return bool(name) and name.strip().lower() in CITY_ALIASES


def city_from_area(area: list[str]) -> str | None:
    """Pick the city out of a location list like ["India", "Karnataka", "Bangalore", "Whitefield"].

    Prefer any level that is a known city, so neighbourhoods such as Whitefield or
    Koramangala roll up to Bengaluru. Otherwise use the third level (country, state, city).
    """
    for part in area[1:]:
        if is_known_city(part):
            return normalise_city(part)
    if len(area) >= 3:
        return normalise_city(area[2])
    return normalise_city(area[-1]) if len(area) == 2 else None


_RANGE = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years|yrs|year|yr)", re.I)
_MIN_ONLY = re.compile(r"(\d{1,2})\s*\+\s*(?:years|yrs|year|yr)", re.I)
_FRESHER = re.compile(r"\b(fresher|freshers|entry[- ]level|graduate trainee)\b", re.I)


def extract_experience(text: str) -> tuple[int | None, int | None]:
    """Best-effort guess at the experience range a posting asks for.

    "2-4 years" -> (2, 4), "3+ yrs" -> (3, None), "freshers welcome" -> (0, 1).
    Returns (None, None) when nothing is found.
    """
    if m := _RANGE.search(text):
        low, high = int(m.group(1)), int(m.group(2))
        return (low, high) if low <= high else (high, low)
    if m := _MIN_ONLY.search(text):
        return int(m.group(1)), None
    if _FRESHER.search(text):
        return 0, 1
    return None, None


def looks_remote(*texts: str) -> bool:
    joined = " ".join(t.lower() for t in texts if t)
    return any(w in joined for w in ("remote", "work from home", "wfh"))
