import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import io
import json
import os
import re
import time
import traceback
import requests
import win32print
import win32ui
from datetime import datetime, date, timedelta
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageWin


"""
"""

WEATHER_RETRY_ATTEMPTS = 3    # how many times to try before giving up
WEATHER_RETRY_WAIT     = 60   # seconds to wait between attempts

PRINTER_NAME = "XP-80C"

FONT_PATH    = "cour.ttf"
FONT_BOLD    = "courbd.ttf"
FONT_SIZE    = 32          # pt — adjust to taste

RENDER_SCALE = 4

PRINTER_DPI  = 203
PAPER_MM     = 80
PAPER_PX     = int(PAPER_MM / 25.4 * PRINTER_DPI)   # ≈ 638 px at output res
MARGIN_PX    = 10
TEXT_PX      = PAPER_PX - 2 * MARGIN_PX

LATITUDE     = 43.6532
LONGITUDE    = -79.3832
TIMEZONE     = "America/Toronto"

MORNING_HOUR   = 9
NOON_HOUR      = 12
EVENING_HOUR   = 18

# First-meeting section (work calendar via Power Automate -> Gmail)
# Same account/label/subject as the Morning report -- it's the SAME email
WORK_SENDER             = "work.sender@example.com"   # From: address the flow sends as
# Distinct from Morning's "HECKNET-AGENDA" prefix: the same flow fires
# twice a day (7:15 AM and 10:15 PM), and the PM run's email gets this
AGENDA_SUBJECT_PREFIX   = "HECKNET-AGENDA-PM"
GMAIL_ADDRESS           = "you@example.com"
GMAIL_LABEL             = "MorningReport"
GMAIL_TRASH_FOLDER      = "[Gmail]/Bin"   # this account's locale names it "Bin", not "Trash"
GMAIL_APP_PASSWORD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gmail_app_password.txt")


WMO_CODES = {
    0:  "Clear sky",
    1:  "Mainly clear",        2:  "Partly cloudy",    3:  "Overcast",
    45: "Foggy",               48: "Icy fog",
    51: "Light drizzle",       53: "Drizzle",          55: "Heavy drizzle",
    61: "Light rain",          63: "Rain",              65: "Heavy rain",
    71: "Light snow",          73: "Snow",              75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",       81: "Showers",           82: "Heavy showers",
    85: "Light snow showers",  86: "Snow showers",
    95: "Thunderstorm",        96: "Thunderstorm w/ hail", 99: "Heavy thunderstorm",
}

def describe(code):
    return WMO_CODES.get(code, f"Code {code}")


def fetch_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":           LATITUDE,
        "longitude":          LONGITUDE,
        "hourly":             "temperature_2m,apparent_temperature,precipitation_probability,weathercode",
        "temperature_unit":   "celsius",
        "wind_speed_unit":    "kmh",
        "precipitation_unit": "mm",
        "timezone":           TIMEZONE,
        "forecast_days":      2,
    }
    last_error = None
    for attempt in range(1, WEATHER_RETRY_ATTEMPTS + 1):
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            if attempt < WEATHER_RETRY_ATTEMPTS:
                print(f"  Weather fetch failed (attempt {attempt}/{WEATHER_RETRY_ATTEMPTS}): {e}")
                print(f"  Retrying in {WEATHER_RETRY_WAIT}s...")
                time.sleep(WEATHER_RETRY_WAIT)
            else:
                print(f"  Weather fetch failed after {WEATHER_RETRY_ATTEMPTS} attempts.")
    raise last_error

def extract_slot(data, target_date_str, hour):
    times = data["hourly"]["time"]
    target = f"{target_date_str}T{hour:02d}:00"
    if target in times:
        idx = times.index(target)
    else:
        day_times = [t for t in times if t.startswith(target_date_str)]
        if not day_times:
            raise ValueError(f"No forecast data found for {target_date_str}")
        idx = times.index(min(day_times, key=lambda t: abs(int(t[11:13]) - hour)))
    return {
        "temp":      round(data["hourly"]["temperature_2m"][idx]),
        "feels":     round(data["hourly"]["apparent_temperature"][idx]),
        "precip":    data["hourly"]["precipitation_probability"][idx],
        "condition": describe(data["hourly"]["weathercode"][idx]),
    }


