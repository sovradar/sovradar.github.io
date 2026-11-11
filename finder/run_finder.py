"""SovRadar-Ausschreibungsfinder.

Sucht in TED und BKMS mit den ServiceNow-Modul-Suchbegriffen des bisherigen Vergabe-Finders,
bewertet neue Treffer mit Claude (Haiku: Vorbewertung mit Commercial-Pre-Screen, Sonnet: Tiefenanalyse inkl.
Souveränität und Commercial Fit), leitet daraus die Bid-Empfehlung ab (Commercial Gate) und speichert alles verschlüsselt in ../tenders.enc.json.

Umgebungsvariablen:
  ANTHROPIC_API_KEY   API-Key (in GitHub als Secret hinterlegt)
  ANTHROPIC_WORKSPACE_ID  nur nötig, wenn der Key keinem Workspace zugeordnet ist
  SOVRADAR_TENDER_KEY 32-Byte-Schlüssel (Base64) für die verschlüsselte Ablage

Aufruf: python run_finder.py [--days 3] [--no-llm]
"""
import argparse
import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bkms_api import search_notices_bkms_multi
from enrichment import TED_API_PREFIX, extract_notice_fields, get_full_notice_text
from prompts import (COMMERCIAL_CRITERIA, DEEP_PROMPT, DEEP_SCHEMA, DEEP_VERSION, HARD_GATES, MARGIN_ACCELERATORS, MARGIN_KILLERS,
                     PRESCREEN_FLAGS, PRESCREEN_GAPS, SCORING_PROMPT, SCORING_SCHEMA, SN_CONTEXT, SOV_CODES)
from servicenow_config import SERVICENOW_MODULES
from ted_api import search_notices_multi_keywords
from utils import deduplicate_notices

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "tenders.enc.json")
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
DEEP_MIN_SCORE = 50   # ab diesem Haiku-Score folgt die Tiefenanalyse (wie bisher)
KEEP_DAYS = 180       # ältere Einträge werden entfernt
CHUNK = 8             # Ausschreibungen je Haiku-Aufruf


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------- verschlüsselte Ablage ----------------
def _key():
    k = os.environ.get("SOVRADAR_TENDER_KEY", "")
    if not k:
        sys.exit("SOVRADAR_TENDER_KEY fehlt.")
    return base64.b64decode(k)


def load_state():
    if not os.path.exists(STATE):
        return {"version": 1, "updated": None, "runs": [], "tenders": {}}
    blob = json.load(open(STATE, encoding="utf-8"))
    data = AESGCM(_key()).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["data"]), None)
    return json.loads(data)


def save_state(state):
    iv = os.urandom(12)
    data = AESGCM(_key()).encrypt(iv, json.dumps(state, ensure_ascii=False).encode("utf-8"), None)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(data).decode()}, f)


# ---------------- Suche (wie bisheriger Finder, ServiceNow-Modus) ----------------
def search(days):
    groups = {k: v["keywords"] for k, v in SERVICENOW_MODULES.items()}
    per_group = max(3, 100 // len(groups))
    with ThreadPoolExecutor(max_workers=2) as ex:
        ted_f = ex.submit(search_notices_multi_keywords, keyword_groups=groups, country="DEU", days_back=days, limit_per_group=per_group)
        bkms_f = ex.submit(search_notices_bkms_multi, keyword_groups=groups, country="DEU", days_back=days, limit=300)
        ted, bkms = [], []
        try:
            ted = ted_f.result()
        except Exception as e:
            print("TED-Suche fehlgeschlagen:", e)
        try:
            bkms = bkms_f.result()
        except Exception as e:
            print("BKMS-Suche fehlgeschlagen:", e)
    found = deduplicate_notices(ted + bkms)
    return found, len(ted), len(bkms)


# ---------------- KI-Bewertung ----------------
class ConfigError(Exception):
    """Fehler, die sich durch Wiederholen nicht beheben (Key, Workspace, Anfrageformat)."""


def _json_call(client, model, prompt, schema, max_tokens, effort):
    """Ein Aufruf mit garantiert gültigem JSON (structured outputs); bis zu 3 Versuche."""
    import anthropic
    last = None
    for attempt in range(3):
        try:
            r = client.messages.create(
                model=model, max_tokens=max_tokens,
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}],
            )
            if r.stop_reason == "refusal":
                raise RuntimeError(f"Ablehnung ({getattr(r.stop_details, 'category', None)})")
            if r.stop_reason == "max_tokens":
                raise RuntimeError("Antwort abgeschnitten (max_tokens)")
            return json.loads(next(b.text for b in r.content if b.type == "text"))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError, anthropic.NotFoundError) as e:
            raise ConfigError(str(e)) from e
        except Exception as e:  # SDK wiederholt 429/5xx bereits selbst; hier zusätzlich Parse-/Abbruchfehler
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def score_batch(client, items):
    modules = "\n".join(f"- {k}: {v['description']}" for k, v in SERVICENOW_MODULES.items() if k != "DIRECT")
    lines = []
    for i, n in enumerate(items):
        lines.append(f"ID:{i} | Titel: {n.get('title', '')} | Auftraggeber: {n.get('buyer', '')} | Modul-Treffer: {n.get('sn_module', 'unbekannt')} | "
                     f"Wert: {n.get('estimated_value', 'unbekannt')} | Beschreibung: {(n.get('description') or '')[:600]}")
    flags = "\n".join(f"- {k}: {v}" for k, v in PRESCREEN_FLAGS.items())
    gaps = ", ".join(f"{k} ({v})" for k, v in PRESCREEN_GAPS.items())
    prompt = SCORING_PROMPT.format(modules=modules, notices="\n\n".join(lines), flags=flags, gaps=gaps)
    out = _json_call(client, HAIKU, prompt, SCORING_SCHEMA, 8000, "low")
    return {r["id"]: r for r in out["results"] if 0 <= r["id"] < len(items)}


