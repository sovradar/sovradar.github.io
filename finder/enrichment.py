"""
Volltext-Anreicherung für die Sonnet-Neubewertung (Stufe A).

Holt für eine bereits gespeicherte Vergabe den vollständigen, öffentlich verfügbaren
Bekanntmachungstext nach – also alles, was die gerenderte Bekanntmachung (PDF) der
jeweiligen Quelle zeigt: volle Beschreibung, alle Lose, Eignungs- und
Zuschlagskriterien inkl. Gewichtung, Fristen, Verfahrensart, Ausschlussgründe usw.

Ansatz: Wir laden die **gerenderte PDF-Bekanntmachung** der Notice und extrahieren den
Text mit pymupdf (fitz). Die PDF-Rendition enthält – anders als die abgeflachten
OCDS-/Such-API-Felder – die menschenlesbaren Labels, aufgelösten Codes und formatierten
Werte und ist damit die vollständigste, LLM-freundlichste Quelle.

Wichtig: Das wird bewusst NUR bei der manuellen Neubewertung aufgerufen, nicht im
ersten Such-/Scoring-Durchlauf (Token-/Laufzeit-Kosten).

Die eigentlichen Vergabeunterlagen (externe Plattform-PDFs) werden hier NICHT geladen –
deren Link ist aber im Bekanntmachungstext enthalten.
"""

import re
import requests
import pymupdf as fitz  # PyMuPDF (fitz-Alias ist veraltet)
from typing import Dict, Optional

# Maximale Länge des angereicherten Textes (Token-Schutz für den Sonnet-Prompt).
# Großzügig gewählt, da die entscheidungsrelevanten Kriterien oft erst am Ende der
# Bekanntmachung stehen (z.B. Zuschlags-/Eignungskriterien in Abschnitt 5.1.9 ff.).
MAX_TEXT_CHARS = 40000

TED_API_BASE = "https://api.ted.europa.eu/v3/notices"
BKMS_NOTICE_API = "https://oeffentlichevergabe.de/api/notices"


def _pdf_to_text(content: bytes) -> str:
    """Extrahiert Text aus PDF-Bytes (pymupdf). Gibt "" bei Fehler zurück."""
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text.strip()
    except Exception:
        return ""


def _fetch_pdf_text(url: str) -> str:
    """Lädt eine PDF-URL und gibt den extrahierten Text zurück (fehlertolerant)."""
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            return ""
        return _pdf_to_text(r.content)
    except Exception:
        return ""


def _ted_full_text(notice_id: str) -> str:
    """Holt für eine TED-Notice den vollen Text aus der deutschen PDF-Rendition."""
    # Stabiles URL-Muster der TED-PDF-Rendition (aus dem links-Objekt der Such-API).
    return _fetch_pdf_text(f"https://ted.europa.eu/de/notice/{notice_id}/pdf")


# Felder der offiziellen TED-Such-API (eForms) für die Tiefenanalyse, mit deutscher Bezeichnung.
# Ersatz für die PDF-Rendition: ted.europa.eu beantwortet Abrufe von GitHub-Servern mit einer
# Bot-Prüfung (HTTP 202, leere Seite), die API dagegen normal.
TED_API_PREFIX = "Strukturierte Angaben aus der TED-API (eForms-Felder):"
TED_DETAIL_FIELDS = {
    "notice-title": "Titel",
    "procedure-type": "Verfahrensart",
    "description-proc": "Beschreibung des Verfahrens",
    "title-lot": "Titel der Lose",
    "description-lot": "Beschreibung der Lose",
    "estimated-value-proc": "Geschätzter Gesamtwert (EUR)",
    "estimated-value-lot": "Geschätzter Wert je Los (EUR)",
    "framework-estimated-value": "Geschätzter Wert der Rahmenvereinbarung (EUR)",
    "framework-maximum-value-lot": "Höchstwert der Rahmenvereinbarung je Los (EUR)",
    "framework-agreement-lot": "Rahmenvereinbarung",
    "framework-maximum-participants-number-lot": "Höchstzahl der Rahmenvertragspartner",
    "contract-duration-period-lot": "Laufzeit je Los",
    "duration-additional-information-lot": "Weitere Angaben zur Laufzeit",
    "renewal-maximum-lot": "Höchstzahl Verlängerungen",
    "renewal-description-lot": "Verlängerungen",
    "option-description-lot": "Optionen",
    "recurrence-description-lot": "Wiederkehrender Auftrag",
    "award-criterion-description-lot": "Zuschlagskriterien (Beschreibung)",
    "term-performance-lot": "Bedingungen für die Auftragsausführung",
    "contract-conditions-description-lot": "Vertragsbedingungen",
    "terms-financial-lot": "Finanzielle Bedingungen / Zahlung",
    "csecurity-clearance-description-lot": "Sicherheitsüberprüfung",
    "selection-criterion-description-lot": "Eignungskriterien",
    "additional-information-lot": "Zusätzliche Informationen je Los",
    "additional-information": "Zusätzliche Informationen",
}
TED_CRITERIA_FIELDS = ["award-criterion-type-lot", "award-criterion-name-lot", "award-criterion-number-lot"]
# eForms-Codes, die sonst unverständlich im Text stünden
TED_CODES = {
    "none": "keine", "fa-w-rc": "Rahmenvereinbarung mit erneutem Aufruf zum Wettbewerb",
    "fa-wo-rc": "Rahmenvereinbarung ohne erneuten Aufruf zum Wettbewerb", "fa-mix": "Rahmenvereinbarung, teils mit erneutem Wettbewerb",
    "open": "offenes Verfahren", "restricted": "nicht offenes Verfahren", "neg-w-call": "Verhandlungsverfahren mit Teilnahmewettbewerb",
    "neg-wo-call": "Verhandlungsverfahren ohne Teilnahmewettbewerb", "comp-dial": "wettbewerblicher Dialog", "innovation": "Innovationspartnerschaft",
    "price": "Preis", "quality": "Qualität", "cost": "Kosten", "YEAR": "Jahre", "MONTH": "Monate", "DAY": "Tage",
}


