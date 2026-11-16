"""Monatliche Aktualitätsprüfung der Anbieter-Steckbriefe.

Liest die verschlüsselte Prüfbasis (pruefbasis.enc.json, erzeugt beim Build aus den Steckbriefen):
je Anbieter bis zu 8 Quellseiten (Zertifikats-/Trust-Seiten zuerst) und die Angaben, die sich auf
diese Seiten stützen. Jede Seite wird neu abgerufen; hat sich der Seitentext oder die Angabe seit
der letzten Prüfung geändert, vergleicht Claude Haiku beides. Abweichungen landen als Prüfhinweis
in pruefstand.enc.json und werden in SovRadar angezeigt. Bewertungen ändert der Monitor nie selbst.

Seiten, die automatische Abrufe blocken, werden als „nicht prüfbar“ gemeldet (keine Umgehung).

Umgebung: ANTHROPIC_API_KEY, optional ANTHROPIC_WORKSPACE_ID, SOVRADAR_TENDER_KEY (Schlüssel der Ablage)
Aufruf:   python pruef_monitor.py [--force] [--provider noris]
"""
import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from enrichment import _pdf_to_text
from run_finder import ConfigError, _json_call, now

HERE = os.path.dirname(os.path.abspath(__file__))
BASIS = os.path.join(HERE, "..", "pruefbasis.enc.json")
STATE = os.path.join(HERE, "..", "pruefstand.enc.json")
HAIKU = "claude-haiku-5-5"
MAX_CHARS = 40000
UA = "Mozilla/5.0 (compatible; SovRadar-Pruefung/1.0; monatliche Quellenpruefung)"

PROMPT = """Du prüfst, ob eine Quellseite noch zu den Angaben in einem Anbieter-Steckbrief passt.
Anbieter: {name}. Steckbrief-Stand: {stand}. Heute: {today}.

Angaben aus dem Steckbrief, die sich (auch) auf diese Seite stützen:
{claims}

Text der Seite ({url}):
<<<
{text}
>>>

Bewerte jede Angabe einzeln:
- "abweichung": Die Seite sagt konkret etwas anderes oder Neueres, das die Angabe oder ihre Bewertung
  ändern könnte. Beispiele: anderer Testat-Typ (C5 Typ 2 statt Typ 1), neues oder abgelaufenes Zertifikat,
  anderes Prüfdatum, Angebot jetzt allgemein verfügbar oder eingestellt, neuer Standort, neue Partnerschaft,
  neuer Rahmenvertrag, geänderte Eigentümer.
- "bestaetigt": Die Seite stützt die Angabe.
- "kein_bezug": Die Seite sagt dazu nichts. Fehlende Erwähnung ist KEINE Abweichung.
Keine Abweichung sind außerdem: allgemeine Aussagen über Produkte Dritter, die {name} nicht nennen
(z. B. dass ServiceNow Self-Hosted/Private Stack allgemein verfügbar ist, wenn der Steckbrief genau das als
„nur technisch denkbar“ für {name} bewertet); Aussagen, die der Steckbrief bereits enthält oder ausdrücklich
einordnet; reine Werbeaussagen ohne konkreten Fakt; ältere Seiteninhalte, die der Steckbrief schon berücksichtigt.
Sei streng: Melde nur "abweichung", wenn du die Stelle wörtlich zitieren kannst. "befund" ist ein kurzer
deutscher Satz, was genau abweicht; "zitat" ist die wörtliche Stelle (max. 300 Zeichen), sonst leer."""

SCHEMA = {
    "type": "object",
    "properties": {"ergebnisse": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "kriterium": {"type": "string"},
            "status": {"type": "string", "enum": ["abweichung", "bestaetigt", "kein_bezug"]},
            "befund": {"type": "string"},
            "zitat": {"type": "string"},
        },
        "required": ["kriterium", "status", "befund", "zitat"],
        "additionalProperties": False,
    }}},
    "required": ["ergebnisse"],
    "additionalProperties": False,
}


# ---------------- verschlüsselte Ablage (gleicher Schlüssel wie tenders.enc.json) ----------------
def _key():
    k = os.environ.get("SOVRADAR_TENDER_KEY", "")
    if not k:
        sys.exit("SOVRADAR_TENDER_KEY fehlt.")
    return base64.b64decode(k)


def load_enc(path, default):
    if not os.path.exists(path):
        return default
    blob = json.load(open(path, encoding="utf-8"))
    return json.loads(AESGCM(_key()).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["data"]), None))


def save_enc(path, obj):
    iv = os.urandom(12)
    data = AESGCM(_key()).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(data).decode()}, f)