def load_font(bold=False):
    """Load font at RENDER_SCALE * FONT_SIZE for supersampled rendering."""
    path = FONT_BOLD if bold else FONT_PATH
    try:
        return ImageFont.truetype(path, FONT_SIZE * RENDER_SCALE)
    except OSError:
        return ImageFont.load_default()

def _wrap(text, font, max_width):
    """Greedy word-wrap: split text into lines that each render <= max_width
    px in the given font. A single word longer than max_width is kept whole."""
    line, wrapped = "", []
    for word in text.split():
        test = (line + " " + word).strip()
        if font.getlength(test) <= max_width:
            line = test
        else:
            if line:
                wrapped.append(line)
            line = word
    if line:
        wrapped.append(line)
    return wrapped

# FETCH FIRST MEETING (work calendar via Gmail)
# Reads the SAME agenda email as the Morning report (Power Automate flow

def _html_to_text(html_body):
    """Strip tags, unescape entities, then collapse whitespace runs to a
    single space. The last step matters: Power Automate's rich-text editor
    hard-wraps long paragraphs in the raw HTML source (a real embedded
    newline, not a MIME soft line break), which is invisible once a browser
    renders it but is an unescaped control character to json.loads."""
    import re, html as html_mod
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", html_body))
    return re.sub(r"\s+", " ", text)

def _extract_agenda_json(raw_email_bytes):
    """Parse a raw RFC822 message; return the JSON agenda list from its body.
    Raises on any failure — caller decides how to degrade."""
    import email as email_mod
    msg = email_mod.message_from_bytes(raw_email_bytes)
    body = None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() in ("text/html", "text/plain"):
            payload = part.get_payload(decode=True)
            if payload:
                body = payload.decode(part.get_content_charset() or "utf-8",
                                      errors="replace")
                if part.get_content_type() == "text/html":
                    break   # prefer the HTML part; keep looking if plain
    if body is None:
        raise ValueError("no text part found in agenda email")
    text  = _html_to_text(body)
    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array found in agenda email body")
    return json.loads(text[start:end + 1])

def _pick_first_meeting(events, target_date):
    """Apply the exclusion rules and return the earliest remaining event on
    target_date (a "YYYY-MM-DD" string), or None. Rules: drop events not on
    target_date, all-day events, declined meetings, and solo blocks (no
    required or optional attendees). Tentative/not-responded count."""
    def qualifies(ev):
        if not (ev.get("start") or "").startswith(target_date):
            return False
        if ev.get("isAllDay"):
            return False
        if ev.get("response") == "declined":
            return False
        attendees = ((ev.get("requiredAttendees") or "")
                     + (ev.get("optionalAttendees") or ""))
        if not attendees.replace(";", "").strip():
            return False
        return True
    qualifying = [ev for ev in events if qualifies(ev)]
    if not qualifying:
        return None
    return min(qualifying, key=lambda ev: ev.get("start") or "~")

def _format_meeting_time(start_iso):
    """'2026-07-18T09:30:00' -> '9:30 AM'. Truncates to seconds so
    fractional-second variants from the flow can't break parsing."""
    dt = datetime.fromisoformat(start_iso[:19])
    return dt.strftime("%I:%M %p").lstrip("0")

