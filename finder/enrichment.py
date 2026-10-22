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
            text = _ted_full_text(notice_id)
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
