"""The monthly nudge to go and fetch last month's statements.

**Read this before changing anything here.** Mittens & Pence is a program on somebody's
own computer. It has no server behind it and no mail account of its own, so it cannot
simply "send an email" on a date — there is nothing running to send it. Every way round
that has a real cost:

* **A mail server of ours.** There isn't one, and there is not going to be: it would
  mean collecting every family member's email address somewhere central, and an API key
  baked into a program anybody can open is a key anybody can take.
* **Their own mail account.** Possible, and supported below for whoever wants it — but
  it means typing an email password into an app, which is the exact habit nobody should
  be taught. Off by default, and the credentials go in the encrypted vault rather than
  in settings.json.
* **A `mailto:` link.** Opens a compose window. Requires the app to be open and a person
  to be sitting at it, which is precisely what a reminder is for.

So the reminder is delivered two ways that actually work:

1. **A calendar entry.** One file, added once, and the household's own calendar —
   Outlook, Google, Apple — repeats it every month and alerts them wherever they are,
   phone included. If their calendar is set to email them about events, they get an
   email, sent by something whose job that is.
2. **A strip across the top of the app** on and after the day, until something has
   actually been imported for that month.

The day may be anything from 1 to 31. 29, 30 and 31 all mean *the last day of the
month*, which is 28 in February and 29 in a leap year — a rule the calendar can express
exactly (`BYMONTHDAY=-1`) and the banner clamps to the real month length.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
import re
import smtplib
import uuid
from email.message import EmailMessage

from . import config, db

VAULT_REF = "reminder:smtp"
#: Anything at or above this means "the last day of the month", whatever that month is.
LAST_DAY = 29

_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


def valid_email(address: str) -> bool:
    """Deliberately loose. The job is to catch a typo, not to adjudicate RFC 5322."""
    return bool(_EMAIL_RX.match((address or "").strip()))


# ---------------------------------------------------------------------------
# Which day, really
# ---------------------------------------------------------------------------

def chosen_day() -> int:
    try:
        day = int(config.settings.get("reminder_day", 1))
    except (TypeError, ValueError):
        day = 1
    return max(1, min(31, day))


def day_in(year: int, month: int, day: int | None = None) -> int:
    """The chosen day as it actually falls in that month.

    31 in February is the 28th, or the 29th in a leap year. `calendar.monthrange`
    already knows which years are leap years, including the century rule that catches
    people out — 2100 is not one.
    """
    day = chosen_day() if day is None else day
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


def next_due(after: dt.date | None = None) -> dt.date:
    """The next date the reminder falls on, counting today if it is the day."""
    today = after or dt.date.today()
    this = dt.date(today.year, today.month, day_in(today.year, today.month))
    if this >= today:
        return this
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return dt.date(year, month, day_in(year, month))


def period_of(day: dt.date) -> str:
    """The month a reminder on this date is asking about — the one before it."""
    first = day.replace(day=1)
    previous = first - dt.timedelta(days=1)
    return f"{previous:%Y-%m}"


# ---------------------------------------------------------------------------
# Has it been dealt with?
# ---------------------------------------------------------------------------

def imported_since(day: dt.date) -> int:
    return db.scalar("SELECT COUNT(*) FROM import_batches WHERE created_at >= ?",
                     (day.isoformat(),), 0) or 0


def state(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    on = bool(config.settings.get("reminder_on", True))
    address = str(config.settings.get("reminder_email") or "").strip()
    due_this_month = dt.date(today.year, today.month, day_in(today.year, today.month))
    # Nothing set up yet means nothing to fetch. A brand-new copy opened after the first
    # of the month used to greet the person with a strip across the top telling them to
    # go and download last month's statements for the accounts they have not added.
    set_up = (db.scalar("SELECT COUNT(*) FROM accounts WHERE closed=0", (), 0) or 0) > 0
    # Due once the day arrives, and it stays due until something is imported — a
    # reminder that gives up at midnight is no reminder at all.
    due = on and set_up and today >= due_this_month
    done = imported_since(due_this_month) > 0
    dismissed = str(config.settings.get("reminder_dismissed") or "")
    return {
        "on": on,
        "email": address,
        "email_valid": valid_email(address) if address else None,
        "day": chosen_day(),
        "day_means_last": chosen_day() >= LAST_DAY,
        "due_on": due_this_month.isoformat(),
        "next_due": next_due(today).isoformat(),
        "period": period_of(due_this_month),
        "due": bool(due and not done),
        "done": done,
        "show_banner": bool(due and not done and dismissed != due_this_month.isoformat()),
        "smtp_configured": _smtp_settings() is not None,
    }


def dismiss(today: dt.date | None = None):
    today = today or dt.date.today()
    config.settings["reminder_dismissed"] = dt.date(
        today.year, today.month, day_in(today.year, today.month)).isoformat()


# ---------------------------------------------------------------------------
# The calendar entry — the part that reaches them with the app shut
# ---------------------------------------------------------------------------

def _fold(line: str) -> str:
    """iCalendar lines wrap at 75 octets, continuation lines starting with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > 73:
            out.append(chunk.decode("utf-8"))
            chunk = b""
        chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", r"\;")
                .replace(",", r"\,").replace("\n", r"\n"))