# ---------------- Abruf ----------------
def html_to_text(s):
    s = re.sub(r"(?is)<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h\d)[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def fetch(url):
    """Gibt (status, text) zurück. status: ok | blockiert | fehler | leer."""
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.8"}, allow_redirects=True)
    except requests.RequestException as e:
        return "fehler", type(e).__name__
    if r.status_code in (202, 401, 403, 429, 503):
        return "blockiert", f"HTTP {r.status_code}"
    if r.status_code != 200:
        return "fehler", f"HTTP {r.status_code}"
    if r.content[:4] == b"%PDF" or "pdf" in r.headers.get("content-type", ""):
        text = _pdf_to_text(r.content)
    else:
        r.encoding = r.encoding or r.apparent_encoding
        text = html_to_text(r.text)
        if re.search(r"(?i)captcha|cf-chl|just a moment|access denied|enable javascript", text[:2000]) and len(text) < 3000:
            return "blockiert", "Bot-Prüfung"
    if len(text) < 200:
        return "leer", f"{len(text)} Zeichen"
    return "ok", text


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------------- Prüfung ----------------
def check_page(client, prov, page, state, force):
    url = page["url"]
    claims = {c: prov["claims"][c] for c in page["crits"] if c in prov["claims"]}
    claims_hash = sha(json.dumps(claims, sort_keys=True, ensure_ascii=False))
    key = f'{prov["id"]}|{url}'
    old = state["seiten"].get(key, {})
    status, text = fetch(url)
    rec = {"provider": prov["id"], "url": url, "q": page["q"], "title": page["title"], "crits": page["crits"],
           "status": status, "geprueft": now(), "hash": old.get("hash"), "claims_hash": old.get("claims_hash"),
           "ausgewertet": old.get("ausgewertet"), "info": "" if status == "ok" else text}
    if status != "ok" or not claims:
        return key, rec, None
    page_hash = sha(text)
    if not force and old.get("hash") == page_hash and old.get("claims_hash") == claims_hash and old.get("ausgewertet"):
        return key, rec, None  # unverändert, vorherige Auswertung bleibt gültig
    prompt = PROMPT.format(name=prov["name"], stand=prov["stand"], today=now()[:10], url=url,
                           claims="\n".join(f"- {v}" for v in claims.values()), text=text[:MAX_CHARS])
    try:
        res = _json_call(client, HAIKU, prompt, SCHEMA, 8000, "medium")
    except ConfigError:
        raise
    except Exception as e:  # einzelne Seite nicht auswertbar – beim nächsten Lauf erneut
        rec.update(status="fehler", info=f"Auswertung: {type(e).__name__}")
        return key, rec, None
    rec.update(hash=page_hash, claims_hash=claims_hash, ausgewertet=now())
    return key, rec, [r for r in res["ergebnisse"] if r["kriterium"][:3] in claims]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="alle Seiten neu auswerten, auch unveränderte")
    ap.add_argument("--provider", help="nur diesen Anbieter prüfen (id)")
    a = ap.parse_args()

    basis = load_enc(BASIS, None)
    if not basis:
        sys.exit("pruefbasis.enc.json fehlt – erst lokal `npm run publish-build` ausführen und pushen.")
    state = load_enc(STATE, {"version": 1, "seiten": {}, "hinweise": {}, "laeufe": []})

    import anthropic
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)

    provs = [p for p in basis["providers"] if not a.provider or p["id"] == a.provider]
    jobs = [(p, pg) for p in provs for pg in p["pages"]]
    by_id = {p["id"]: p for p in provs}
    run = {"zeit": now(), "basis": basis["generated"], "seiten": len(jobs), "ausgewertet": 0, "abweichungen": 0,
           "nicht_pruefbar": 0, "fehler": None}
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for key, rec, results in ex.map(lambda j: check_page(client, j[0], j[1], state, a.force), jobs):
                state["seiten"][key] = rec
                if rec["status"] != "ok":
                    run["nicht_pruefbar"] += 1
                if results is None:
                    continue
                run["ausgewertet"] += 1
                prov = by_id[rec["provider"]]
                for r in results:
                    crit = r["kriterium"][:3]
                    hk = f'{prov["id"]}|{crit}|{rec["url"]}'
                    if r["status"] == "abweichung":
                        prev = state["hinweise"].get(hk, {})
                        state["hinweise"][hk] = {"provider": prov["id"], "name": prov["name"], "kriterium": crit,
                                                 "angabe": prov["claims"].get(crit, "")[:400], "befund": r["befund"],
                                                 "zitat": r["zitat"][:400], "url": rec["url"], "q": rec["q"],
                                                 "seit": prev.get("seit", now()), "geprueft": now()}
                    else:
                        state["hinweise"].pop(hk, None)  # Angabe passt (wieder) – Hinweis erledigt
    except ConfigError as e:
        run["fehler"] = str(e)[:300]
    # Hinweise zu Angaben/Seiten, die nicht mehr in der Prüfbasis stehen, entfernen
    valid = {f'{p["id"]}|{c}|{pg["url"]}' for p in basis["providers"] for pg in p["pages"] for c in pg["crits"]}
    state["hinweise"] = {k: v for k, v in state["hinweise"].items() if k in valid}
    valid_pages = {f'{p["id"]}|{pg["url"]}' for p in basis["providers"] for pg in p["pages"]}
    state["seiten"] = {k: v for k, v in state["seiten"].items() if k in valid_pages}
    run["abweichungen"] = len(state["hinweise"])
    state["laeufe"] = [run] + state["laeufe"][:11]
    state["updated"] = now()
    save_enc(STATE, state)
    print(json.dumps(run, ensure_ascii=False))
    if run["fehler"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
