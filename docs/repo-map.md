# repo-map

Keep under 5KB. No code. Update by 2 lines after every change.

## Files
- app.py: entry (WebToApp); init_db, scheduler.start, Flask on 127.0.0.1:$PORT
- config.py: APP_VERSION, rate limits, persona, Gemini/Thaura defaults
- version.json: remote update check (must equal APP_VERSION)
- core/server.py: legacy API + registers v2/replies blueprints + PWA files whitelist
- core/routes_v2.py: /api/groups, group-targets retry, ai/generate, rules, health, platforms
- core/routes_replies.py: /api/reply-rules, reply-log, matcher test
- core/database.py: SQLite schema + all queries (groups, targets, rules, replies, settings)
- core/publisher.py: claim-before-send, error classes, backoff, missed grace, cleanup
- core/auto_rules.py: daily rule slots -> Gemini -> group (draft or scheduled)
- core/auto_reply.py + keywords.py: comment polling, Arabic matcher, public reply + DM
- core/gemini.py: Interactions API (grounded news) with generateContent fallback
- core/media.py, net.py, accounts.py (Fernet), scheduler.py, interaction.py, ai_engine.py (legacy)
- platforms/: base.py + instagram, facebook, x_twitter, youtube, telegram, bluesky, threads
- static/: index.html (whole UI), manifest.webmanifest, sw.js, icon.svg
- tests/: 50 unittest tests (python -m unittest discover -s tests); UI self-test at /#test
- docs/prototypes/: A/B/C static directions (C chosen)

## Sections (SECTION markers in index.html script)
- CONFIG (head): brand, OKLCH palette, platforms, states, license, updateUrl
- CORE: h(), sig/eff signals, api(), toast, sheets
- STATE, NAV, COMPOSE (fan, dial, AI sheet, submit), DATA
- LEGACY: old pages kept verbatim (dash, accounts, content, queue, settings)
- PLAN, AUTO, BACKUP, SELFTEST, INIT

## Key functions and IDs
- Legacy IDs/functions must stay: page-dash/accounts/content/queue/settings, loadDash, buildQueue, loadAccounts, addAccount, generate, schedulePost, loadQueue, approveQ, rejectQ, saveSettings, uploadMedia, go(p)
- Compose: submit(), renderDial(), renderFan(), openGroup(), selfTest()

## Decisions
- Fan cards + 24h dial (direction C): chosen by owner
- Idempotent publish: claim state before send; stuck publishing -> failed, never auto-resent
- Capabilities per adapter drive validation and UI; platform logic only inside adapters
- DOM built with h() + textContent, never innerHTML for data (legacy pages still use esc())
- Gemini is the one allowed external API besides the platforms (CLAUDE.md)
- Optional libs (instagrapi, google) degrade with a clear Arabic RuntimeError, never at import time
- Business Instagram excluded from groups (needs public URL); stays in legacy quick schedule

## Solved bugs (never reintroduce)
- On-device pip timed out (120s cap, ~240KB/s): requirements.txt must stay light (~7MB); no openai pkg (ai_engine uses requests); instagrapi/google only in requirements-full.txt and imported lazily
- Backoff used stale attempts (3h wait) -> use target["attempts"] before increment
- Day strip duplicated on DST change -> build dates with new Date(y,m,d+i), never +864e5
- Popover sheets stuck hidden without popover support -> attr set from JS only if supported

## Open issues
- Real-account runs untested for all adapters and Gemini; instagrapi on Python 3.14 untested
- Android OEM battery killing needs manual exemption
