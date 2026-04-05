"""Shared helpers for scraper implementations."""

import re


def parse_price_cents(raw: str | None, currency: str = "ARS") -> int | None:
    """
    Convert a price string like '$1.250,00' or '1250.00' into integer cents.

    Examples:
        '$1.250,00' → 125000
        '1250'      → 125000
        '12.50'     → 1250  (if currency is USD/EUR)
        'agotado'   → None
    """
    if not raw:
        return None

    # Strip currency symbols and whitespace
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())

    if not cleaned:
        return None

    # Argentine/European format: 1.250,00
    if "," in cleaned and "." in cleaned:
        if cleaned.index(".") < cleaned.index(","):
            # 1.250,00 → remove dots, replace comma with dot
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # 1,250.00 → remove commas
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Could be 1250,00 (ARS) or just 1,250 (thousands separator)
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        # Dot-only: check if it's a thousands separator (e.g. "21.200" in ARS)
        # A dot followed by exactly 3 digits at the end is a thousands separator.
        if re.match(r"^\d{1,3}(\.\d{3})+$", cleaned):
            cleaned = cleaned.replace(".", "")

    try:
        return round(float(cleaned) * 100)
    except ValueError:
        return None


def parse_weight_grams(raw: str | None) -> int | None:
    """
    Extract grams from strings like '250g', '1kg', '500 gr'.

    Returns weight in grams as int, or None if unparseable.
    """
    if not raw:
        return None

    raw = raw.lower().strip()

    kg_match = re.search(r"(\d+(?:\.\d+)?)\s*kg", raw)
    if kg_match:
        return round(float(kg_match.group(1)) * 1000)

    g_match = re.search(r"(\d+)\s*g(?:r(?:amos?)?)?", raw)
    if g_match:
        return int(g_match.group(1))

    return None


def clean_text(text: str | None) -> str | None:
    """Strip excessive whitespace from scraped text."""
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def normalize_name(raw: str | None) -> str | None:
    """
    Clean up a coffee product name:
    - Remove weight mentions like '250g', '250 G', '- 250 G', '1kg', '250-g'
    - Normalize to title case (first letter of each word capitalized)

    Examples:
        'Café de especialidad 250g Tanzania' → 'Café De Especialidad Tanzania'
        'JUAN CHAMORRO CASTILLO LAVADO - 250 G' → 'Juan Chamorro Castillo Lavado'
        '250gr / 1kg Blend' → 'Blend'
    """
    if not raw:
        return None

    # Remove weight patterns with an optional preceding dash/separator.
    # The [\s-]* between the number and unit handles slugs like "250-g" or "1-kg".
    # Handles: "250g", "250 G", "- 250 G", "250-g", "1kg", "1-kg", "250gr", "250gramos"
    cleaned = re.sub(
        r"\s*[-–—]?\s*\d+[\s-]*(?:kg|g(?:r(?:amos?)?)?)s?\b",
        "",
        raw,
        flags=re.IGNORECASE,
    )

    # Clean up any leftover leading/trailing separator characters (e.g. "/ Café" or "Café /")
    cleaned = re.sub(r"^[\s/—–-]+", "", cleaned)
    cleaned = re.sub(r"[\s/—–-]+$", "", cleaned)

    # Collapse multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return None

    return cleaned.title()


def normalize_process(raw: str | None) -> str | None:
    """
    Normalize processing method to one of: Natural, Washed, Honey, Anaerobic, Other.
    """
    if not raw:
        return None

    lower = raw.lower()
    # Anaerobic must come FIRST — it's the most specific.
    # "anaeróbico natural" should map to Anaerobic, not Natural.
    if any(w in lower for w in [
        "anaerobic", "anaeróbico", "anaerobico",
        "doble fermentacion", "doble fermentación",
    ]):
        return "Anaerobic"
    if any(w in lower for w in ["natural", "natur", "seco", "dry"]):
        return "Natural"
    if any(w in lower for w in ["washed", "lavado", "húmedo", "humedo", "wet"]):
        return "Washed"
    if any(w in lower for w in ["honey", "miel"]):
        return "Honey"
    return None


