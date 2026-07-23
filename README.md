# Evening Report

Evening receipt with tomorrow's forecast and first meeting, for an XP-80C thermal printer.

The overnight counterpart to the morning report. Prints tomorrow's forecast at
9AM, noon and 6PM, plus tomorrow's first meeting.

The calendar section reads the same agenda mail as the morning report - the
Power Automate flow sends a two-day window in one message - but filters the
events to the following day. Setup is identical: see the morning report's
README, or configure `WORK_SENDER`, `AGENDA_SUBJECT_PREFIX`, `GMAIL_ADDRESS`
and `GMAIL_LABEL` at the top of the file and save a Gmail app password as
`gmail_app_password.txt` beside the script.

## Checking it without printing

    python evening_report.py --preview        writes receipt_preview_evening.png
    python evening_report.py --meeting-test   prints the agenda fetch to console

## Tests

Stdlib unittest, no runner to install. The hardware and network modules are
stubbed only if they fail to import, so this runs on the printer machine and
off it alike.

    python -m unittest discover -s tests

## Requirements

- Windows. Printing goes through the Win32 GDI API.
- Python 3.10 or later.
- An 80mm thermal printer installed as a Windows printer. Built against an
  XP-80C at 203 DPI; other 80mm printers should work if the DPI matches.

```bash
pip install -r requirements.txt
```

Set `PRINTER_NAME` in `evening_report.py` to the printer's name as it appears in
Settings > Printers & scanners.

Receipts are rendered as images at 4x and downsampled before printing, which
is what keeps the text dark and legible on a thermal head. Courier is used
throughout; glyphs Courier lacks are composited from Segoe UI Emoji.

## Running

```bash
python evening_report.py
```

## Versioning

Tagged releases, with the change notes in [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT. See [LICENSE](LICENSE).