def ics(today: dt.date | None = None) -> str:
    """A monthly repeating reminder with an alarm, as one calendar file."""
    today = today or dt.date.today()
    day = chosen_day()
    start = next_due(today)
    # 29, 30 and 31 all mean the last day. BYMONTHDAY=-1 says exactly that and needs no
    # arithmetic from the calendar app; a literal BYMONTHDAY=31 would simply skip every
    # month without a 31st, which is the opposite of what was asked for.
    rule = "FREQ=MONTHLY;BYMONTHDAY=-1" if day >= LAST_DAY else f"FREQ=MONTHLY;BYMONTHDAY={day}"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = (
        f"Download last month's statement for every account {config.APP_NAME} can't "
        f"update on its own, and drop the files into the "
        f"\"{_folder_name()}\" folder.\n\n"
        f"Then open {config.APP_NAME} and press Sync everything. That's it."
    )
    title = "Download last month’s statements"

    # The address the tour asks for has to do something, or asking for it is theatre.
    # It goes on the entry two ways:
    #
    # * as ORGANIZER and ATTENDEE, so the entry is *addressed* to them rather than
    #   floating loose. PARTSTAT=ACCEPTED and RSVP=FALSE matter: without them Outlook
    #   reads an ATTENDEE as a meeting invitation and starts asking the person to
    #   accept their own reminder.
    # * as an ACTION:EMAIL alarm, which is the one thing in the whole iCalendar format
    #   that actually means "email me about this". A client that supports it sends the
    #   email; one that doesn't ignores the block and still shows the pop-up below.
    #   Either way it is their calendar doing the sending, not us.
    address = str(config.settings.get("reminder_email") or "").strip()
    who = [] if not valid_email(address) else [
        f"ORGANIZER;CN={_esc(address)}:mailto:{address}",
        # CUTYPE=INDIVIDUAL and ROLE=REQ-PARTICIPANT are both the default, and spelling
        # them out only pushed the line past 75 octets and into a fold.
        f"ATTENDEE;PARTSTAT=ACCEPTED;RSVP=FALSE;CN={_esc(address)}:mailto:{address}",
    ]
    mail_alarm = [] if not who else [
        "BEGIN:VALARM",
        "ACTION:EMAIL",
        f"SUMMARY:{_esc(config.APP_NAME + ': ' + title)}",
        f"DESCRIPTION:{_esc(body)}",
        f"ATTENDEE:mailto:{address}",
        "TRIGGER:PT9H",
        "END:VALARM",
    ]

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{config.APP_FILE_NAME}//Statement reminder//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uuid.uuid4()}@{config.APP_SLUG}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
        f"DTEND;VALUE=DATE:{start + dt.timedelta(days=1):%Y%m%d}",
        f"RRULE:{rule}",
        f"SUMMARY:{_esc(title)}",
        f"DESCRIPTION:{_esc(body)}",
        "TRANSP:TRANSPARENT",
        *who,
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_esc(title)}",
        "TRIGGER:PT9H",          # nine hours into the day, i.e. mid-morning
        "END:VALARM",
        *mail_alarm,
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def _folder_name() -> str:
    from . import inbox
    return inbox.folder(create=False).name