def normalize_intensity(raw: str | None) -> int | None:
    """
    Convert a text or numeric intensity description to a 1–5 integer scale.
    Used for acidity, sweetness, and body.

    Examples:
        "3"              → 3
        "low acidity"    → 1
        "baja acidez"    → 1
        "medium"         → 3
        "vibrant"        → 4
        "alta"           → 5
        "bright"         → 4
        None / ""        → None
    """
    if not raw:
        return None

    raw = raw.strip()

    # Direct numeric value
    if raw.isdigit():
        value = int(raw)
        return value if 1 <= value <= 5 else None

    lower = raw.lower()

    # Level 1 — very low
    if any(w in lower for w in ["very low", "muy baja", "muy bajo", "very light"]):
        return 1
    # Level 2 — low
    if any(w in lower for w in ["low", "baja", "bajo", "leve", "suave", "delicate", "delicada"]):
        return 2
    # Level 4 — high / vibrant
    if any(w in lower for w in ["high", "alta", "alto", "vibrant", "bright", "pronounced", "pronunciada", "intensa", "intense"]):
        return 4
    # Level 5 — very high
    if any(w in lower for w in ["very high", "muy alta", "muy alto", "extreme", "extrema"]):
        return 5
    # Level 3 — medium (catch-all for anything in between)
    if any(w in lower for w in ["medium", "media", "medio", "moderate", "moderada", "balanced", "balanceada"]):
        return 3

    return None


def normalize_brew_methods(raw: str | None) -> list[str] | None:
    """
    Convert a raw brew method string into a list of canonical method names.

    Examples:
        "Espresso / Filtro"         → ["Espresso", "Filtro"]
        "Pour over y aeropress"     → ["Pour Over", "Aeropress"]
        "prensa francesa"           → ["French Press"]
        "unrelated text"            → None
    """
    if not raw:
        return None

    lower = raw.lower()

    methods = []
    if "espresso" in lower:
        methods.append("Espresso")
    if any(w in lower for w in ["filtro", "filter"]):
        methods.append("Filtro")
    if any(w in lower for w in ["pour over", "pourover", "v60", "chemex", "hario", "dripper"]):
        methods.append("Pour Over")
    if "aeropress" in lower:
        methods.append("Aeropress")
    if "moka" in lower:
        methods.append("Moka")
    if any(w in lower for w in ["french press", "prensa francesa", "prensa"]):
        methods.append("French Press")
    if any(w in lower for w in ["cold brew", "cold drip"]):
        methods.append("Cold Brew")

    return methods if methods else None


def _sentence_case(text: str) -> str:
    """
    Sentence-case: capitalize only the first character, leave the rest as-is.
    Input is already lowercased by step 6 of the pipeline.

    Examples:
        "azúcar de caña"      → "Azúcar de caña"
        "chocolate con leche" → "Chocolate con leche"
    """
    if not text:
        return text
    return text[0].upper() + text[1:]


def normalize_tasting_notes(raw_notes: list[str] | None) -> list[str] | None:
    """
    Clean, normalize, and deduplicate a list of tasting notes.

    Pipeline per note:
    1. Replace non-breaking spaces with regular spaces
    2. Strip whitespace
    3. Re-split on '.' followed by text (handles "Anana. chocolate" → two notes)
    4. Strip trailing/leading punctuation
    5. Strip known label prefixes
    6. Lowercase
    7. Apply typo corrections
    8. Apply plural→singular mapping
    9. Split known compound notes
    10. Apply Spanish title case
    11. Filter junk (< 2 chars, known non-notes)
    12. Deduplicate, cap at 6

    Examples:
        ["Durano", "vainilla."] → ["Durazno", "Vainilla"]
        ["Anana. chocolate"]    → ["Ananá", "Chocolate"]
        ["vainilla miel"]       → ["Vainilla", "Miel"]
        ["CHOCOLATE", "chocolate"] → ["Chocolate"]
    """
    if not raw_notes:
        return None

    TYPO_MAP = {
        "durano": "durazno",
        "anana": "ananá",
        "arandano": "arándano",
        "limon": "limón",
        "citrico": "cítrico",
        "citricos": "cítrico",
        "cascara": "cáscara",
    }

    PLURAL_MAP = {
        "arándanos": "arándano",
        "arandanos": "arándano",
        "frutas de hueso": "fruta de hueso",
        "cerezas": "cereza",
        "cítricos": "cítrico",
        "frutos rojos": "fruto rojo",
        "frutos secos": "fruto seco",
        "frutas tropicales": "fruta tropical",
        "flores blancas": "flor blanca",
        "flores": "flor",
    }

    COMPOUND_MAP = {
        "vainilla miel": ["vainilla", "miel"],
        "caramelo chocolate": ["caramelo", "chocolate"],
    }

    JUNK = {"en taza", "perfil en taza", "perfil", "taza", "café", "cafe"}

    LABEL_PREFIXES = re.compile(
        r"^(?:organolépticas|organolepticas|que recuerda a|notas de cata|notas)[:\s]*",
        re.IGNORECASE,
    )

    cleaned: list[str] = []

    for note in raw_notes:
        # 1. Replace non-breaking spaces
        note = note.replace("\xa0", " ")
        # 2. Strip whitespace
        note = note.strip()
        if not note:
            continue

        # 3. Re-split on '.' followed by text (handles "Anana. chocolate")
        parts = re.split(r"\.\s+", note)

        for part in parts:
            # 4. Strip trailing/leading punctuation
            part = part.strip(" \t.,;:")
            if not part:
                continue

            # 5. Strip known label prefixes
            part = LABEL_PREFIXES.sub("", part).strip()
            if not part:
                continue

            # 6. Lowercase for matching
            lower = part.lower()

            # 7. Typo corrections
            lower = TYPO_MAP.get(lower, lower)

            # 8. Plural → singular
            lower = PLURAL_MAP.get(lower, lower)

            # 9. Split known compounds
            if lower in COMPOUND_MAP:
                for sub in COMPOUND_MAP[lower]:
                    cleaned.append(sub)
                continue

            cleaned.append(lower)

    # 10. Spanish title case + 11. Filter junk
    result: list[str] = []
    seen: set[str] = set()
    for note in cleaned:
        if len(note) < 2:
            continue
        if note.lower() in JUNK:
            continue
        titled = _sentence_case(note)
        key = titled.lower()
        if key not in seen:
            seen.add(key)
            result.append(titled)

    # 12. Cap at 6, return None if empty
    result = result[:6]
    return result if result else None