def deep_dive(client, n):
    full = get_full_notice_text(n)
    if full:
        f = extract_notice_fields(full)
        if f.get("deadline") and n.get("deadline") in (None, "", "N/A", "Nicht verfügbar", "Keine Frist angegeben"):
            n["deadline"] = f["deadline"]
        if f.get("estimated_value") and (n.get("estimated_value") or "").strip() in ("", "N/A", "Nicht angegeben", "unbekannt"):
            n["estimated_value"] = f["estimated_value"]
    text = (f"Titel: {n.get('title', '')}\nAuftraggeber: {n.get('buyer', '')}\nWert: {n.get('estimated_value', 'unbekannt')}\n"
            f"Frist: {n.get('deadline', 'unbekannt')}\nBeschreibung: {n.get('description') or ''}")
    if full:
        text += f"\n\nVollständiger Bekanntmachungstext (Quelle):\n{full}"
    codes = "\n".join(f"- {k}: {v}" for k, v in SOV_CODES.items())
    lst = lambda d: "\n".join(f"- {k}: {v}" for k, v in d.items())
    prompt = DEEP_PROMPT.format(codes=codes, notice=text, sn_context=SN_CONTEXT, criteria=lst(COMMERCIAL_CRITERIA),
                                accelerators=lst(MARGIN_ACCELERATORS), killers=lst(MARGIN_KILLERS))
    dd = _json_call(client, SONNET, prompt, DEEP_SCHEMA, 12000, "medium")
    dd["volltext"] = bool(full)
    dd["volltext_quelle"] = ("TED-API" if full.startswith(TED_API_PREFIX) else "PDF") if full else None
    dd["version"] = DEEP_VERSION
    add_volume(dd["commercial"])
    k = dd["commercial"]["kennzahlen"]
    if k["auftragswert_eur"] > 0 and (n.get("estimated_value") or "").strip() in ("", "N/A", "Nicht angegeben", "unbekannt"):
        n["estimated_value"] = f"{k['auftragswert_eur']:,} EUR".replace(",", ".") + (" (Höchstwert)" if k["wert_art"] == "hoechstwert" else "")
    return dd


def add_volume(p):
    """Beratungsvolumen aus den Kennzahlen der KI; gerechnet wird im Code, damit die Zahl nachvollziehbar bleibt."""
    k = p["kennzahlen"]
    wert, monate, anteil = k["auftragswert_eur"], k["grundlaufzeit_monate"], k["beratungsanteil_prozent"]
    if wert <= 0:
        p["volumen"] = None
        return
    anteil_bekannt = 0 <= anteil <= 100
    beratung = wert * (anteil if anteil_bekannt else 100) / 100
    p["volumen"] = {
        "beratung_eur": round(beratung),
        "pro_jahr_eur": round(beratung * 12 / monate) if monate > 0 else None,
        "anteil_bekannt": anteil_bekannt,   # False: Lizenzanteil nicht abschätzbar, Wert evtl. inkl. Lizenzen
        "hoechstwert": k["wert_art"] == "hoechstwert",
    }


