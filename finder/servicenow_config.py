"""
ServiceNow-Modul-Definitionen und Keyword-Bundles für gezielte Vergabe-Suche.
Deckt alle ServiceNow-Hauptmodule mit deutschen Suchbegriffen für TED und BKMS ab.
"""

SERVICENOW_MODULES = {
    "ITSM": {
        "label": "IT Service Management",
        "icon": "🎫",
        "description": "Helpdesk, Ticketing, Incident/Change/Problem Management",
        "keywords": [
            "Ticketsystem", "Service Desk", "Helpdesk", "ITSM",
            "Störungsmanagement", "Serviceportal IT", "IT-Serviceanfragen",
        ],
    },
    "ITOM": {
        "label": "IT Operations & Asset Mgmt",
        "icon": "🖥️",
        "description": "CMDB, Asset Management, IT-Infrastruktur-Monitoring",
        "keywords": [
            "IT-Asset-Management", "CMDB", "Konfigurationsmanagement IT",
            "IT-Inventarisierung", "IT-Infrastruktur Management",
        ],
    },
    "HRSD": {
        "label": "HR Service Delivery",
        "icon": "👥",
        "description": "Mitarbeiterportal, HR-Workflows, Onboarding-Automatisierung",
        "keywords": [
            "Mitarbeiterportal", "HR-Portal", "Personalservice Software",
            "Onboarding System", "HR-Self-Service",
        ],
    },
    "GRC": {
        "label": "Governance, Risk & Compliance",
        "icon": "⚖️",
        "description": "Risikomanagement, ISMS, Audit, Compliance-Tracking",
        "keywords": [
            "Risikomanagement Software", "GRC Software", "ISMS Software",
            "Auditmanagement System", "Compliance Management",
            "Informationssicherheit Software",
        ],
    },
    "SecOps": {
        "label": "Security Operations",
        "icon": "🔐",
        "description": "SOC, Schwachstellenmanagement, Incident Response",
        "keywords": [
            "Security Operations", "Schwachstellenmanagement Software",
            "SOC Plattform", "Sicherheitsvorfälle Management",
            "Vulnerability Management",
        ],
    },
    "CSM": {
        "label": "Bürger- & Kundenservice",
        "icon": "🏛️",
        "description": "Bürgerportal, Antragsmanagement, E-Government-Portale",
        "keywords": [
            "Bürgerportal", "Antragsmanagement Software", "E-Government Portal",
            "OZG Umsetzung", "Serviceportal Verwaltung", "Bürgerservice Digital",
        ],
    },
    "PPM": {
        "label": "Projektportfolio Management",
        "icon": "📊",
        "description": "PPM-Software, Projektsteuerung, Portfoliomanagement",
        "keywords": [
            "Projektportfolio Software", "PPM Software",
            "Projektmanagementsoftware", "Projektsteuerung System",
        ],
    },
    "FSM": {
        "label": "Field Service / Instandhaltung",
        "icon": "🔧",
        "description": "Außendienstmanagement, Wartungssysteme, Techniker-Einsatzplanung",
        "keywords": [
            "Instandhaltungssystem", "Wartungsmanagement Software",
            "Field Service Management", "Außendienstmanagement Software",
            "Einsatzplanung Techniker",
        ],
    },
    "ESM": {
        "label": "Enterprise Service Mgmt / Low-Code",
        "icon": "⚡",
        "description": "Workflow-Automatisierung, Low-Code-Plattform, ESM",
        "keywords": [
            "Enterprise Service Management", "Workflow-Automatisierung Software",
            "Low-Code Plattform", "Prozessautomatisierung Software",
            "No-Code Plattform",
        ],
    },
    "DIRECT": {
        "label": "Direkte ServiceNow-Erwähnung",
        "icon": "🎯",
        "description": "Ausschreibungen die ServiceNow oder ITSM-Plattform explizit nennen",
        "keywords": [
            "ServiceNow", "ITSM-Plattform Einführung",
            "Service Management Plattform Einführung",
        ],
    },
}