def normalize_origin(name: str | None, text: str | None) -> str | None:
    """
    Extract a coffee origin country from the product name and/or description text.

    Searches the name first (more specific), then falls back to the full text.
    Longer country names are checked before shorter ones to avoid substring
    false matches (e.g. "Papua New Guinea" before "Guinea").

    Returns the canonical country name, or None if no match.
    """
    # Ordered: longer/more-specific names first to avoid substring collisions.
    _ORIGIN_KEYWORDS = [
        # Multi-word names first
        ("papua new guinea", "Papua New Guinea"), ("papúa nueva guinea", "Papua New Guinea"),
        ("costa rica", "Costa Rica"),
        ("el salvador", "El Salvador"),
        # Single-word names
        ("guatemala", "Guatemala"),
        ("colombia", "Colombia"),
        ("ethiopia", "Ethiopia"), ("etiopía", "Ethiopia"), ("etiopia", "Ethiopia"),
        ("nicaragua", "Nicaragua"),
        ("brazil", "Brazil"), ("brasil", "Brazil"),
        ("kenya", "Kenya"), ("kenia", "Kenya"),
        ("honduras", "Honduras"),
        ("panama", "Panamá"), ("panamá", "Panamá"),
        ("rwanda", "Rwanda"), ("ruanda", "Rwanda"),
        ("tanzania", "Tanzania"),
        ("burundi", "Burundi"),
        ("peru", "Perú"), ("perú", "Perú"),
        ("bolivia", "Bolivia"),
        ("yemen", "Yemen"),
        ("indonesia", "Indonesia"),
        ("india", "India"),
        ("mexico", "México"), ("méxico", "México"),
        ("uganda", "Uganda"),
        ("congo", "Congo"),
        ("ecuador", "Ecuador"),
        ("dominicana", "Dominican Republic"),
        ("jamaica", "Jamaica"),
        ("guinea", "Guinea"),
    ]

    for src_raw in (name, text):
        if not src_raw:
            continue
        src = src_raw.lower()
        for keyword, canonical in _ORIGIN_KEYWORDS:
            if keyword in src:
                return canonical
    return None


def normalize_roast(raw: str | None) -> str | None:
    """
    Normalize roast level to one of: Light, Medium-Light, Medium, Medium-Dark, Dark.
    """
    if not raw:
        return None

    lower = raw.lower()
    if any(w in lower for w in ["light", "claro", "clara"]):
        return "Light"
    if any(w in lower for w in ["medium-dark", "medio oscuro", "medio-oscuro"]):
        return "Medium-Dark"
    if any(w in lower for w in ["medium-light", "medio claro"]):
        return "Medium-Light"
    if any(w in lower for w in ["medium", "medio", "media"]):
        return "Medium"
    if any(w in lower for w in ["dark", "oscuro", "oscura"]):
        return "Dark"
    return None