def write_ics(path=None):
    import pathlib
    out = pathlib.Path(path) if path else (
        config.exports_dir() / f"{config.APP_FILE_NAME} - monthly statement reminder.ics")
    out.write_text(ics(), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# A real email, for anyone who has a mail server to send it with
# ---------------------------------------------------------------------------

def _smtp_settings() -> dict | None:
    from . import security
    creds = security.get(VAULT_REF) or {}
    if creds.get("host") and creds.get("username"):
        return creds
    return None


def save_smtp(host: str, port: int, username: str, password: str, use_tls: bool = True):
    from . import security
    security.put(VAULT_REF, {"host": (host or "").strip(), "port": int(port or 587),
                             "username": (username or "").strip(),
                             "password": password or "", "tls": bool(use_tls)})


def forget_smtp():
    from . import security
    security.put(VAULT_REF, {})


def send_email(to: str | None = None, today: dt.date | None = None) -> dict:
    """Send the reminder for real. Only possible when SMTP details have been given."""
    creds = _smtp_settings()
    if not creds:
        return {"sent": False,
                "why": "No mail server has been set up, so there is nothing to send with."}
    address = (to or config.settings.get("reminder_email") or "").strip()
    if not valid_email(address):
        return {"sent": False, "why": "That email address doesn't look right."}

    today = today or dt.date.today()
    period = period_of(dt.date(today.year, today.month, day_in(today.year, today.month)))
    msg = EmailMessage()
    msg["Subject"] = f"{config.APP_NAME}: time to download {_month_name(period)} statements"
    msg["From"] = creds["username"]
    msg["To"] = address
    msg.set_content(
        f"Time for the monthly download.\n\n"
        f"Get {_month_name(period)}'s statement for every account {config.APP_NAME} "
        f"can't update on its own, and drop the files into your "
        f"\"{_folder_name()}\" folder.\n\n"
        f"Then open {config.APP_NAME} and press Sync everything. Whatever is in the "
        f"folder goes in, and the files are filed away for you.\n\n"
        f"— sent by {config.APP_NAME} on your own computer\n")
    try:
        if int(creds.get("port", 587)) == 465:
            server = smtplib.SMTP_SSL(creds["host"], 465, timeout=20)
        else:
            server = smtplib.SMTP(creds["host"], int(creds.get("port", 587)), timeout=20)
        with server:
            if creds.get("tls") and int(creds.get("port", 587)) != 465:
                server.starttls()
            if creds.get("password"):
                server.login(creds["username"], creds["password"])
            server.send_message(msg)
    except Exception as e:
        logging.exception("reminder email failed")
        return {"sent": False, "why": _plainly(e)}
    config.settings["reminder_last_sent"] = today.isoformat()
    return {"sent": True, "to": address}


def maybe_send(today: dt.date | None = None) -> dict:
    """Called at start-up. Sends at most one email per month, and only if set up."""
    today = today or dt.date.today()
    st = state(today)
    if not st["on"] or not st["due"] or not _smtp_settings():
        return {"sent": False, "why": "not due, or no mail server"}
    last = str(config.settings.get("reminder_last_sent") or "")
    if last and last[:7] == f"{today:%Y-%m}":
        return {"sent": False, "why": "already sent this month"}
    return send_email(today=today)


def _month_name(period: str) -> str:
    try:
        y, m = period.split("-")
        return f"{calendar.month_name[int(m)]} {y}"
    except Exception:
        return "last month's"


def _plainly(e: Exception) -> str:
    text = str(e).strip()
    if "authentication" in text.lower() or "auth" in e.__class__.__name__.lower():
        return ("The mail server rejected the username or password. Gmail and Outlook "
                "need an app password rather than your normal one.")
    return text[:200] or e.__class__.__name__