SN_SCORING_PROMPT = """Du bist ein erfahrener Lösungsarchitekt für ServiceNow. Du kennst alle Plattformfunktionen im Detail. 
Unser Beratungshaus ist spezialisiert auf folgende Module:

{active_modules_context}

Vergaben (gefunden durch ServiceNow-Modul-Keyword-Suche):
{notices_text}

Analysiere die folgenden Ausschreibungen. Bewerte, wie gut sich die Anforderungen der Ausschreibung standardnah mit ServiceNow lösen lassen.
Bewerte jede Vergabe auf ServiceNow-Fit und antworte NUR mit einem JSON-Array:
[
  {{
    "id": 0,
    "score": 85,
    "recommended_modules": ["ITSM", "CSM"],
    "label": "Must pursue",
    "summary": "Landesbehörde sucht neues Service-Portal für Bürger und internes Ticketing. Klarer Fit für CSM und ITSM, produktoffen formuliert."
  }}
]

Score-Regeln (ServiceNow-spezifisch):
- 80–100 "Must pursue": Klarer Software-/Plattformbedarf, passt perfekt zu unseren ServiceNow-Modulen, produktoffen, realistisches Budget
- 60–79 "Optional": Interessant aber unklar — z.B. nur Beratungsleistung, Produkt könnte feststehen, oder marginaler Modul-Fit
- 0–59 "Skip": Kein Software-Bedarf, anderes Produkt bereits festgelegt, oder keines unserer aktiven Module passt sinnvoll

recommended_modules: Eine Liste der Module aus unserem Portfolio (siehe oben), die hier am besten passen (z.B. ["ITSM", "HRSD"]). Darf auch leer [] sein, wenn nichts passt.
title: Ein kurzer, sprechender Titel für das Vorhaben (max 5-8 Wörter), abgeleitet aus der Beschreibung, falls der aktuelle Titel nichtssagend ist.
summary: 1-2 Sätze, fokussiert auf die strategische Begründung, warum genau diese Module das Problem des Kunden lösen.
Antworte AUSSCHLIESSLICH mit dem JSON-Array, kein anderer Text."""


SN_ACQUISITION_PROMPT = """Du bist ein ServiceNow-Vertriebsexperte und analysierst Vergaben für ein ServiceNow-Implementierungspartner im deutschen öffentlichen Sektor.

Die folgende Analyse betrifft Vergaben, die als potenzielle ServiceNow-Projekte identifiziert wurden.
Suchkontext: ServiceNow-Modul-übergreifende Suche

Gefundene Vergaben:
{notices_text}

Erstelle eine strategische ServiceNow-Akquise-Analyse:

## 🎯 Top 3 ServiceNow-Opportunities

Für jede der 3 besten Chancen:
- **Vergabe**: [Titel]
- **Auftraggeber**: [Behörde]
- **ServiceNow-Modul**: [Welches Modul löst das Problem?]
- **Warum ServiceNow?**: Konkrete Verbindung zwischen dem genannten Bedarf und ServiceNow-Funktionen
- **Gesprächseinstieg**: Wie nähert man sich an? (Fachlicher Aufhänger, nicht "wir verkaufen ServiceNow")
- **Timing**: Wann ist der optimale Kontaktzeitpunkt?

## 📋 Modul-Übersicht

Welche ServiceNow-Module sind besonders gefragt? Kurze Zusammenfassung der erkannten Muster.

## 📞 Nächste Schritte

3 konkrete, priorisierte Handlungsempfehlungen für die Akquise.

Sei präzise und handlungsorientiert. Nutze die Links für Kontaktdaten."""

SONNET_DEEP_DIVE_PROMPT = """Analysiere die folgenden Ausschreibungen aus Sicht eines Beratungshauses, das vor allem ServiceNow Einführungs- und Umsetzungsdiensleistungen anbietet.

Ziel: ultra-knappe Erstbewertung zur Bid-Entscheidung. Ich möchte schnell erkennen, ob sich eine weitere Beschäftigung lohnt oder ob die Ausschreibung verworfen werden sollte. Bewerte zusätzlich, ob die Ausschreibung für ein auch über ServiceNow aufgestelltes Beratungshaus strategisch interessant sein könnte, auch wenn sie kein klarer ServiceNow-Deal ist.

Hier sind die Ausschreibungen:
{notices_text}

Bitte nutze exakt folgende Struktur für DEINE ANTWORT.
Antworte AUSSCHLIESSLICH mit einem validen JSON-Array. Kein Markdown drumherum, keine Erklärungen. 
Die "id" MUSS der übergebenen ID entsprechen.

[
  {{
    "id": "Die ID der Ausschreibung",
    "deep_dive": {{
      "kurzbeschreibung": "1-2 Sätze dazu, wer was beschaffen möchte und worum es im Kern geht.",
      "fachlicher_fit": "hoch / mittel / gering",
      "fachlicher_fit_begruendung": "max. 1 Kurzsatz zur Begründung",
      "relevante_module": "ITSM / ITOM / HRSD / GRC / CSM / PPM/SPM / FSM / keine",
      "custom_app_bedarf": "nein / gering / mittel / hoch",
      "custom_app_begruendung": "kurz begründen",
      "rolle_servicenow": "Kernsystem / Ergänzung / irrelevant",
      "haupthemmnisse": ["Stichpunkt 1", "Stichpunkt 2"],
      "gesamtbewertung": "GO / SELECTIVE / NO-GO",
      "gesamtbewertung_begruendung": "1 klarer Begründungssatz"
    }}
  }}
]

Regeln:
- Nur Stichpunkte, keine langen Fließtexte.
- So kurz wie möglich, aber verständlich.
- Fokus auf schnelle Entscheidbarkeit.
- Bei NO-GO klar und hart begründen.
- Keine theoretischen ServiceNow-Möglichkeiten nennen, wenn sie im Vergabekontext nicht realistisch positionierbar sind.
- Berücksichtige, dass ServiceNow auch durch Custom Apps erweitert werden kann, aber bewerte kritisch, ob das mit realistischem Aufwand sinnvoll ist.
"""