# ---------------- Commercial Fit und Commercial Gate ----------------
# Regeln bewusst im Code (nicht im Modell), damit sie nachvollziehbar sind und ohne Neuanalyse angepasst werden können.
CRIT_POINTS = {"positiv": 100, "neutral": 55, "negativ": 15}   # "unklar" zählt nicht mit
ACCEL_BONUS, KILLER_MALUS = 5, 5                                # je belegtem Accelerator bzw. weichem Killer
CF_ATTRACTIVE, CF_MARGIN_RISK = 70, 45                          # Grenzen der Einstufung
SOLUTION_NO_BID, SOLUTION_MUST = 60, 80                         # Grenzen für den Solution Fit (Score)


def commercial_fit(c):
    """Commercial Fit 0–100, Confidence und Einstufung ATTRACTIVE / SELECTIVE / MARGIN RISK / OFFEN."""
    known = [CRIT_POINTS[v["bewertung"]] for v in c["kriterien"].values() if v["bewertung"] in CRIT_POINTS]
    kill = [k["code"] for k in c["killers"]]
    hard = sorted({k for k in kill if k in HARD_GATES})
    soft = len({k for k in kill if k not in HARD_GATES})
    accel = len({a["code"] for a in c["accelerators"]})
    fit = None
    if known:
        fit = round(sum(known) / len(known) + ACCEL_BONUS * accel - KILLER_MALUS * soft)
        fit = max(0, min(100, fit))
    confidence = "hoch" if len(known) >= 5 else "mittel" if len(known) >= 3 else "gering"
    if hard:
        klasse = "MARGIN RISK"
    elif confidence == "gering" or fit is None:
        klasse = "OFFEN"
    elif fit >= CF_ATTRACTIVE:
        klasse = "ATTRACTIVE"
    elif fit < CF_MARGIN_RISK:
        klasse = "MARGIN RISK"
    else:
        klasse = "SELECTIVE"
    return {"fit": fit, "confidence": confidence, "klasse": klasse, "bewertet": len(known), "harte_gates": hard}


def apply_gate(t):
    """Commercial Gate: Solution Fit (Score) und Commercial Fit ergeben die Bid-Empfehlung MUST PURSUE / SELECTIVE / NO-BID."""
    solution = t.get("score") or 0
    d = t.get("deep") or {}
    c = d.get("commercial")
    if not c:
        t["gate"] = {"empfehlung": "NO-BID" if solution < SOLUTION_NO_BID else "SELECTIVE", "review": False, "review_grund": "",
                     "grund": "Solution Fit zu gering (nur Vorbewertung)." if solution < SOLUTION_NO_BID else "Noch ohne Commercial Fit."}
        return
    cf = commercial_fit(c)
    review, review_grund = False, ""
    if solution < SOLUTION_NO_BID:
        emp, grund = "NO-BID", f"Solution Fit {solution} zu gering ({d.get('gesamtbewertung', '')})."
    elif cf["klasse"] == "MARGIN RISK":
        emp = "NO-BID"
        grund = (f"Hartes Gate: {', '.join(MARGIN_KILLERS[h] for h in cf['harte_gates'])}." if cf["harte_gates"]
                 else f"Commercial Fit {cf['fit']} unter {CF_MARGIN_RISK} trotz Solution Fit {solution}.")
        if c.get("strategisch"):
            review, review_grund = True, "Strategische Ausnahme prüfen: " + c.get("strategisch_begruendung", "")
    elif cf["klasse"] == "OFFEN":
        emp, grund = "SELECTIVE", f"Solution Fit {solution}, Commercial Fit wegen Datenlücken nicht belastbar ({cf['bewertet']} von 6 Kriterien bewertbar)."
        review, review_grund = True, "Datenlücken: Vergabeunterlagen prüfen (Preisblatt, Leistungsbeschreibung, Vertrag)."
    elif cf["klasse"] == "ATTRACTIVE" and solution >= SOLUTION_MUST:
        emp, grund = "MUST PURSUE", f"Solution Fit {solution} und Commercial Fit {cf['fit']} (ATTRACTIVE), kein hartes Gate."
    elif cf["klasse"] == "ATTRACTIVE":
        emp, grund = "SELECTIVE", f"Commercial Fit {cf['fit']} (ATTRACTIVE), aber Solution Fit {solution} unter {SOLUTION_MUST}."
    else:
        emp, grund = "SELECTIVE", f"Solution Fit {solution}, Commercial Fit {cf['fit']} (SELECTIVE): Bid nur mit klaren Mitigations, Partner- oder Staffing-Modell."
    t["gate"] = {"empfehlung": emp, "grund": grund, "review": review, "review_grund": review_grund, **cf}


