"""
Hilfsfunktionen: Duplikat-Erkennung, CPV-Mapping.
"""

import re
from typing import List, Dict


# CPV-Codes → lesbare Themen (relevante IT/Beratungs-Codes)
CPV_THEME_MAP = {
    "72": "IT-Dienstleistungen",
    "7200": "IT-Dienstleistungen",
    "72200": "Software",
    "72210": "Programmierung",
    "72220": "IT-Beratung",
    "72230": "Softwareentwicklung",
    "72240": "Systemanalyse",
    "72250": "System- & Support",
    "72260": "Software-Support",
    "72300": "Datenverarbeitung",
    "72310": "Datenverarbeitung",
    "72320": "Datenbankdienste",
    "72400": "Internet",
    "72500": "IT-Betrieb",
    "72510": "IT-Management",
    "72512": "Dokumentenmanagement",
    "72514": "Rechenzentrumsbetrieb",
    "72540": "Systemaktualisierung",
    "72600": "IT-Support",
    "72610": "IT-Support",
    "72700": "Netzwerk",
    "72710": "Netzwerk",
    "72720": "Wide Area Network",
    "72800": "Audit & Testing",
    "72900": "Backup & Katalog",
    "48": "Softwarepaket",
    "4800": "Softwarepaket",
    "48100": "Branchensoftware",
    "48200": "Netzwerksoftware",
    "48300": "Dokumentensoftware",
    "48400": "Transaktionssoftware",
    "48500": "Kommunikationssoftware",
    "48600": "Datenbank",
    "48700": "Softwaretools",
    "48800": "Informationssysteme",
    "48900": "Diverses",
    "73": "Forschung & Entwicklung",
    "79": "Unternehmensberatung",
    "7900": "Unternehmensberatung",
    "79400": "Unternehmensberatung",
    "79410": "Unternehmensberatung",
    "79420": "Projektmanagement",
    "79600": "Personaldienstleistungen",
    "80": "Bildung & Training",
    "8000": "Bildung & Training",
    "80500": "Schulung",
    "80510": "IT-Schulung",
    "80530": "Berufsausbildung",
}


def cpv_to_theme(cpv_code: str) -> str:
    """Mapped einen CPV-Code auf ein lesbares Thema."""
    code = re.sub(r"[^0-9]", "", str(cpv_code))
    for length in (5, 4, 3, 2):
        prefix = code[:length]
        if prefix in CPV_THEME_MAP:
            return CPV_THEME_MAP[prefix]
    return ""


def _normalize_title(title: str) -> str:
    """Normalisiert einen Titel für den Duplikat-Vergleich."""
    t = title.lower()
    t = re.sub(r"[^a-zäöüß0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    stopwords = {"für", "der", "die", "das", "von", "und", "in", "im", "zu", "mit", "auf", "des"}
    words = [w for w in t.split() if w not in stopwords and len(w) > 2]
    return " ".join(sorted(words))


def _title_similarity(a: str, b: str) -> float:
    """Einfache Token-Overlap-Ähnlichkeit zwischen zwei normalisierten Titeln."""
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def deduplicate_notices(notices: List[Dict], threshold: float = 0.7) -> List[Dict]:
    """
    Entfernt Duplikate aus einer gemischten TED+BKMS-Ergebnisliste.
    Bei Ähnlichkeit >= threshold wird das Duplikat entfernt und die Quellen zusammengeführt.
    """
    if not notices:
        return []

    normalized = [_normalize_title(n.get("title", "")) for n in notices]
    kept_indices = []
    merged_sources: Dict[int, List[str]] = {}

    for i, norm_i in enumerate(normalized):
        duplicate_of = None
        for j in kept_indices:
            sim = _title_similarity(norm_i, normalized[j])
            if sim >= threshold:
                duplicate_of = j
                break
        if duplicate_of is None:
            kept_indices.append(i)
            merged_sources[i] = [notices[i].get("source", "")]
        else:
            src = notices[i].get("source", "")
            if src and src not in merged_sources[duplicate_of]:
                merged_sources[duplicate_of].append(src)

    result = []
    for idx in kept_indices:
        notice = dict(notices[idx])
        sources = merged_sources[idx]
        if len(sources) > 1:
            notice["source"] = "+".join(sources)
            notice["duplicate_hint"] = f"Auch in {' & '.join(sources[1:])} gefunden"
        result.append(notice)

    return result
