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


_LEGAL = re.compile(
    r"[,.]?\s*\b(private limited|pvt\.? ltd\.?|pvt\.?|limited|ltd\.?|inc\.?|llp|llc|"
    r"corporation|corp\.?|company|co\.)\s*$",
    re.I,
)
_GENERIC_TAIL = re.compile(
    r"\s+\b(software|technologies|technology|solutions|services|systems|consulting|"
    r"infotech|india|global|international|careers)\s*$",
    re.I,
)


def short_company(name: str) -> str:
    """How a person would write the company name: "Trigent Software Private Limited" -> "Trigent".

    Legal suffixes always go. Generic words like "Software" go too, unless that
    would leave a name of three letters or fewer ("VY Systems" stays).
    """
    out = name.strip()
    while (new := _LEGAL.sub("", out).strip()) != out:
        out = new
    while (new := _GENERIC_TAIL.sub("", out).strip()) != out and len(new.replace(" ", "")) > 3:
        out = new
    if name.isupper():  # "VY SYSTEMS PRIVATE LIMITED" -> "VY Systems"
        out = " ".join(w if len(w) <= 3 else w.capitalize() for w in out.split())
    return out or name.strip()


# ---------- India location detection (for company job boards) ----------

# Cities that mean "India". Mapped through normalise_city() where an alias exists.
INDIAN_CITIES = [
    "bengaluru",
    "bangalore",
    "mumbai",
    "navi mumbai",
    "bombay",
    "pune",
    "hyderabad",
    "secunderabad",
    "chennai",
    "madras",
    "gurgaon",
    "gurugram",
    "noida",
    "greater noida",
    "new delhi",
    "delhi",
    "delhi ncr",
    "ncr",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "kochi",
    "cochin",
    "thiruvananthapuram",
    "trivandrum",
    "coimbatore",
    "indore",
    "chandigarh",
    "mohali",
    "nagpur",
    "surat",
    "vadodara",
    "lucknow",
    "bhubaneswar",
    "visakhapatnam",
    "mysuru",
    "mysore",
    "mangaluru",
    "mangalore",
    "goa",
    "thane",
]
_CITY_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, INDIAN_CITIES), key=len, reverse=True)) + r")\b",
    re.I,
)
_INDIA_RE = re.compile(r"(?i:\bindia\b)|\bIN\b")  # "IN" only in capitals
_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)
# Other countries that show up on boards of companies with offices in India.
_OTHER_COUNTRY_RE = re.compile(
    r"\b(usa|united states|u\.s\.|canada|uk|united kingdom|london|ireland|germany|france|"
    r"netherlands|spain|poland|singapore|malaysia|indonesia|philippines|vietnam|thailand|"
    r"japan|australia|new zealand|uae|dubai|saudi|brazil|mexico|new york|san francisco|"
    r"seattle|toronto|berlin|amsterdam|jakarta|kuala lumpur|manila|sydney|europe|emea|"
    r"apac|latam)\b",
    re.I,
)


def india_location(
    text: str, country: str | None = None, assume_india: bool = False
) -> tuple[bool, str | None, bool]:
    """Decide whether a board's location text means a job in India.

    Returns (in_india, city, is_remote).
    - A known Indian city, the word "India", or country code "IN" means India.
    - "Remote" alone counts only for companies marked india_company in companies.yaml.
    """
    text = text or ""
    city_match = _CITY_RE.search(text)
    city = normalise_city(city_match.group(1).title()) if city_match else None
    if city in ("Ncr", "Delhi Ncr"):
        city = "Delhi"
    remote = bool(_REMOTE_RE.search(text))
    india = bool(city_match) or bool(_INDIA_RE.search(text)) or (country or "").upper() == "IN"
    if not india and assume_india and not _OTHER_COUNTRY_RE.search(text):
        india = True  # e.g. "Remote" or "Head office" at an India-only company
    return india, city, remote


def html_to_text(raw: str) -> str:
    """Job boards send HTML (Greenhouse even sends it escaped). Keep readable text only."""
    import html as _html

    text = _html.unescape(_html.unescape(raw or ""))  # escaped twice on some boards
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h\d|/tr)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
