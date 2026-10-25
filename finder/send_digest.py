"""Wöchentliche Kurzübersicht der neuen Ausschreibungen per Mail.

Inhalt bewusst knapp (Titel, Auftraggeber, Frist, Bewertung); Details nur per Link in SovRadar
(mit Login). Empfänger stehen als BCC in der Mail, damit sie die Adressen der anderen nicht sehen.

Umgebungsvariablen:
  SOVRADAR_TENDER_KEY  Schlüssel der verschlüsselten Ablage
  MAIL_USER            Gmail-Adresse des Absenders
  MAIL_APP_PASSWORD    App-Passwort dieses Kontos
  MAIL_TO              Empfänger, durch Komma getrennt
  MAIL_TEST_TO         Testadresse (bei manuellem Start mit "nur Test")
  MAIL_TEST_MODE       "true" = nur an MAIL_TEST_TO (sonst an den Absender selbst) senden

Aufruf: python send_digest.py [--days 7] [--dry-run]
"""
import argparse
import html
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from run_finder import load_state

SITE = "https://sovradar.github.io/"
RANK = {"hoch": 3, "mittel": 2, "gering": 1, "keine": 0}


def buyer(t):
    b = t.get("buyer", "")
    return ", ".join(map(str, b)) if isinstance(b, list) else str(b)


def frist(t):
    try:
        return datetime.strptime(t.get("deadline", ""), "%d.%m.%Y").date()
    except ValueError:
        return None


def select(state, days):
    """Neu gefunden in den letzten `days` Tagen, Standardfilter der App (Souveränitätsbezug, ab Optional, Frist offen)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    today = datetime.now().date()
    out = []
    for t in state["tenders"].values():
        d = t.get("deep") or {}
        sov = (d.get("souveraenitaet") or {}).get("relevanz", "keine")
        f = frist(t)
        if ((t.get("first_seen") or "") >= since and t.get("status") == "done" and RANK.get(sov, 0) >= 1
                and (t.get("score") or 0) >= 60 and (f is None or f >= today)):
            out.append(t)
    return sorted(out, key=lambda t: (-RANK[t["deep"]["souveraenitaet"]["relevanz"]], -(t.get("score") or 0)))


def link(t):
    from urllib.parse import quote
    return f"{SITE}#/ausschreibungen/{quote(t['id'], safe='')}"


def build(items, days, total_new):
    week = datetime.now().strftime("%d.%m.%Y")
    subject = f"SovRadar: {len(items)} neue Ausschreibung{'en' if len(items) != 1 else ''} mit ServiceNow- und Souveränitätsbezug" if items \
        else "SovRadar: diese Woche keine neuen Treffer"
    lines = [f"SovRadar – Wochenübersicht vom {week}", "",
             f"In den letzten {days} Tagen wurden {total_new} Ausschreibungen neu gefunden; {len(items)} davon haben ServiceNow-Potenzial (ab \"Optional\") und Souveränitätsbezug.", ""]
    rows = []
    for t in items:
        d = t["deep"]
        title = t.get("title_ai") or t.get("title") or ""
        f = t.get("deadline") if frist(t) else "unbekannt"
        lines += [f"- {title}", f"  {buyer(t)} | Frist {f} | {d['gesamtbewertung']} | Souveränität {d['souveraenitaet']['relevanz']}", f"  {link(t)}", ""]
        rows.append(f"""<tr><td style="padding:10px 0;border-top:1px solid #e3e7e8">
<a href="{html.escape(link(t))}" style="color:#0a5655;font-weight:600;text-decoration:none">{html.escape(title)}</a><br>
<span style="color:#4a5658;font-size:13px">{html.escape(buyer(t))}</span><br>
<span style="font-size:12px;color:#4a5658">Frist {html.escape(str(f))} · <b>{d['gesamtbewertung']}</b> · Souveränität {d['souveraenitaet']['relevanz']}</span>
</td></tr>""")
    lines += ["Details, Begründungen und passende Cloud-Anbieter: in SovRadar (Login erforderlich).", SITE]
    body_html = f"""<div style="font-family:Segoe UI,Arial,sans-serif;color:#1c2526;max-width:640px">
<p style="font-size:18px;font-weight:600;margin:0 0 4px">SovRadar · Wochenübersicht</p>
<p style="color:#4a5658;font-size:13px;margin:0 0 14px">{week} · {total_new} neu gefunden, {len(items)} mit ServiceNow-Potenzial und Souveränitätsbezug</p>
{'<table style="width:100%;border-collapse:collapse">' + ''.join(rows) + '</table>' if rows else '<p>Diese Woche keine neuen Treffer, die die Standardfilter erfüllen.</p>'}
<p style="font-size:13px;color:#4a5658;margin-top:16px">Details, Begründungen und passende Cloud-Anbieter in <a href="{SITE}" style="color:#0a5655">SovRadar</a> (Login erforderlich). Interne Einschätzung, keine Zertifizierung.</p>
</div>"""
    return subject, "\n".join(lines), body_html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="Mail nur anzeigen, nicht senden")
    args = ap.parse_args()

    state = load_state()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    total_new = sum(1 for t in state["tenders"].values() if (t.get("first_seen") or "") >= since)
    items = select(state, args.days)
    subject, text, body_html = build(items, args.days, total_new)
    if args.dry_run:
        print(subject, "\n", text)
        return

    user, pw = os.environ.get("MAIL_USER", ""), os.environ.get("MAIL_APP_PASSWORD", "")
    test = os.environ.get("MAIL_TEST_MODE", "").lower() == "true"
    test_to = (os.environ.get("MAIL_TEST_TO", "").strip() or user) if test else ""
    to = [a.strip() for a in (test_to or os.environ.get("MAIL_TO", "")).split(",") if a.strip()]
    if not (user and pw and to):
        sys.exit("MAIL_USER, MAIL_APP_PASSWORD und MAIL_TO (oder MAIL_TEST_TO) müssen gesetzt sein.")

    msg = EmailMessage()
    msg["Subject"] = ("[Test] " if test_to else "") + subject
    msg["From"] = formataddr(("SovRadar", user))
    msg["To"] = formataddr(("SovRadar-Verteiler", user))   # Empfänger nur als BCC
    msg["Bcc"] = ", ".join(to)
    msg["Message-ID"] = make_msgid(domain="sovradar.github.io")
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"Gesendet an {len(to)} Empfänger: {msg['Subject']} ({len(items)} Treffer)")


if __name__ == "__main__":
    main()
