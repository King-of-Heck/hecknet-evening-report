"""Tests for evening_report.py (stdlib unittest only)."""
import html as html_mod
import importlib
import importlib.util
import json
import pathlib
import sys
import unittest
from unittest import mock

# The receipt script imports hardware/network modules at module level
# (win32print, win32ui, requests). On the production PC these are real
for _name in ("win32print", "win32ui", "requests"):
    try:
        importlib.import_module(_name)
    except ImportError:
        sys.modules[_name] = mock.MagicMock()

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "evening_report", ROOT / "evening_report.py")
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)


class TestWrap(unittest.TestCase):
    def test_lines_fit_width_and_preserve_words(self):
        font = er.load_font(bold=False)
        text = ("The quick brown fox jumps over the lazy dog and then "
                "keeps on running well past the edge of the paper")
        width = 300 * er.RENDER_SCALE
        lines = er._wrap(text, font, width)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(font.getlength(line), width)
        self.assertEqual(" ".join(lines), text)

    def test_short_text_is_single_line(self):
        font = er.load_font(bold=False)
        self.assertEqual(er._wrap("Standup", font, 300 * er.RENDER_SCALE),
                         ["Standup"])


# Agenda spans TWO days, matching the widened flow's real payload shape:
# today's (2026-07-17) events plus tomorrow's (2026-07-18). The evening
SAMPLE_AGENDA = [
    {"subject": "Today's leftover meeting", "start": "2026-07-17T16:00:00",
     "isAllDay": False, "response": "accepted",
     "requiredAttendees": "a@work.com", "optionalAttendees": ""},
    {"subject": "Tomorrow declined thing", "start": "2026-07-18T08:00:00",
     "isAllDay": False, "response": "declined",
     "requiredAttendees": "x@work.com", "optionalAttendees": ""},
    {"subject": "Tomorrow focus block", "start": "2026-07-18T08:30:00",
     "isAllDay": False, "response": "organizer",
     "requiredAttendees": "", "optionalAttendees": ""},
    {"subject": "Tomorrow Q3 Roadmap Review", "start": "2026-07-18T09:30:00",
     "isAllDay": False, "response": "accepted",
     "requiredAttendees": "a@work.com;b@work.com", "optionalAttendees": ""},
    {"subject": "Tomorrow Company Holiday", "start": "2026-07-18T00:00:00",
     "isAllDay": True, "response": "notResponded",
     "requiredAttendees": "", "optionalAttendees": ""},
    {"subject": "Tomorrow tentative sync", "start": "2026-07-18T11:00:00",
     "isAllDay": False, "response": "tentativelyAccepted",
     "requiredAttendees": "", "optionalAttendees": "z@work.com"},
]


def make_email_bytes(agenda):
    """Build a raw RFC822 email the way Power Automate sends it:
    HTML body with the JSON entity-escaped."""
    body_json = json.dumps(agenda)
    html_body = ("<html><body><p>" + html_mod.escape(body_json)
                 + "</p></body></html>")
    raw = ("From: Sandy Work <work.sender@example.com-domain.com>\r\n"
           "To: you@example.com\r\n"
           "Subject: HECKNET-AGENDA-PM 2026-07-17\r\n"
           "MIME-Version: 1.0\r\n"
           'Content-Type: text/html; charset="utf-8"\r\n'
           "\r\n" + html_body + "\r\n")
    return raw.encode("utf-8")


class TestExtractAgendaJson(unittest.TestCase):
    def test_roundtrip_through_html_email(self):
        events = er._extract_agenda_json(make_email_bytes(SAMPLE_AGENDA))
        self.assertEqual(events, SAMPLE_AGENDA)

    def test_html_entities_in_subject_survive(self):
        agenda = [{"subject": "R&D <sync> \"kickoff\"",
                   "start": "2026-07-18T10:00:00", "isAllDay": False,
                   "response": "accepted",
                   "requiredAttendees": "a@work.com",
                   "optionalAttendees": ""}]
        events = er._extract_agenda_json(make_email_bytes(agenda))
        self.assertEqual(events[0]["subject"], 'R&D <sync> "kickoff"')

    def test_no_json_raises(self):
        raw = (b"From: x@y.com\r\nSubject: hi\r\n"
               b"Content-Type: text/plain\r\n\r\nno agenda here\r\n")
        with self.assertRaises(Exception):
            er._extract_agenda_json(raw)


class TestPickFirstMeeting(unittest.TestCase):
    def test_picks_earliest_qualifying_on_target_date(self):
        # Today's leftover meeting is a different date -> excluded.
        # Tomorrow: 8:00 declined, 8:30 solo block, all-day -> excluded;
        first = er._pick_first_meeting(SAMPLE_AGENDA, "2026-07-18")
        self.assertEqual(first["subject"], "Tomorrow Q3 Roadmap Review")

    def test_ignores_todays_events(self):
        # "Today's leftover meeting" fully qualifies but is dated today,
        # not tomorrow -- must never be picked by the evening report.
        first = er._pick_first_meeting(SAMPLE_AGENDA[:1], "2026-07-18")
        self.assertIsNone(first)

    def test_tentative_qualifies(self):
        first = er._pick_first_meeting([SAMPLE_AGENDA[5]], "2026-07-18")
        self.assertEqual(first["subject"], "Tomorrow tentative sync")

    def test_all_excluded_returns_none(self):
        self.assertIsNone(er._pick_first_meeting(SAMPLE_AGENDA[1:3], "2026-07-18"))

    def test_empty_list_returns_none(self):
        self.assertIsNone(er._pick_first_meeting([], "2026-07-18"))

    def test_not_responded_qualifies(self):
        event = {"subject": "Skip-level", "start": "2026-07-18T10:00:00",
                 "isAllDay": False, "response": "notResponded",
                 "requiredAttendees": "boss@work.com", "optionalAttendees": ""}
        first = er._pick_first_meeting([event], "2026-07-18")
        self.assertEqual(first["subject"], "Skip-level")

    def test_two_day_agenda_each_script_picks_its_own_day(self):
        # Same agenda list, different target_date -> different pick. This is
        # the exact mechanism Morning and Evening share against one email.
        first_today = er._pick_first_meeting(SAMPLE_AGENDA, "2026-07-17")
        self.assertEqual(first_today["subject"], "Today's leftover meeting")
        first_tomorrow = er._pick_first_meeting(SAMPLE_AGENDA, "2026-07-18")
        self.assertEqual(first_tomorrow["subject"], "Tomorrow Q3 Roadmap Review")


class TestFormatMeetingTime(unittest.TestCase):
    def test_morning_no_leading_zero(self):
        self.assertEqual(er._format_meeting_time("2026-07-18T09:30:00"),
                         "9:30 AM")

    def test_afternoon(self):
        self.assertEqual(er._format_meeting_time("2026-07-18T14:05:00"),
                         "2:05 PM")

    def test_fractional_seconds_tolerated(self):
        self.assertEqual(
            er._format_meeting_time("2026-07-18T09:30:00.0000000"),
            "9:30 AM")


if __name__ == "__main__":
    unittest.main()
