"""Prompts und Antwortschemata des SovRadar-Ausschreibungsfinders.

Die ServiceNow-Logik (Vorbewertung, Score-Regeln, Tiefenanalyse) ist aus dem bisherigen
Vergabe-Finder übernommen (servicenow_config.py: SN_SCORING_PROMPT, SONNET_DEEP_DIVE_PROMPT);
neu sind die Souveränitäts- und die Profitabilitätsebene in der Tiefenanalyse.
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

# Profitabilitätssignale mit Bewertungsregeln (die App zeigt sie in dieser Reihenfolge)
PROFIT_SIGNALS = {
    "beratungsvolumen": "Beratungsvolumen pro Jahr: Auftragswert ohne Lizenz-/Hardwareanteil, geteilt durch die Grundlaufzeit. "
                        "positiv ab ca. 500.000 EUR/Jahr, neutral 100.000–500.000, negativ darunter; unklar ohne Wertangabe",
    "preisgewicht": "Gewicht des Preises in den Zuschlagskriterien. positiv: Qualität (Konzept, Team, Referenzen) zählt mindestens 50 %; "
                    "neutral: Preis 50–69 %; negativ: Preis 70–100 % oder Zuschlag nur nach Preis",
    "abrechnung": "Abrechnungsart. positiv: nach Aufwand (Tagessätze, Dienstvertrag); neutral: Festpreis mit klar abgegrenztem Umfang oder gemischt; "
                  "negativ: Pauschalfestpreis/Werkvertrag mit unklarem oder offenem Umfang",
    "umsatzzusage": "Zugesagter Umsatz. positiv: festes Projekt oder garantierte Mindestabnahme; neutral: Rahmenvertrag mit einem Partner ohne Mindestabnahme; "
                    "negativ: nur Höchstwert/Obergrenze bei Rahmenvertrag mit mehreren Partnern und erneutem Wettbewerb je Abruf",
    "reisekosten": "Reisekosten. positiv: werden gesondert erstattet oder Leistung überwiegend remote; neutral: nicht geregelt; "
                   "negativ: Reisekosten/Reisezeiten sind mit dem Tagessatz bzw. Preis abgegolten bei regelmäßiger Vor-Ort-Pflicht",
    "vertragsstrafen": "Vertragsstrafen und Haftung. positiv: keine besonderen Regelungen; neutral: übliche Regelungen (z. B. EVB-IT-Standard); "
                       "negativ: hohe Vertragsstrafen, verschärfte Haftung oder harte Termine mit Abzügen, besonders bei Festpreis",
    "folgegeschaeft": "Folgegeschäft. positiv: Betrieb, Wartung, Weiterentwicklung oder Verlängerungsoptionen enthalten; "
                      "neutral: Folgeaufträge plausibel, aber nicht genannt; negativ: klar einmaliges, abgeschlossenes Vorhaben",
}

# Version der Tiefenanalyse: Erhöhen, wenn sich Prompt oder Kontext ändern; ältere Analysen werden dann neu erstellt.
DEEP_VERSION = 3

# Aktueller Stand souveräner ServiceNow-Betriebsoptionen (aus der SovRadar-Recherche, Stand 10/2026).
# Wird der Tiefenanalyse mitgegeben, weil das Modellwissen hier veraltet sein kann.
SN_CONTEXT = """Stand 10/2026 zu souveränen ServiceNow-Betriebsoptionen in Deutschland (verbindlich, geht eigenem Vorwissen vor):
- ServiceNow-Standard-SaaS läuft in ServiceNow-eigenen Rechenzentren (u. a. Frankfurt); ServiceNow ist ein US-Unternehmen (CLOUD Act).
- "Sovereign Partner Cloud": ServiceNow-Plattform von zertifizierten deutschen Partnern in deren deutschen Rechenzentren betrieben, inkl. Lizenz und Betrieb aus einer Hand: T-Systems (live seit 08.09.2026), noris network (zertifiziert seit 05.10.2026), STACKIT (Managed Service seit 2024); secunet angekündigt für voraussichtlich 2027 (Ziel VS-NfD).
- "ServiceNow Private Stack" (seit 30.04.2026): Self-Hosting, der Kunde bzw. sein IT-Dienstleister betreibt ServiceNow selbst im eigenen Rechenzentrum, mit Funktionseinschränkungen; damit ist Betrieb im Behörden-/Landesrechenzentrum grundsätzlich möglich.
- VS-NfD: Laut BSI-Leitfaden ist VS-Verarbeitung in der Public Cloud derzeit nur als IaaS möglich, nicht als SaaS; für ServiceNow mit VS-NfD bleibt nur Private Stack auf einer zugelassenen Infrastruktur oder ein künftiges zugelassenes Partnerangebot.
Folgerung: Eine Anforderung "Betrieb im eigenen/Landes-Rechenzentrum" oder "nur deutsche Rechenzentren" schließt ServiceNow NICHT aus, sondern verlangt Private Stack bzw. Sovereign Partner Cloud. Bewerte das als Hürde (Aufwand, Funktionsumfang), nicht als K.-o.-Kriterium."""

DEEP_PROMPT = """Analysiere die folgende Ausschreibung aus Sicht eines Beratungshauses, das vor allem ServiceNow-Einführungs- und Umsetzungsdienstleistungen anbietet.

Ziel 1: ultra-knappe Erstbewertung zur Bid-Entscheidung. Erkenne schnell, ob sich eine weitere Beschäftigung lohnt oder ob die Ausschreibung verworfen werden sollte. Bewerte zusätzlich, ob sie für ein auch über ServiceNow aufgestelltes Beratungshaus strategisch interessant sein könnte, auch wenn sie kein klarer ServiceNow-Deal ist.