def apply_verdict(t):
    """Wie bisher: Die Sonnet-Gesamtbewertung überschreibt den Haiku-Score."""
    v = (t.get("deep") or {}).get("gesamtbewertung")
    if v == "NO-GO":
        t["score"], t["label"] = 30, "Skip"
    elif v == "SELECTIVE":
        t["score"], t["label"] = 65, "Optional"
    elif v == "GO":
        t["score"], t["label"] = max(t.get("score", 0), 85), "Must pursue"


# ---------------- Ablauf ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-llm", action="store_true", help="nur suchen und speichern, ohne KI-Bewertung")
    args = ap.parse_args()

    state = load_state()
    tenders = state["tenders"]
    found, n_ted, n_bkms = search(args.days)
    new = 0
    for n in found:
        if n["id"] not in tenders:
            n.update(first_seen=now(), status="pending")
            tenders[n["id"]] = n
            new += 1
    print(f"Suche ({args.days} Tage): TED {n_ted}, BKMS {n_bkms}, nach Dubletten {len(found)}, neu {new}")

    stats = {"at": now(), "days": args.days, "ted": n_ted, "bkms": n_bkms, "found": len(found), "new": new, "scored": 0, "deep": 0, "errors": 0}
    if not args.no_llm:
        import anthropic
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
        client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)
        # 1) Vorbewertung aller offenen Einträge (auch aus früheren, fehlgeschlagenen Läufen)
        pending = [t for t in tenders.values() if t.get("status") == "pending"]
        for i in range(0, len(pending), CHUNK):
            batch = pending[i:i + CHUNK]
            try:
                res = score_batch(client, batch)
            except ConfigError as e:
                print("Abbruch der KI-Bewertung (Konfiguration prüfen, Einträge bleiben offen):", e)
                stats["errors"] += 1
                stats["config_error"] = str(e)[:300]
                break
            except Exception as e:
                print("Vorbewertung fehlgeschlagen, wird im nächsten Lauf wiederholt:", e)
                stats["errors"] += 1
                continue
            for j, t in enumerate(batch):
                r = res.get(j)
                if not r:
                    continue
                t.update(score=r["score"], label=r["label"], score_pre=r["score"], label_pre=r["label"], modules=r["recommended_modules"], summary=r["summary"],
                         title_ai=r["title"], red_flags=r["red_flags"], datenluecken=r["datenluecken"],
                         status="scored" if r["score"] < DEEP_MIN_SCORE else "deep_pending")
                stats["scored"] += 1
        # 1b) Pre-Screen für ältere Einträge nachholen (nur red_flags/datenluecken übernehmen, Scores bleiben unverändert)
        missing = [] if stats.get("config_error") else [t for t in tenders.values() if t.get("status") in ("scored", "done") and "red_flags" not in t]
        for i in range(0, len(missing), CHUNK):
            batch = missing[i:i + CHUNK]
            try:
                res = score_batch(client, batch)
            except Exception as e:
                print("Pre-Screen-Nachtrag fehlgeschlagen, wird wiederholt:", e)
                continue
            for j, t in enumerate(batch):
                if res.get(j):
                    t.update(red_flags=res[j]["red_flags"], datenluecken=res[j]["datenluecken"])
        # 2) Tiefenanalyse inkl. Souveränität und Commercial Fit
        #    Analysen einer älteren Version neu erstellen (Score vorher auf die Vorbewertung zurücksetzen)
        for t in tenders.values():
            if t.get("deep") and t["deep"].get("version", 1) < DEEP_VERSION and t.get("status") == "done":
                t["status"] = "deep_pending"
                if "score_pre" in t:
                    t["score"], t["label"] = t["score_pre"], t["label_pre"]
        todo = [] if stats.get("config_error") else [t for t in tenders.values() if t.get("status") == "deep_pending"]

        def run(t):
            try:
                t["deep"] = deep_dive(client, t)
                t["status"] = "done"
                apply_verdict(t)
                return True
            except Exception as e:
                print(f"Tiefenanalyse {t['id']} fehlgeschlagen, wird wiederholt:", e)
                return False

        with ThreadPoolExecutor(max_workers=4) as ex:
            ok = list(ex.map(run, todo))
        stats["deep"] = sum(ok)
        stats["errors"] += ok.count(False)

    # 3) Commercial Gate für alle Einträge neu berechnen (deterministisch, auch nach Regeländerungen ohne Neuanalyse)
    for t in tenders.values():
        if t.get("status") in ("scored", "done"):
            apply_gate(t)

    # Aufräumen: Einträge älter als KEEP_DAYS entfernen
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, t in tenders.items() if (t.get("first_seen") or "") < cutoff]:
        del tenders[k]

    state["updated"] = now()
    state["runs"] = ([stats] + state.get("runs", []))[:20]
    save_state(state)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
