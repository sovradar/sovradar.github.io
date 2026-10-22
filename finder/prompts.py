"""Prompts und Antwortschemata des SovRadar-Ausschreibungsfinders.

Die ServiceNow-Logik (Vorbewertung, Score-Regeln, Tiefenanalyse) ist aus dem bisherigen
Vergabe-Finder übernommen (servicenow_config.py: SN_SCORING_PROMPT, SONNET_DEEP_DIVE_PROMPT);
neu ist die Souveränitätsebene in der Tiefenanalyse.
"""

# Erkennbare Souveränitätsanforderungen (fester Code-Satz, die App ordnet sie SovRadar-Kriterien zu)
SOV_CODES = {
    "SOUVERAEN_EXPLIZIT": "Fordert ausdrücklich eine souveräne Cloud bzw. digitale Souveränität",
    "C5": "BSI-C5-Testat gefordert",
    "VSNFD": "Verarbeitung von Verschlusssachen (VS-NfD oder höher)",
    "ISO27001": "ISO 27001 bzw. IT-Grundschutz gefordert",
    "DATEN_DE_EU": "Datenhaltung/-verarbeitung nur in Deutschland bzw. der EU",
    "KEIN_DRITTSTAAT": "Kein Zugriff durch Drittstaaten (z. B. CLOUD Act), Anbieter unter EU-Recht",
    "EU_PERSONAL": "Betrieb/Support nur durch Personal in DE/EU bzw. sicherheitsüberprüft",
    "SCHLUESSEL_KUNDE": "Schlüsselhoheit beim Auftraggeber (BYOK/HYOK)",
    "ON_PREM": "Betrieb im Rechenzentrum des Auftraggebers / On-Premises",
    "OPEN_SOURCE": "Open Source oder offene Standards gefordert/bevorzugt",
    "DVC": "Deutsche Verwaltungscloud bzw. Bezug über öffentliche IT-Dienstleister/Rahmenverträge",
}

SCORING_PROMPT = """Du bist ein erfahrener Lösungsarchitekt für ServiceNow. Du kennst alle Plattformfunktionen im Detail.
Unser Beratungshaus ist spezialisiert auf folgende Module:

{modules}

Vergaben (gefunden durch ServiceNow-Modul-Keyword-Suche):
{notices}

Bewerte jede Vergabe darauf, wie gut sich die Anforderungen standardnah mit ServiceNow lösen lassen.

Score-Regeln (ServiceNow-spezifisch):
- 80–100 "Must pursue": Klarer Software-/Plattformbedarf, passt zu unseren ServiceNow-Modulen, produktoffen, realistisches Budget
- 60–79 "Optional": Interessant aber unklar, z. B. nur Beratungsleistung, Produkt könnte feststehen, oder marginaler Modul-Fit
- 0–59 "Skip": Kein Software-Bedarf, anderes Produkt bereits festgelegt, oder keines der Module passt sinnvoll

recommended_modules: Module aus der Liste oben, die am besten passen (leer, wenn nichts passt).
title: kurzer, sprechender Titel (5–8 Wörter), falls der vorhandene Titel nichtssagend ist, sonst der vorhandene Titel.
summary: 1–2 Sätze zur strategischen Begründung.
Gib für jede Vergabe genau ein Ergebnis zurück; "id" ist die ID aus der Liste."""

SCORING_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "integer"},
                    "label": {"type": "string", "enum": ["Must pursue", "Optional", "Skip"]},
                    "recommended_modules": {"type": "array", "items": {"type": "string"}},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["id", "score", "label", "recommended_modules", "title", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

DEEP_PROMPT = """Analysiere die folgende Ausschreibung aus Sicht eines Beratungshauses, das vor allem ServiceNow-Einführungs- und Umsetzungsdienstleistungen anbietet.

Ziel 1: ultra-knappe Erstbewertung zur Bid-Entscheidung. Erkenne schnell, ob sich eine weitere Beschäftigung lohnt oder ob die Ausschreibung verworfen werden sollte. Bewerte zusätzlich, ob sie für ein auch über ServiceNow aufgestelltes Beratungshaus strategisch interessant sein könnte, auch wenn sie kein klarer ServiceNow-Deal ist.

Ziel 2 (Souveränitätsebene): Prüfe, ob die Ausschreibung Anforderungen an digitale Souveränität bzw. souveränen Cloud-Betrieb stellt. Erfasse nur, was im Text steht oder eindeutig daraus folgt, nichts Vermutetes. Erlaubte Codes:
{codes}
relevanz: "hoch" = ausdrücklich gefordert und für den Zuschlag zentral; "mittel" = mehrere konkrete Anforderungen; "gering" = einzelne Hinweise (z. B. nur DSGVO/Datenhaltung EU); "keine" = nichts erkennbar.
belege: je erkanntem Code ein kurzes Zitat (höchstens 25 Wörter) aus dem Text.
servicenow_folge: 1 Satz, was das für einen ServiceNow-Betrieb bedeutet (z. B. Standard-SaaS ausgeschlossen, souveräner Partnerbetrieb oder Self-Hosting nötig, VS-NfD nur IaaS).

Regeln:
- Nur Stichpunkte, keine langen Fließtexte, so kurz wie möglich, aber verständlich.
- Bei NO-GO klar und hart begründen.
- Keine theoretischen ServiceNow-Möglichkeiten nennen, wenn sie im Vergabekontext nicht realistisch positionierbar sind.
- ServiceNow kann durch Custom Apps erweitert werden; bewerte kritisch, ob das mit realistischem Aufwand sinnvoll ist.

Ausschreibung:
{notice}"""

DEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "kurzbeschreibung": {"type": "string"},
        "fachlicher_fit": {"type": "string", "enum": ["hoch", "mittel", "gering"]},
        "fachlicher_fit_begruendung": {"type": "string"},
        "relevante_module": {"type": "array", "items": {"type": "string"}},
        "custom_app_bedarf": {"type": "string", "enum": ["nein", "gering", "mittel", "hoch"]},
        "custom_app_begruendung": {"type": "string"},
        "rolle_servicenow": {"type": "string", "enum": ["Kernsystem", "Ergänzung", "irrelevant"]},
        "haupthemmnisse": {"type": "array", "items": {"type": "string"}},
        "gesamtbewertung": {"type": "string", "enum": ["GO", "SELECTIVE", "NO-GO"]},
        "gesamtbewertung_begruendung": {"type": "string"},
        "souveraenitaet": {
            "type": "object",
            "properties": {
                "relevanz": {"type": "string", "enum": ["hoch", "mittel", "gering", "keine"]},
                "anforderungen": {"type": "array", "items": {"type": "string", "enum": list(SOV_CODES)}},
                "belege": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"code": {"type": "string", "enum": list(SOV_CODES)}, "zitat": {"type": "string"}},
                        "required": ["code", "zitat"],
                        "additionalProperties": False,
                    },
                },
                "servicenow_folge": {"type": "string"},
            },
            "required": ["relevanz", "anforderungen", "belege", "servicenow_folge"],
            "additionalProperties": False,
        },
    },
    "required": ["kurzbeschreibung", "fachlicher_fit", "fachlicher_fit_begruendung", "relevante_module", "custom_app_bedarf",
                 "custom_app_begruendung", "rolle_servicenow", "haupthemmnisse", "gesamtbewertung",
                 "gesamtbewertung_begruendung", "souveraenitaet"],
    "additionalProperties": False,
}
