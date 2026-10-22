"""
Bekanntmachungsservice (BKMS) API Client
Deutscher nationaler Vergabeservice – inkl. Unterschwellenvergaben.
Nutzt die OpenData-Schnittstelle: https://oeffentlichevergabe.de/api/notice-exports
"""

import io
import csv
import zipfile
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict


BKMS_EXPORT_URL = "https://oeffentlichevergabe.de/api/notice-exports"
BKMS_NOTICE_URL = "https://www.oeffentlichevergabe.de/ui/de/notices"

# formType-Werte, die NICHT bietbar sind und ausgeschlossen werden
# (Vergabe-/Ergebnisbekanntmachung, Direktvergabe-Ankündigung, Vertragsänderung).
# Identische eForms-Kodierung wie bei TED.
EXCLUDED_FORM_TYPES = ("result", "dir-awa-pre", "cont-modif")

# Auftragsart-Mapping (BKMS mainNature → TED-kompatibel)
CONTRACT_TYPE_MAP = {
    "services": "services",
    "supplies": "supplies",
    "works": "works",
}


def _download_day(day: str) -> Optional[Dict[str, list]]:
    """Lädt CSV-ZIP für einen Tag (YYYY-MM-DD) und gibt geparste Tabellen zurück."""
    try:
        response = requests.get(
            BKMS_EXPORT_URL,
            params={"pubDay": day, "format": "csv.zip"},
            timeout=20,
        )
        if response.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            tables = {}
            for name in zf.namelist():
                key = name.replace(".csv", "")
                with zf.open(name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                    tables[key] = list(reader)
        return tables
    except Exception:
        return None


def search_notices_bkms(
    query: str,
    country: str = "DEU",
    days_back: int = 30,
    contract_type: Optional[str] = None,
    limit: int = 20,
) -> List[Dict]:
    """
    Sucht Vergabebekanntmachungen im BKMS per Keyword-Suche.
    Lädt Tages-ZIPs parallel herunter und filtert lokal.
    """
    keywords = [w.strip().lower() for w in query.split() if w.strip()]
    if not keywords:
        return []

    today = datetime.now().date()
    days = [
        (today - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        for i in range(min(days_back, 60))
    ]

    # Parallele Downloads
    all_days_tables = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_download_day, day): day for day in days}
        for future in as_completed(futures):
            result = future.result()
            if result:
                all_days_tables.append(result)

    matches = []

    for tables in all_days_tables:
        notice_map = {
            (r["noticeIdentifier"], r["noticeVersion"]): r
            for r in tables.get("notice", [])
        }
        # Buyer-Lookup: erste Buyer-Organisation pro Notice
        buyer_map = {}
        for r in tables.get("organisation", []):
            if r.get("organisationRole") != "buyer":
                continue
            key = (r["noticeIdentifier"], r["noticeVersion"])
            if key not in buyer_map:
                buyer_map[key] = r

        for row in tables.get("purpose", []):
            key = (row["noticeIdentifier"], row["noticeVersion"])
            notice = notice_map.get(key, {})

            # Ergebnis-/Vergabebekanntmachungen überspringen (nicht bietbar)
            if notice.get("formType") in EXCLUDED_FORM_TYPES:
                continue

            # Länderfilter über Buyer-Organisation
            buyer = buyer_map.get(key, {})
            if country and buyer.get("organisationCountryCode", "") != country:
                continue

            # Auftragsart-Filter
            if contract_type and row.get("mainNature", "").lower() != contract_type.lower():
                continue

            title = row.get("title", "")
            description = row.get("description", "")
            searchable = (title + " " + description).lower()

            if not all(kw in searchable for kw in keywords):
                continue

            notice_id = row["noticeIdentifier"]
            pub_date_raw = notice.get("publicationDate", "")
            pub_date = _format_date(pub_date_raw)

            value_raw = row.get("estimatedValue", "")
            value_currency = row.get("estimatedValueCurrency", "EUR")
            estimated_value = _format_value(value_raw, value_currency)

            # CPV-Codes aus classification.csv
            cpv_codes = []
            for cls_row in tables.get("classification", []):
                if (cls_row.get("noticeIdentifier") == notice_id and
                        cls_row.get("classificationType", "").lower() == "cpv"):
                    code = cls_row.get("mainClassificationCode", "")
                    if code:
                        cpv_codes.append(code)

            matches.append({
                "id": notice_id,
                "title": title or "Kein Titel verfügbar",
                "buyer": buyer.get("organisationName", "Nicht angegeben"),
                "deadline": "Nicht verfügbar",
                "publication_date": pub_date,
                "estimated_value": estimated_value,
                "cpv_codes": cpv_codes,
                "description": description,
                "url": f"{BKMS_NOTICE_URL}/{notice_id}",
                "contract_type": row.get("mainNature", ""),
                "country": buyer.get("organisationCountryCode", country),
                "source": "BKMS",
            })

            if len(matches) >= limit:
                break

        if len(matches) >= limit:
            break

    return matches[:limit]


