# session-handoff

## State (v1.1.1)
- Scheduler v2 complete on branch feature/scheduler-v2, merged to main: multi-platform groups, video, Gemini, rules, IG replies, 7 adapters, UI direction C with 24h dial.
- 50 unit tests pass; UI self-test at /#test passes; swipe-to-schedule verified in headless Chromium (390x844).

## Decisions
- Idempotent claim-before-send; failed never auto-resent; missed grace 12h.
- Gemini default model gemini-3.8-flash, configurable in settings.
- Legacy pages kept under "المزيد" with original IDs/functions.

## Open issues
- Nothing tested against live accounts or live Gemini.
- instagrapi install on WebToApp Python 3.14 unverified.
- OEM background killing: user must exempt the app from battery optimization.

- v1.1.1: slim requirements after on-device pip timeout; full set in requirements-full.txt.

## Next step
- Owner: package with web-to-app, add Telegram or Bluesky first, schedule a test post 5 min ahead, then add the rest.