Ziel 2 (Souveränitätsebene): Prüfe, ob die Ausschreibung Anforderungen an digitale Souveränität bzw. souveränen Cloud-Betrieb stellt. Erfasse nur, was im Text steht oder eindeutig daraus folgt, nichts Vermutetes. Erlaubte Codes:
{codes}
relevanz: "hoch" = ausdrücklich gefordert und für den Zuschlag zentral; "mittel" = mehrere konkrete Anforderungen; "gering" = einzelne Hinweise (z. B. nur DSGVO/Datenhaltung EU); "keine" = nichts erkennbar.
belege: je erkanntem Code ein kurzes Zitat (höchstens 25 Wörter) aus dem Text.
servicenow_folge: 1 Satz, was das für einen ServiceNow-Betrieb bedeutet (z. B. Standard-SaaS ausgeschlossen, souveräner Partnerbetrieb oder Self-Hosting nötig, VS-NfD nur IaaS).

{sn_context}

Ziel 3 (Profitabilität): Schätze ein, wie gut die Rahmenbedingungen für die Marge eines Beratungshauses sind, das ServiceNow-Einführung und -Umsetzung anbietet (es verkauft selbst keine Lizenzen). Bewerte jedes der folgenden Signale mit positiv / neutral / negativ / unklar:
{profit_signals}
Je Signal: "befund" = 1 kurzer Satz mit dem konkreten Sachverhalt (Zahlen nennen, wenn vorhanden); "zitat" = kurzes wörtliches Zitat (höchstens 25 Wörter) als Beleg, leer lassen, wenn der Text dazu nichts sagt (dann "unklar").
Kennzahlen (nur aus dem Text, nichts erfinden; geschätzt wird nur beratungsanteil_prozent):
- auftragswert_eur: genannter Gesamtwert in EUR als ganze Zahl (Summe über alle Lose, wenn nur Loswerte genannt sind), 0 wenn nicht genannt
- wert_art: "geschätzt" (geschätzter Auftragswert), "hoechstwert" (Höchstwert/Obergrenze eines Rahmenvertrags) oder "unbekannt"
- grundlaufzeit_monate: Laufzeit ohne Verlängerungsoptionen, 0 wenn nicht genannt; verlaengerung_monate: mögliche Verlängerungen, 0 wenn keine
- lizenzen_enthalten: "ja", wenn Lizenzen, Subskriptionen oder Hardware mitbeschafft werden, sonst "nein" oder "unklar"
- beratungsanteil_prozent: geschätzter Anteil von Beratung/Umsetzung am Auftragswert (100, wenn nur Dienstleistung), -1 wenn nicht abschätzbar
- preis_gewicht_prozent: Gewichtung des Preises in den Zuschlagskriterien, -1 wenn nicht genannt
- rahmenvertrag_partner: Anzahl Rahmenvertragspartner (1 bei Einzelvergabe), -1 wenn unbekannt
stufe: "hoch" = überwiegend positiv und kein negatives Signal bei Preisgewicht, Abrechnung oder Umsatzzusage; "gering" = mindestens zwei negative Signale oder ein sehr deutliches (z. B. Zuschlag nur nach Preis); "unklar" = vier oder mehr Signale unklar; sonst "mittel".
begruendung: 1 Satz, was die Stufe hauptsächlich bestimmt.

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
        "profitabilitaet": {
            "type": "object",
            "properties": {
                "stufe": {"type": "string", "enum": ["hoch", "mittel", "gering", "unklar"]},
                "begruendung": {"type": "string"},
                "kennzahlen": {
                    "type": "object",
                    "properties": {
                        "auftragswert_eur": {"type": "integer"},
                        "wert_art": {"type": "string", "enum": ["geschätzt", "hoechstwert", "unbekannt"]},
                        "grundlaufzeit_monate": {"type": "integer"},
                        "verlaengerung_monate": {"type": "integer"},
                        "lizenzen_enthalten": {"type": "string", "enum": ["ja", "nein", "unklar"]},
                        "beratungsanteil_prozent": {"type": "integer"},
                        "preis_gewicht_prozent": {"type": "integer"},
                        "rahmenvertrag_partner": {"type": "integer"},
                    },
                    "required": ["auftragswert_eur", "wert_art", "grundlaufzeit_monate", "verlaengerung_monate", "lizenzen_enthalten",
                                 "beratungsanteil_prozent", "preis_gewicht_prozent", "rahmenvertrag_partner"],
                    "additionalProperties": False,
                },
                "signale": {
                    "type": "object",
                    "properties": {k: {
                        "type": "object",
                        "properties": {
                            "bewertung": {"type": "string", "enum": ["positiv", "neutral", "negativ", "unklar"]},
                            "befund": {"type": "string"},
                            "zitat": {"type": "string"},
                        },
                        "required": ["bewertung", "befund", "zitat"],
                        "additionalProperties": False,
                    } for k in PROFIT_SIGNALS},
                    "required": list(PROFIT_SIGNALS),
                    "additionalProperties": False,
                },
            },
            "required": ["stufe", "begruendung", "kennzahlen", "signale"],
            "additionalProperties": False,
        },
    },
    "required": ["kurzbeschreibung", "fachlicher_fit", "fachlicher_fit_begruendung", "relevante_module", "custom_app_bedarf",
                 "custom_app_begruendung", "rolle_servicenow", "haupthemmnisse", "gesamtbewertung",
                 "gesamtbewertung_begruendung", "souveraenitaet", "profitabilitaet"],
    "additionalProperties": False,
}
