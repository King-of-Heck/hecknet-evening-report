import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import io
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

def build_receipt_image(morning, noon, evening, now, printed, tomorrow_str):
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

    final_h = int(y / S) + line_h
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


def print_receipt():
    print("Fetching weather...")
    data = fetch_weather()

    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    morning   = extract_slot(data, tomorrow, MORNING_HOUR)
    noon      = extract_slot(data, tomorrow, NOON_HOUR)
    evening   = extract_slot(data, tomorrow, EVENING_HOUR)

    now     = datetime.now().strftime("%A, %B %d %Y")
    printed = datetime.now().strftime("Printed %I:%M %p")

    print("Rendering receipt...")
    receipt_img = build_receipt_image(
        morning, noon, evening,
        now, printed, tomorrow,
    )

    print("Sending to printer...")
    send_image_to_printer(receipt_img)


if __name__ == "__main__":
    try:
        print_receipt()
        print("Receipt printed successfully.")
    except Exception as e:
        import os, traceback
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_receipt_evening_error.log")
        with open(log_path, "a") as f:
            f.write(f"\n[{datetime.now()}] ERROR:\n{traceback.format_exc()}\n")
        raise
