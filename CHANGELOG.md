# Changelog

Version numbers were reassigned for release (the old scheme bumped the
minor version for bugfixes); the original number is in parentheses. The
notes below are from each script's own version history.

## 2.1.0  (originally 1.8)

Adds FIRST MEETING section showing tomorrow's first meeting (same work Outlook
calendar / Power Automate / Gmail pipeline as the Morning report). The flow now
sends a 2-day window in one email; this script reads the SAME email as Morning
(same subject, dated today) but filters events for TOMORROW's date. New
fetch_first_meeting() + config block + _wrap() helper; --meeting-test flag
(console check, no printing) and --preview flag (renders
receipt_preview_evening.png instead of printing).

## 2.0.0  (originally 1.7)

Removed r/aww / cat-subreddit image fetch and rendering entirely (Reddit's public
JSON endpoint has been blocking these requests with 403s for a while and the
feature was dead weight). Receipt now ends after the "Night Night!" line.

## 1.3.2  (originally 1.6)

Fixed UnicodeEncodeError when piped through HeckNet Manager (Windows cp1252 can't
encode emoji). Added sys.stdout.reconfigure(UTF-8) and -u unbuffered flag support
so poll output streams in real time.

## 1.3.1  (originally 1.5)

Fixed Reddit 403 block by switching to a browser-like User-Agent.

## 1.3.0  (originally 1.4)

Added retry loop for weather fetch (handles 502 / DNS errors).

## 1.2.1  (originally 1.3)

Changed weather slots to 9AM / 12PM / 6PM tomorrow.

## 1.2.0  (originally 1.2)

Added fallback subreddit list (SleepingCats -> cats -> aww).

## 1.1.0  (originally 1.1)

Added Bayer dithering (numpy). Fixed missing imports.

## 1.0.0  (originally 1.0)

First version. Weather slots: 12AM / 8AM / 12PM tomorrow.