def _lang(v) -> list:
    """eForms-Werte vereinheitlichen: mehrsprachige Felder auf Deutsch (sonst Englisch/erste Sprache), immer als Liste."""
    if isinstance(v, dict):
        if "unit" in v or "value" in v:
            return [f"{v.get('value', '')} {v.get('unit', '')}".strip()]
        v = v.get("deu") or v.get("DEU") or v.get("eng") or v.get("ENG") or next(iter(v.values()), [])
    if not isinstance(v, list):
        v = [v]
    out = []
    for x in v:
        out += _lang(x) if isinstance(x, (dict, list)) else [str(x)]
    return [" ".join(TED_CODES.get(w, w) for w in x.split(" ")) if len(x) < 40 else x for x in out if x and x != "None"]


def _ted_api_text(notice_id: str) -> str:
    """Baut aus den Detailfeldern der TED-API einen lesbaren Text (fehlertolerant, "" bei Fehler)."""
    try:
        r = requests.post(f"{TED_API_BASE}/search", timeout=30, json={
            "query": f"publication-number={notice_id}", "limit": 1,
            "fields": list(TED_DETAIL_FIELDS) + TED_CRITERIA_FIELDS})
        if r.status_code != 200 or not r.json().get("notices"):
            return ""
        n = r.json()["notices"][0]
    except Exception:
        return ""
    lines = [TED_API_PREFIX]
    for key, label in TED_DETAIL_FIELDS.items():
        vals = _lang(n.get(key))
        if vals:
            lines.append(f"{label}: " + " | ".join(dict.fromkeys(vals)))
    # Zuschlagskriterien mit Gewichtung zusammenführen (Typ, Name, Gewicht stehen in parallelen Listen)
    typ, name, num = (_lang(n.get(f)) for f in TED_CRITERIA_FIELDS)
    if typ or name:
        crit = [f"{name[i] if i < len(name) else typ[i]} ({typ[i] if i < len(typ) else '?'}): {num[i] if i < len(num) else '?'}"
                for i in range(max(len(typ), len(name)))]
        lines.append("Zuschlagskriterien mit Gewichtung: " + "; ".join(crit))
    return "\n".join(lines) if len(lines) > 2 else ""


def _bkms_full_text(notice_id: str) -> str:
    """Holt für eine BKMS-Notice den vollen Text aus der PDF-Bekanntmachung (per ID)."""
    return _fetch_pdf_text(f"{BKMS_NOTICE_API}/{notice_id}?format=pdf")


def get_full_notice_text(notice: Dict) -> str:
    """
    Dispatcher: ruft den vollen Bekanntmachungstext je nach Quelle ab.
    Gibt "" zurück, wenn nichts ermittelt werden konnte (fehlertolerant).
    """
    if not notice:
        return ""
    source = (notice.get("source") or "").upper()
    notice_id = notice.get("id") or ""
    if not notice_id:
        return ""

    text = ""
    try:
        # source kann "TED", "BKMS" oder kombiniert "TED+BKMS" sein.
        if "TED" in source:
            text = _ted_full_text(notice_id) or _ted_api_text(notice_id)
        if not text and "BKMS" in source:
            text = _bkms_full_text(notice_id)
    except Exception:
        return ""

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + " …[gekürzt]"
    return text


def _extract_deadline(text: str) -> Optional[str]:
    """Zieht die Abgabe-/Teilnahmefrist aus dem Bekanntmachungstext (TT.MM.JJJJ).

    Das eForms-Label ist bei TED und BKMS konsistent:
    "Frist für den Eingang der Teilnahmeanträge/Angebote: TT/MM/JJJJ …".
    """
    m = re.search(
        r"Frist für den Eingang der (?:Teilnahmeanträge|Angebote)\s*:\s*(\d{2})/(\d{2})/(\d{4})",
        text,
    )
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return None


def _extract_value(text: str) -> Optional[str]:
    """Zieht den Auftragswert bzw. das Volumen aus dem Bekanntmachungstext.

    Priorisiert einen EUR-Wert; fällt sonst auf eine Personentage-Höchstmenge zurück
    (viele IT-Rahmenverträge geben kein EUR-Budget, sondern ein PT-Kontingent an).
    """
    t = re.sub(r"\s+", " ", text)
    m = re.search(r"Gesch[äa]tzter (?:Gesamt)?[Ww]ert(?:[^:]*)?:\s*([\d.,]+)\s*(?:EUR|€)", t)
    if m:
        return f"{m.group(1)} EUR"
    m = re.search(r"H[öo]chstwert[^:]*:\s*([\d.,]+)\s*(?:EUR|€)", t)
    if m:
        return f"{m.group(1)} EUR (Höchstwert)"
    m = re.search(r"H[öo]chstmenge[^:]*:\s*([\d.]+)\s*(?:Personentage|PT)", t)
    if m:
        return f"{m.group(1)} Personentage (Höchstmenge)"
    return None


def extract_notice_fields(text: str) -> Dict[str, Optional[str]]:
    """Extrahiert strukturierte Stammdaten (Frist, Volumen) aus dem Volltext."""
    if not text:
        return {"deadline": None, "estimated_value": None}
    return {
        "deadline": _extract_deadline(text),
        "estimated_value": _extract_value(text),
    }
