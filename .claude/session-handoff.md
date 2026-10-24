# session-handoff

## State (v1.3.0, branch feature/v1.3-roadmap merged to main)
- Roadmap phases 0-7 done: classified errors + reasons in UI, unified adapter interface, per-platform workers, token refresh, Instagram official (+relay) with personal/session fallback and in-app code entry, Facebook personal manual share, Oxion->Gemini chain with live model picks, Gemini image/video, Thaura removed, health page (heartbeat, catch-up, delays, accounts, libs reinstall), full encrypted backup, local fonts, old-WebView color fallback, upcoming list and C-fan motion.
- 75 unit tests pass; /#test passes in headless Chromium (dark, light, legacy CSS emulation).

## Decisions
- Official Instagram is default; media needs public URL -> GitHub relay (owner's token).
- Facebook personal cannot be automated (Meta policy) -> needs_you + share sheet.
- Veo shut down 2026-10-22 -> gemini-omni-1.1-flash for video.

## Open issues
- Not yet run against live accounts/keys on the phone.
- web-to-app persistent notification for Python projects undocumented; rely on battery exemption + catch-up.

## Next step
- Owner: rebuild APK, add Oxion + Gemini keys, test Telegram, then Instagram official (token + relay), then share flow on Facebook.
