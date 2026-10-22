"""
TED API Client für die Suche nach öffentlichen Ausschreibungen.
Nutzt die TED Search API: https://docs.ted.europa.eu/api/latest/search.html
"""

import requests
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed


TED_API_BASE = "https://api.ted.europa.eu/v3/notices"

# form-type-Werte, die NICHT bietbar sind und ausgeschlossen werden:
#   result      → Vergabe-/Ergebnisbekanntmachung (Contract Award Notice)
#   dir-awa-pre → freiwillige Ex-ante-Transparenz (Direktvergabe-Ankündigung)
#   cont-modif  → Vertragsänderung
# Bietbare Typen (competition, planning/Vorinformation, change) bleiben erhalten.
EXCLUDED_FORM_TYPES = ("result", "dir-awa-pre", "cont-modif")
TED_EXCLUDE_AWARD_CLAUSE = "NOT (form-type IN (result dir-awa-pre cont-modif))"


def search_notices(
    query: str,
    country: str = "DEU",
    days_back: int = 30,
    contract_type: Optional[str] = None,
    limit: int = 20
) -> dict:
    """
    Sucht nach Vergabebekanntmachungen in der TED-Datenbank.
    
    Args:
        query: Suchbegriff (z.B. "Digitalisierung", "IT-Beratung")
        country: ISO 3166-1 Alpha-3 Ländercode (DEU für Deutschland)
        days_back: Anzahl der Tage in die Vergangenheit
        contract_type: Auftragsart (services, supplies, works)
        limit: Maximale Anzahl der Ergebnisse
    
    Returns:
        dict mit Suchergebnissen
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Berechne das Startdatum für die Suche
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
    
    # Query im TED Expert Search Format aufbauen
    query_parts = []
    
    # Volltext-Suche mit FT~ (Full Text)
    if query and query.strip():
        search_term = query.strip()
        # Ersetze problematische Sonderzeichen durch Leerzeichen
        for char in ['&', '|', '!', '(', ')', '[', ']', '{', '}', '^', '"', '~', '*', '?', ':', '\\', '/']:
            search_term = search_term.replace(char, ' ')
        # Splitte in einzelne Wörter und verbinde mit AND
        words = [w.strip() for w in search_term.split() if w.strip()]
        if words:
            # Jedes Wort einzeln mit FT~ prefixen und mit AND verbinden
            ft_parts = [f'FT~{word}' for word in words]
            query_parts.append(' AND '.join(ft_parts))
    
    # Land filtern (organisation-country-buyer ist das korrekte Feld)
    if country and country.strip():
        query_parts.append(f'organisation-country-buyer={country.strip()}')
    
    # Zeitraum filtern
    query_parts.append(f'publication-date>={start_date}')
    
    # Auftragsart filtern - verwende korrektes Feldformat
    if contract_type:
        type_mapping = {
            "services": "services",
            "supplies": "supplies", 
            "works": "works"
        }
        if contract_type in type_mapping:
            query_parts.append(f'contract-nature={type_mapping[contract_type]}')

    # Ergebnis-/Vergabebekanntmachungen ausschließen — nur bietbare Verfahren behalten
    query_parts.append(TED_EXCLUDE_AWARD_CLAUSE)

    # Kombiniere Query-Teile mit AND
    final_query = " AND ".join(query_parts)
    
    # Felder die abgerufen werden sollen (getestete, funktionierende Feldnamen)
    fields = [
        "publication-number",
        "notice-title",
        "title-proc",
        "description-proc",
        "organisation-country-buyer", 
        "publication-date",
        "deadline",
        "buyer-name",
        "organisation-name-buyer",
        "contract-nature",
        "form-type"
    ]
    
    payload = {
        "query": final_query,
        "fields": fields,
        "limit": min(limit, 100),  # API max ist 100
        "page": 1,
        "paginationMode": "PAGE_NUMBER"
    }
    
    try:
        response = requests.post(
            f"{TED_API_BASE}/search",
            json=payload,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        # Bei Fehler: Debug-Info zurückgeben
        error_detail = str(e)
        try:
            error_detail = response.text if response else str(e)
        except:
            pass
        return {"error": error_detail, "notices": []}


def parse_notices(api_response: dict) -> List[Dict]:
    """
    Parst die API-Antwort und extrahiert relevante Informationen.
    
    Args:
        api_response: Rohe API-Antwort
    
    Returns:
        Liste von Vergabe-Dictionaries mit standardisierten Feldern
    """
    if "error" in api_response:
        return []
    
    notices = []
    raw_notices = api_response.get("notices", [])
    
    for notice in raw_notices:
        # Notice ID - neues Feldformat
        notice_id = _get_field(notice, "publication-number")
        
        # Beschreibung extrahieren
        description = _extract_description(notice)
        
        # Extrahiere relevante Felder
        parsed = {
            "id": notice_id,
            "title": _extract_title(notice),
            "buyer": _extract_buyer(notice),
            "deadline": _extract_deadline(notice),
            "publication_date": _extract_publication_date(notice),
            "estimated_value": _extract_value(notice),
            "cpv_codes": _extract_cpv(notice),
            "description": description if description else _extract_title(notice),
            "url": _generate_ted_url(notice_id),
            "contract_type": _get_field(notice, "contract-nature"),
            "country": _get_field(notice, "organisation-country-buyer"),
            "form_type": _get_field(notice, "form-type"),
        }

        # Sicherheitsnetz: Award-/Ergebnis-Notices ausschließen, falls die API
        # den Query-Filter doch einmal umgeht.
        if parsed["form_type"] in EXCLUDED_FORM_TYPES:
            continue

        notices.append(parsed)
    
    return notices


def _get_field(notice: dict, field_name: str, default: str = "") -> str:
    """Holt ein Feld aus der Notice, unterstützt verschiedene Formate."""
    value = notice.get(field_name, default)
    
    if isinstance(value, list) and value:
        return str(value[0])
    elif isinstance(value, dict):
        # Bevorzuge Deutsch, dann Englisch
        return value.get("DEU", "") or value.get("deu", "") or \
               value.get("ENG", "") or value.get("eng", "") or \
               value.get("de", "") or value.get("en", "") or \
               next(iter(value.values()), default) if value else default
    elif isinstance(value, str):
        return value
    
    return default


def _extract_title(notice: dict) -> str:
    """Extrahiert den Titel der Vergabe (bevorzugt Deutsch)."""
    # Versuche verschiedene Feldnamen in Prioritätsreihenfolge
    for field_name in ["notice-title", "title-proc", "title-lot", "title"]:
        title = notice.get(field_name, {})
        
        if isinstance(title, dict) and title:
            # Bevorzuge Deutsch, dann Englisch
            result = title.get("DEU", "") or title.get("deu", "") or \
                     title.get("ENG", "") or title.get("eng", "") or \
                     title.get("de", "") or title.get("en", "") or \
                     next(iter(title.values()), "")
            if result:
                return result
        elif isinstance(title, str) and title:
            return title
        elif isinstance(title, list) and title:
            first = title[0]
            if isinstance(first, dict):
                result = first.get("DEU", "") or first.get("deu", "") or \
                       first.get("ENG", "") or first.get("eng", "") or \
                       next(iter(first.values()), "")
                if result:
                    return result
            elif first:
                return str(first)
    
    return "Kein Titel verfügbar"


def _extract_description(notice: dict) -> str:
    """Extrahiert die Beschreibung der Vergabe."""
    for field_name in ["description-proc", "description-lot"]:
        desc = notice.get(field_name, {})
        
        if isinstance(desc, dict) and desc:
            result = desc.get("DEU", "") or desc.get("deu", "") or \
                     desc.get("ENG", "") or desc.get("eng", "") or \
                     next(iter(desc.values()), "")
            if result:
                return result
        elif isinstance(desc, str) and desc:
            return desc
        elif isinstance(desc, list) and desc:
            first = desc[0]
            if isinstance(first, dict):
                result = first.get("DEU", "") or first.get("deu", "") or \
                       first.get("ENG", "") or first.get("eng", "") or \
                       next(iter(first.values()), "")
                if result:
                    return result
            elif first:
                return str(first)
    
    return ""


def _extract_buyer(notice: dict) -> str:
    """Extrahiert den Auftraggeber."""
    # Versuche verschiedene Buyer-Felder
    for field_name in ["buyer-name", "organisation-name-buyer"]:
        buyer = notice.get(field_name, "")
        
        if isinstance(buyer, list) and buyer:
            # Sammle alle Buyer-Namen
            names = []
            for item in buyer:
                if isinstance(item, dict):
                    name = item.get("DEU", "") or item.get("deu", "") or \
                           item.get("ENG", "") or item.get("eng", "") or \
                           next(iter(item.values()), "")
                    if name:
                        names.append(name)
                elif item:
                    names.append(str(item))
            if names:
                return names[0]  # Nimm den ersten Buyer
        elif isinstance(buyer, dict) and buyer:
            result = buyer.get("DEU", "") or buyer.get("deu", "") or \
                   buyer.get("ENG", "") or buyer.get("eng", "") or \
                   next(iter(buyer.values()), "")
            if result:
                return result
        elif isinstance(buyer, str) and buyer:
            return buyer
    
    # Fallback: Aus Titel extrahieren (Format: "Deutschland-Stadt: Beschreibung")
    title = _extract_title(notice)
    if "-" in title and ":" in title:
        parts = title.split(":")
        if parts:
            location_part = parts[0].strip()
            if "-" in location_part:
                country_city = location_part.split("-", 1)
                if len(country_city) == 2:
                    return f"{country_city[1].strip()}, {country_city[0].strip()}"
            return location_part
    
    return "Siehe TED-Link für Details"


def _extract_deadline(notice: dict) -> str:
    """Extrahiert die Abgabefrist."""
    # Versuche verschiedene Deadline-Felder
    deadline = notice.get("deadline", "") or notice.get("submission-deadline", "")
    
    if isinstance(deadline, list) and deadline:
        deadline = deadline[0]
    
    if isinstance(deadline, str) and deadline:
        # Format: "2026-01-19T00:00:00+01:00" → "19.01.2026"
        try:
            date_part = deadline.split("T")[0].split("+")[0]
            parts = date_part.split("-")
            if len(parts) == 3:
                return f"{parts[2]}.{parts[1]}.{parts[0]}"
        except (IndexError, ValueError):
            pass
        return deadline[:10] if len(deadline) >= 10 else deadline
    
    return "Keine Frist angegeben"


def _extract_publication_date(notice: dict) -> str:
    """Extrahiert das Veröffentlichungsdatum."""
    pub_date = notice.get("publication-date", "") or notice.get("PD", "")
    
    if isinstance(pub_date, list) and pub_date:
        pub_date = pub_date[0]
    
    if isinstance(pub_date, str) and pub_date:
        try:
            date_part = pub_date.split("T")[0].split("+")[0]
            parts = date_part.split("-")
            if len(parts) == 3:
                return f"{parts[2]}.{parts[1]}.{parts[0]}"
        except (IndexError, ValueError):
            pass
        return pub_date[:10] if len(pub_date) >= 10 else pub_date
    
    return "N/A"


def _extract_value(notice: dict) -> str:
    """Extrahiert den geschätzten Auftragswert."""
    value = notice.get("estimated-value", "") or notice.get("TVL", "") or notice.get("VA", "")
    
    if isinstance(value, list) and value:
        value = value[0]
    
    if isinstance(value, (int, float)):
        return f"{value:,.0f} EUR".replace(",", ".")
    elif isinstance(value, str) and value:
        try:
            num = float(value.replace(",", "."))
            return f"{num:,.0f} EUR".replace(",", ".")
        except ValueError:
            return value
    
    return "Nicht angegeben"


def _extract_cpv(notice: dict) -> List[str]:
    """Extrahiert CPV-Codes."""
    cpv = notice.get("cpv-code", []) or notice.get("CPV", [])
    
    if isinstance(cpv, str):
        return [cpv]
    elif isinstance(cpv, list):
        return [str(c) for c in cpv[:5]]
    
    return []


def _generate_ted_url(notice_id: str) -> str:
    """Generiert die URL zur Vergabe auf TED."""
    if notice_id:
        # Neues TED URL Format
        return f"https://ted.europa.eu/de/notice/-/detail/{notice_id}"
    return ""


def get_buyer_history(buyer_name: str, days_back: int = 365, limit: int = 10) -> dict:
    """
    Holt vergangene Ausschreibungen eines Auftraggebers der letzten 12 Monate.
    Gibt Anzahl und Titelübersicht zurück.

    Strategie: Aus dem Buyer-Namen werden die 2 längsten, spezifischsten Wörter
    extrahiert und als FT~-Query kombiniert. Generische Begriffe wie „Land",
    „Stadt", „vertreten" werden über eine erweiterte Stopword-Liste herausgefiltert,
    da sie in fast jeder TED-Bekanntmachung auftauchen und die Trefferzahl
    künstlich aufblähen würden.
    """
    if not buyer_name or buyer_name in ("Nicht angegeben", "Siehe TED-Link für Details"):
        return {}

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")

    # Erweiterte Stopwords: Funktionswörter + generische geografische/
    # organisatorische Begriffe, die in fast jeder deutschen TED-Bekanntmachung
    # vorkommen und deshalb keinen Filterwert haben.
    stopwords = {
        # Deutsche Funktionswörter
        "fuer", "und", "oder", "aber", "der", "die", "das", "des", "dem", "den",
        "von", "aus", "mit", "bei", "zur", "zum", "nach", "ueber", "unter",
        "neben", "seit", "gegen", "ohne", "durch", "beim", "beim",
        # Generische geografische / Verwaltungsbegriffe
        "land", "lands", "staat", "bund", "bundes",
        "stadt", "staedt", "staedte",
        "gemeinde", "gemeinden",
        "kreis", "kreise",
        "bezirk", "bezirke",
        "region", "regionen",
        "freistaat", "hansestadt",
        # Generische Org-/Verwaltungswörter
        "vertreten", "vertretung",
        "verwaltung", "behoerde", "behoerden",
        "ministerium", "senat", "amt", "amtes",
        "stelle", "stellen", "dienst",
        "oeffentlich", "zentrum",
    }

    clean_name = buyer_name.strip()

    # Umlaute transliterieren (nicht einfach löschen!), damit die Wörter
    # erhalten bleiben und korrekt gefiltert werden können.
    umlaut_map = {
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue',
        'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue',
        'ß': 'ss',
    }
    for uml, rep in umlaut_map.items():
        clean_name = clean_name.replace(uml, rep)

    # Sonderzeichen die TED-Query-Syntax stören durch Leerzeichen ersetzen
    for ch in ['&', '|', '!', '(', ')', '[', ']', '"', '~', '*', '?', ':', '\\', '/']:
        clean_name = clean_name.replace(ch, ' ')

    # Filter: mind. 7 Zeichen (kurze Wörter sind zu generisch), kein Stopword,
    # nur ASCII (TED-API verarbeitet Umlaute im Query unzuverlässig)
    words = [
        w for w in clean_name.split()
        if len(w) >= 7 and w.lower() not in stopwords and w.isascii()
    ]

    if not words:
        # Fallback auf 5-Zeichen-Minimum, falls kein langes Wort übrig bleibt
        words = [
            w for w in clean_name.split()
            if len(w) >= 5 and w.lower() not in stopwords and w.isascii()
        ]

    if not words:
        return {}

    # Längste Wörter zuerst — sie sind am spezifischsten / eindeutigsten
    words.sort(key=len, reverse=True)

    # Maximal 2 Schlüsselwörter verwenden: mehr erhöht die Präzision kaum,
    # erhöht aber das Risiko, echte Treffer auszuschließen.
    key_words = words[:2]
    ft_parts = [f'FT~{w}' for w in key_words]
    query = " AND ".join(ft_parts) + f" AND publication-date>={start_date}"

    payload = {
        "query": query,
        "fields": ["publication-number", "notice-title", "buyer-name",
                   "organisation-name-buyer", "publication-date"],
        "limit": limit,
        "page": 1,
        "paginationMode": "PAGE_NUMBER",
    }

    try:
        response = requests.post(
            f"{TED_API_BASE}/search",
            json=payload,
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return {}

    total = data.get("totalNoticeCount", 0) or data.get("total", 0)
    notices = data.get("notices", [])

    titles = []
    for n in notices:
        title = _extract_title(n)
        if title and title != "Kein Titel verfügbar":
            titles.append(title)

    return {
        "total": total,
        "recent_titles": titles[:3],
    }


def get_notice_details(notice_id: str) -> dict:
    """
    Ruft detaillierte Informationen zu einer spezifischen Vergabe ab.
    
    Args:
        notice_id: Die TED Notice ID
    
    Returns:
        Detaillierte Vergabeinformationen
    """
    headers = {
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(
            f"{TED_API_BASE}/{notice_id}",
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def get_total_count(api_response: dict) -> int:
    """Gibt die Gesamtzahl der gefundenen Notices zurück."""
    # Neue API verwendet "totalNoticeCount" oder "total"
    return api_response.get("totalNoticeCount", 0) or api_response.get("total", 0)


def search_notices_multi_keywords(
    keyword_groups: Dict[str, list],
    country: str = "DEU",
    days_back: int = 30,
    contract_type: Optional[str] = None,
    limit_per_group: int = 10,
) -> List[Dict]:
    """
    Parallele TED-Suche für mehrere Keyword-Gruppen (z.B. ServiceNow-Module).
    Jede Gruppe bekommt das primäre Keyword (erstes in der Liste) als TED-Suchbegriff.
    Ergebnisse werden dedupliziert und mit dem Modul-Tag versehen.

    Args:
        keyword_groups: {"ITSM": ["Ticketsystem", "Service Desk", ...], "GRC": [...]}
        country: ISO 3166-1 Alpha-3 Ländercode
        days_back: Suchzeitraum in Tagen
        contract_type: Auftragsart-Filter
        limit_per_group: Max. Ergebnisse pro Keyword-Gruppe

    Returns:
        Deduplizierte Liste von Notices mit "sn_module"-Tag
    """
    tasks = []
    for module, keywords in keyword_groups.items():
        for kw in keywords[:2]:  # Max 2 Keywords pro Modul für TED (primär + alternativ)
            tasks.append((module, kw))

    all_results: List[Dict] = []
    seen_ids: set = set()

    def _fetch(module: str, keyword: str) -> List[Dict]:
        raw = search_notices(
            query=keyword,
            country=country,
            days_back=days_back,
            contract_type=contract_type,
            limit=limit_per_group,
        )
        notices = parse_notices(raw)
        for n in notices:
            n["source"] = "TED"
            if "sn_module" not in n:
                n["sn_module"] = module
        return notices

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch, module, kw): (module, kw) for module, kw in tasks}
        for future in as_completed(futures):
            try:
                for notice in future.result():
                    if notice["id"] and notice["id"] not in seen_ids:
                        seen_ids.add(notice["id"])
                        all_results.append(notice)
            except Exception:
                pass

    return all_results
