"""Telegram + email alerts. Each channel is used only if its settings are present."""
import html
import json
import os
import smtplib
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _line(j):
    posted = (j.get("posted") or "")[:10]
    bits = [j["location"] or j["location_group"]]
    ctc = str(j.get("est_ctc_lpa") or "").strip()
    if ctc:
        bits.append(ctc if ("LPA" in ctc or "$" in ctc or "₹" in ctc) else "~%s LPA" % ctc.strip("~ "))
    if posted:
        bits.append("posted " + posted)
    return bits


def _tg(token, method, payload=None, files=None):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    if files:   # multipart/form-data for documents
        boundary = "----finder%d" % os.getpid()
        parts = []
        for k, v in (payload or {}).items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, k, v)).encode())
        for k, (name, data) in files.items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                          "Content-Type: application/pdf\r\n\r\n" % (boundary, k, name)).encode() + data + b"\r\n")
        body = b"".join(parts) + ("--%s--\r\n" % boundary).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()


def _block(j):
    e = html.escape
    return "\n<b>%s</b> (T%s) · %s\n<a href=\"%s\">%s</a>\n%s  ·  score %s\n" % (
        e(j["company"]), j["tier"], e(j["location_group"]), e(j["url"]), e(j["title"]),
        e(" · ".join(_line(j))), j["priority"])


def telegram(jobs, token, chat_id):
    """One message per job that has a tailored resume (message, then its PDF); the rest are batched."""
    send = lambda text: _tg(token, "sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                                    "disable_web_page_preview": True})
    with_cv = [j for j in jobs if j.get("_resume")]
    rest = [j for j in jobs if not j.get("_resume")]
    for j in with_cv:
        send("<b>Finder: new job + tailored resume</b>\n" + _block(j))
        r = j["_resume"]
        cap = "Resume for %s - %s\nATS keywords covered: %d/%d" % (j["company"], j["title"], r["covered"], r["total"])
        if r.get("missing"):
            cap += "\nMissing: " + ", ".join(r["missing"])
        with open(r["path"], "rb") as f:
            _tg(token, "sendDocument", {"chat_id": chat_id, "caption": cap[:1000]},
                {"document": (os.path.basename(r["path"]), f.read())})
        ref = j.get("_referral")
        if ref:   # <pre> blocks are tap-to-copy in Telegram
            e = html.escape
            send("<b>Referral for %s</b>\n\n1) Connection note (%d chars):\n<pre>%s</pre>\n"
                 "2) Message after they accept:\n<pre>%s</pre>\n3) For the referrer's form (3rd person):\n<pre>%s</pre>"
                 % (e(j["company"]), len(ref["note"]), e(ref["note"]), e(ref["message"]), e(ref["blurb"])))
    if rest:
        chunks, cur = [], "<b>Finder: %d %snew job%s</b>\n" % (len(rest), "more " if with_cv else "",
                                                             "s" if len(rest) != 1 else "")
        for j in rest:
            block = _block(j)
            if len(cur) + len(block) > 3800:
                chunks.append(cur)
                cur = ""
            cur += block
        chunks.append(cur)
        for text in chunks:
            send(text)


def whatsapp(jobs, phone, apikey):
    """WhatsApp via CallMeBot (free, personal use). Sends in chunks of ~10 jobs."""
    import urllib.parse
    for i in range(0, len(jobs), 10):
        part = jobs[i:i + 10]
        lines = ["*Finder: %d new job%s*" % (len(jobs), "s" if len(jobs) != 1 else "")] if i == 0 else []
        for j in part:
            lines.append("\n*%s* (T%s) - %s\n%s\n%s" % (j["company"], j["tier"], j["location_group"], j["title"],
                                                        j["url"]))
        q = urllib.parse.urlencode({"phone": phone, "text": "\n".join(lines), "apikey": apikey})
        urllib.request.urlopen("https://api.callmebot.com/whatsapp.php?" + q, timeout=30).read()


def email(jobs, user, password, to, host="smtp.gmail.com", port=465, dashboard=""):
    rows = "".join(
        "<tr><td style='padding:8px;border-bottom:1px solid #eee'><b>%s</b> <small>Tier %s</small><br>"
        "<a href='%s'>%s</a><br><small>%s · score %s</small></td></tr>" % (
            html.escape(j["company"]), j["tier"], html.escape(j["url"]), html.escape(j["title"]),
            html.escape(" · ".join(_line(j))), j["priority"]) for j in jobs)
    link = "<p><a href='%s'>Open your Finder dashboard</a></p>" % dashboard if dashboard else ""
    body = "<div style='font-family:sans-serif'><h2>%d new matching job%s</h2>%s<table>%s</table></div>" % (
        len(jobs), "s" if len(jobs) != 1 else "", link, rows)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Finder: %d new job%s — %s" % (len(jobs), "s" if len(jobs) != 1 else "",
                                                    ", ".join(dict.fromkeys(j["company"] for j in jobs))[:120])
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(body, "html"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, password)
        s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())


def send_all(jobs, log=print):
    if not jobs:
        return
    jobs = sorted(jobs, key=lambda j: (-j["priority"], j["tier"]))
    env = os.environ
    if env.get("WHATSAPP_PHONE") and env.get("WHATSAPP_APIKEY"):
        try:
            whatsapp(jobs, env["WHATSAPP_PHONE"], env["WHATSAPP_APIKEY"])
            log("whatsapp: sent %d" % len(jobs))
        except Exception as ex:
            log("whatsapp failed: %s" % ex)
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        try:
            telegram(jobs, env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"])
            log("telegram: sent %d" % len(jobs))
        except Exception as ex:
            log("telegram failed: %s" % ex)
    for name, keys in (("telegram", ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")),
                       ("email", ("SMTP_USER", "SMTP_PASS", "EMAIL_TO"))):
        missing = [k for k in keys if not env.get(k)]
        if missing and len(missing) < len(keys):    # all missing = channel switched off on purpose
            log("%s: not sent - secret(s) missing: %s" % (name, ", ".join(missing)))
    if env.get("SMTP_USER") and env.get("SMTP_PASS") and env.get("EMAIL_TO"):
        try:
            email(jobs, env["SMTP_USER"], env["SMTP_PASS"], env["EMAIL_TO"],
                  env.get("SMTP_HOST", "smtp.gmail.com"), int(env.get("SMTP_PORT", "465")),
                  env.get("DASHBOARD_URL", ""))
            log("email: sent %d" % len(jobs))
        except Exception as ex:
            log("email failed: %s" % ex)