def fetch_first_meeting(target_date):
    """
    Reads today's HECKNET-AGENDA email from the Gmail label over IMAP and
    picks the first qualifying meeting on target_date ("YYYY-MM-DD").
    Housekeeping: any message in the label whose subject is NOT today's
    expected subject is moved to Trash. Returns {"time", "subject"},
    {"no_meetings": True}, or None on failure. All exceptions are caught —
    this can never kill the receipt.
    """
    import imaplib
    import email as email_mod
    try:
        with open(GMAIL_APP_PASSWORD_FILE, "r") as f:
            app_password = f.read().strip().replace(" ", "")
        expected_subject = (AGENDA_SUBJECT_PREFIX + " "
                            + date.today().strftime("%Y-%m-%d"))

        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            imap.login(GMAIL_ADDRESS, app_password)
            status, _ = imap.select(f'"{GMAIL_LABEL}"')
            if status != "OK":
                raise RuntimeError(f"could not open label {GMAIL_LABEL}")

            status, data = imap.uid(
                "SEARCH", None,
                f'(FROM "{WORK_SENDER}" SUBJECT "{expected_subject}")')
            if status != "OK":
                raise RuntimeError("IMAP search failed")

            uids       = data[0].split()
            result     = None
            agenda_raw = None
            # SEARCH matches substrings; walk newest-first and require the
            # Subject header to EQUAL the expected subject (trust rule).
            for uid in reversed(uids):
                status, msg_data = imap.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    raise RuntimeError("IMAP fetch failed")
                candidate = email_mod.message_from_bytes(msg_data[0][1])
                if (candidate.get("Subject") or "").strip() == expected_subject:
                    agenda_raw = msg_data[0][1]
                    break
            if agenda_raw is not None:
                events = _extract_agenda_json(agenda_raw)
                print(f"  Meeting: {len(events)} events in agenda email")
                first = _pick_first_meeting(events, target_date)
                if first is None:
                    result = {"no_meetings": True}
                else:
                    result = {"time":    _format_meeting_time(first["start"]),
                              "subject": first.get("subject") or "(no subject)"}

            status, data = imap.uid("SEARCH", None, "ALL")
            if status == "OK":
                for uid in data[0].split():
                    s, hdr = imap.uid(
                        "FETCH", uid,
                        "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                    if s != "OK" or not hdr or hdr[0] is None:
                        continue
                    subj = (email_mod.message_from_bytes(hdr[0][1]).get("Subject") or "").strip()
                    if subj != expected_subject:
                        imap.uid("COPY", uid, f'"{GMAIL_TRASH_FOLDER}"')
                        imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
                imap.expunge()
            imap.close()
        finally:
            imap.logout()

        if result is None:
            print(f"  Meeting: no email found for '{expected_subject}'")
        return result
    except Exception as e:
        print(f"  Meeting fetch failed - {e}")
        return None

def build_receipt_image(morning, noon, evening, now, printed, tomorrow_str, meeting=None):
    """
    Renders receipt at 4x resolution then downsamples to OUTPUT resolution.
    This gives much crisper, darker text than rendering at output res directly.
    """
    S        = RENDER_SCALE
    paper_w  = PAPER_PX * S
    margin   = MARGIN_PX * S
    text_w   = TEXT_PX * S
    line_h   = (FONT_SIZE + 5) * S

    font_n = load_font(bold=False)
    font_b = load_font(bold=True)

    DIVIDER = "─" * int(text_w / (FONT_SIZE * S * 0.62))

    ops = []
    def add(text, bold=False, align="left"):
        ops.append((text, font_b if bold else font_n, align))
    def div():
        add(DIVIDER)

    add("TORONTO",           bold=True,  align="center")
    add("EVENING BEBE REPORT",  bold=True,  align="center")
    add(now,                             align="center")
    add(printed,                         align="center")
    div()

    tomorrow_label = datetime.strptime(tomorrow_str, "%Y-%m-%d").strftime("%b %d")

    for label, slot in [
        (f"TOMORROW   (9 AM)",            morning),
        (f"TOMORROW   (12 PM)",           noon),
        (f"TOMORROW   (6 PM)",            evening),
    ]:
        add(label, bold=True)
        add(f"  {slot['condition']}")
        add(f"  Temp:   {slot['temp']}\u00b0C  (feels {slot['feels']}\u00b0C)")
        add(f"  Precip: {slot['precip']}% chance")
        div()

    # First meeting section (tomorrow) — ALWAYS rendered (spec: positive
    # confirmation; a missing section and a broken pipeline must not look
    add("FIRST MEETING", bold=True, align="center")
    if meeting is None:
        add("  Meeting info unavailable")
    elif meeting.get("no_meetings"):
        add("  No meetings tomorrow")
    else:
        add(f"  {meeting['time']}")
        indent_w = int(font_n.getlength("  "))
        for wl in _wrap(meeting["subject"], font_n, text_w - indent_w):
            add("  " + wl)
    div()

    add("Night Night!", align="center")

    total_h  = len(ops) * line_h + line_h * 3

    hi_res = Image.new("RGB", (paper_w, total_h), "white")
    draw   = ImageDraw.Draw(hi_res)
    y      = margin // 2

    for text, font, align in ops:
        if align == "center":
            tw = font.getlength(text)
            x  = (paper_w - tw) / 2
        else:
            x = margin
        draw.text((x, y), text, font=font, fill="black")
        y += line_h

    out_w  = PAPER_PX
    out_h  = int(total_h / S)
    output = hi_res.resize((out_w, out_h), Image.LANCZOS)

    output = ImageEnhance.Contrast(output).enhance(2.5)
    output = output.convert("L").convert("RGB")

    # Crop to actual content (remove excess white at bottom).
    # y is already divided by S (output scale); line_h is NOT (it's a
    final_h = int(y / S) + line_h // S
    output = output.crop((0, 0, out_w, final_h))

    return output


def send_image_to_printer(pil_img):
    """
    Send PIL image to printer via Windows GDI.
    Pre-resizes to exact printer pixel width so driver does zero scaling,
    which eliminates row-skipping artefacts.
    """
    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(PRINTER_NAME)
    printer_w = hdc.GetDeviceCaps(110)  # HORZRES

    img_w, img_h = pil_img.size
    if img_w != printer_w:
        new_h = int(img_h * printer_w / img_w)
        pil_img = pil_img.resize((printer_w, new_h), Image.LANCZOS)
        img_w, img_h = pil_img.size

    hdc.StartDoc("Evening Weather Receipt")
    hdc.StartPage()
    dib = ImageWin.Dib(pil_img)
    dib.draw(hdc.GetHandleOutput(), (0, 0, img_w, img_h))
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()


def print_receipt(preview=False):
    print("Fetching weather...")
    data = fetch_weather()

    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    morning   = extract_slot(data, tomorrow, MORNING_HOUR)
    noon      = extract_slot(data, tomorrow, NOON_HOUR)
    evening   = extract_slot(data, tomorrow, EVENING_HOUR)

    print("Fetching first meeting...")
    meeting = fetch_first_meeting(tomorrow)

    now     = datetime.now().strftime("%A, %B %d %Y")
    printed = datetime.now().strftime("Printed %I:%M %p")

    print("Rendering receipt...")
    receipt_img = build_receipt_image(
        morning, noon, evening,
        now, printed, tomorrow, meeting,
    )

    if preview:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "receipt_preview_evening.png")
        receipt_img.save(out)
        print(f"Preview saved to {out} (nothing sent to printer).")
    else:
        print("Sending to printer...")
        send_image_to_printer(receipt_img)


if __name__ == "__main__":
    if "--meeting-test" in sys.argv:
        print("Meeting fetch test (nothing will be printed to paper)...")
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        result = fetch_first_meeting(tomorrow)
        if result is None:
            print("RESULT: Meeting info unavailable")
        elif result.get("no_meetings"):
            print("RESULT: No meetings tomorrow")
        else:
            print(f"RESULT: FIRST MEETING -> {result['time']} | {result['subject']}")
        sys.exit(0)

    try:
        print_receipt(preview="--preview" in sys.argv)
        print("Receipt printed successfully.")
    except Exception as e:
        import os, traceback
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_receipt_evening_error.log")
        with open(log_path, "a") as f:
            f.write(f"\n[{datetime.now()}] ERROR:\n{traceback.format_exc()}\n")
        raise