def search_notices_bkms_multi(
    keyword_groups: Dict[str, list],
    country: str = "DEU",
    days_back: int = 30,
    contract_type: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """
    Lädt BKMS-ZIPs einmalig herunter und filtert für alle Keyword-Gruppen gleichzeitig.
    Effizienter als search_notices_bkms mehrfach aufzurufen.

    Args:
        keyword_groups: {"ITSM": ["Ticketsystem", "Service Desk"], "GRC": [...]}
        country: Länderfilter
        days_back: Suchzeitraum in Tagen
        contract_type: Auftragsart-Filter
        limit: Max. Gesamtergebnisse

    Returns:
        Notices mit "sn_module"-Tag (erstes treffendes Modul)
    """
    module_patterns: Dict[str, list] = {
        module: [kw.lower() for kw in keywords]
        for module, keywords in keyword_groups.items()
    }

    today = datetime.now().date()
    days = [
        (today - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        for i in range(min(days_back, 60))
    ]

    all_days_tables = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_download_day, day): day for day in days}
        for future in as_completed(futures):
            result = future.result()
            if result:
                all_days_tables.append(result)

    matches = []
    seen_ids: set = set()

    for tables in all_days_tables:
        notice_map = {
            (r["noticeIdentifier"], r["noticeVersion"]): r
            for r in tables.get("notice", [])
        }
        buyer_map = {}
        for r in tables.get("organisation", []):
            if r.get("organisationRole") != "buyer":
                continue
            key = (r["noticeIdentifier"], r["noticeVersion"])
            if key not in buyer_map:
                buyer_map[key] = r

        for row in tables.get("purpose", []):
            key = (row["noticeIdentifier"], row["noticeVersion"])
            notice = notice_map.get(key, {})
            buyer = buyer_map.get(key, {})

            # Ergebnis-/Vergabebekanntmachungen überspringen (nicht bietbar)
            if notice.get("formType") in EXCLUDED_FORM_TYPES:
                continue

            if country and buyer.get("organisationCountryCode", "") != country:
                continue
            if contract_type and row.get("mainNature", "").lower() != contract_type.lower():
                continue

            title = row.get("title", "")
            description = row.get("description", "")
            searchable = (title + " " + description).lower()

            matched_module = None
            for module, kw_list in module_patterns.items():
                if any(kw in searchable for kw in kw_list):
                    matched_module = module
                    break

            if not matched_module:
                continue

            notice_id = row["noticeIdentifier"]
            if notice_id in seen_ids:
                continue
            seen_ids.add(notice_id)

            pub_date = _format_date(notice.get("publicationDate", ""))
            estimated_value = _format_value(
                row.get("estimatedValue", ""),
                row.get("estimatedValueCurrency", "EUR"),
            )

            cpv_codes = []
            for cls_row in tables.get("classification", []):
                if (
                    cls_row.get("noticeIdentifier") == notice_id
                    and cls_row.get("classificationType", "").lower() == "cpv"
                ):
                    code = cls_row.get("mainClassificationCode", "")
                    if code:
                        cpv_codes.append(code)

            matches.append({
                "id": notice_id,
                "title": title or "Kein Titel verfügbar",
                "buyer": buyer.get("organisationName", "Nicht angegeben"),
                "deadline": "Nicht verfügbar",
                "publication_date": pub_date,
                "estimated_value": estimated_value,
                "cpv_codes": cpv_codes,
                "description": description,
                "url": f"{BKMS_NOTICE_URL}/{notice_id}",
                "contract_type": row.get("mainNature", ""),
                "country": buyer.get("organisationCountryCode", country),
                "source": "BKMS",
                "sn_module": matched_module,
            })

            if len(matches) >= limit:
                break

        if len(matches) >= limit:
            break

    return matches[:limit]


def _format_date(raw: str) -> str:
    if not raw:
        return "N/A"
    try:
        date_part = raw.split("T")[0]
        parts = date_part.split("-")
        if len(parts) == 3:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
    except Exception:
        pass
    return raw[:10] if len(raw) >= 10 else raw


def _format_value(raw: str, currency: str = "EUR") -> str:
    if not raw:
        return "Nicht angegeben"
    try:
        num = float(raw)
        return f"{num:,.0f} {currency}".replace(",", ".")
    except (ValueError, TypeError):
        return raw
