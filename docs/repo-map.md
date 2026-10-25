# repo-map

Keep under 5KB. No code. Update by 2 lines after every change.

## Files
- app.py: entry (WebToApp); init_db, deps, scheduler.start, Flask on 127.0.0.1:$PORT
- config.py: APP_VERSION, rate limits, persona, OXION/GEMINI env fallbacks
- version.json: remote update check (must equal APP_VERSION)
- core/server.py: legacy API + accounts (official/share types, PATCH credentials, reasoned validate) + /api/media/<id> + /fonts + settings (secrets encrypted)
- core/routes_v2.py: groups, preflight via adapters, target done/retry, ai (generate/models/test/media), health + reconcile, accounts health/check-all, ig-login/ig-code, relay check, deps check/reinstall, full backup
- core/routes_replies.py: /api/reply-rules, reply-log, matcher test
- core/database.py: SQLite + numbered MIGRATIONS (PRAGMA user_version); secrets; health; delays; per-platform claim
- core/errors.py: classify() -> code/message/hint/retryable; AppError, ManualShare
- core/publisher.py: per-platform workers (core/queues.py), token refresh, classified errors, needs_you, delay log
- core/tokens.py: refresh on need with per-account lock (instagram official, threads)
- core/llm.py: Oxion -> Gemini chain; core/oxion.py: OpenAI-compatible client, live models, recommend
- core/gemini.py: Interactions + generateContent fallback, model discovery; core/media_gen.py: image/video jobs
- core/relay.py: GitHub public repo media relay for Instagram official
- core/backup.py: full encrypted backup (PBKDF2 + Fernet)
- core/deps.py: background pip into DATA_DIR/pylibs (full set every swap), check_each, targeted reinstall
- core/scheduler.py: UTC interval tick + heartbeat (hb_at) + tick lock + hourly token refresh
- core/auto_rules.py, auto_reply.py, keywords.py, media.py, net.py, accounts.py (Fernet), interaction.py, ai_engine.py (chain)
- platforms/: base.py (probe/check/preflight/refresh) + instagram (official/personal/business) facebook (share/page) x youtube telegram bluesky threads
- static/: index.html (whole UI), fonts/ (local woff2), manifest, sw.js, icon.svg
- tests/: 75 unittest tests; UI self-test at /#test
- docs/prototypes/ (C chosen), docs/phase0-findings.md

## Sections (SECTION markers in index.html script)
- CONFIG (head, + palette fallback oklchRgb/applyPalette), CORE, STATE, NAV, COMPOSE, DATA, LEGACY, PLAN, AUTO, BACKUP, SELFTEST
- GUIDES, IGLOGIN, CREDS, HEALTH, SHARE, AIMEDIA, SETTINGS2, DEPS, INIT

## Key functions and IDs
- Legacy IDs/functions must stay: page-dash/accounts/content/queue/settings, set-key (now Oxion key), loadDash, buildQueue, loadAccounts, addAccount, generate, schedulePost, loadQueue, approveQ, rejectQ, saveSettings, uploadMedia, go(p)
- Compose: submit(), renderDial(), renderFan(), renderUpnext(), openGroup(), selfTest(); health: loadHealth(), reconcile()

## Decisions
- Fan cards + 24h dial (direction C); local fonts; light-dark()/oklch with JS fallback for old WebView
- Idempotent publish: claim before send; stuck publishing -> failed (busy platforms excluded)
- Capabilities + preflight per adapter; platform logic only inside adapters
- AI: Oxion primary, Gemini fallback and media; Thaura removed (old key deleted by migration 4)
- Instagram: official default, personal/session as automatic fallback; Facebook personal = manual share (needs_you)
- Optional libs install at runtime via core/deps.py; never remove them from the product

## Solved bugs (never reintroduce)
- Android server start timeout: scheduler uses datetime.timezone.utc; tzdata in requirements; deps/scheduler errors never block Flask
- On-device pip timeout: requirements.txt light; instagrapi/google only in requirements-full.txt and imported lazily
- Backoff used stale attempts -> use target["attempts"] before increment
- Day strip duplicated on DST change -> new Date(y,m,d+i)
- Popover sheets stuck hidden -> popover attr from JS only if supported
- validate() swallowed errors ("فشل التحقق" without reason) -> probe() raises, check() classifies
- Graph GET sent token in body -> params for GET
- page-dash shipped "on" and showed under home
- deps swap installed only missing libs and dropped the others -> always install the full optional set

## Open issues
- Live accounts, Oxion and Gemini media untested on device; IG official guide rewritten 2026-10-25 from Meta docs (Create App > Other > Business > Instagram > Set up > Generate token)
- Home UI still differs from C-fan (no ambient glow/heading, composer above dial); plan awaiting approval
- Android OEM battery killing needs manual exemption (guide in Health)
