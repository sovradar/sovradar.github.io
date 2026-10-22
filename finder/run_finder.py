"""SovRadar-Ausschreibungsfinder.

Sucht in TED und BKMS mit den ServiceNow-Modul-Suchbegriffen des bisherigen Vergabe-Finders,
bewertet neue Treffer mit Claude (Haiku: Vorbewertung, Sonnet: Tiefenanalyse inkl.
Souveränitätsebene) und speichert alles verschlüsselt in ../tenders.enc.json.

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
from enrichment import extract_notice_fields, get_full_notice_text
from prompts import DEEP_PROMPT, DEEP_SCHEMA, DEEP_VERSION, SCORING_PROMPT, SCORING_SCHEMA, SN_CONTEXT, SOV_CODES
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
    out = _json_call(client, HAIKU, SCORING_PROMPT.format(modules=modules, notices="\n\n".join(lines)), SCORING_SCHEMA, 8000, "low")
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
    dd = _json_call(client, SONNET, DEEP_PROMPT.format(codes=codes, notice=text, sn_context=SN_CONTEXT), DEEP_SCHEMA, 6000, "medium")
    dd["volltext"] = bool(full)
    dd["version"] = DEEP_VERSION
    return dd


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
                         title_ai=r["title"], status="scored" if r["score"] < DEEP_MIN_SCORE else "deep_pending")
                stats["scored"] += 1
        # 2) Tiefenanalyse inkl. Souveränitätsebene
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
