# Project rules (read first, every session)

Owner: Sajed. Reply to him in Arabic. Identifiers, syntax and commits in English.
Output: no greetings or filler. Max 3 bullets of explanation. One-line summary before code.

## EDIT RULES (strict)
- Never rewrite, regenerate or reformat a whole file. Use targeted edits only.
- Before editing: read docs/repo-map.md, then open only the relevant SECTION.
- State which sections you will touch (one line), then edit. Touch nothing else.
- No refactors, renames or style changes unless I ask.
- Preserve every existing feature, element ID, function name and CONFIG key.
- After editing: run the #test route if present, update repo-map.md in 2 lines, commit on a feature branch with a clear message.
- If a change exceeds 150 changed lines, stop and propose a plan first.
- Never delete or change an existing feature without telling me first and getting approval.
- Never reintroduce a bug listed in repo-map.md under "Solved bugs".

## Workflow
- Feature request: plan first, wait for approval, then implement on branch feature/<name>.
- New project: deliver a static prototype (home screen, 3 directions, sample data, no logic) and stop for my choice. Never rebuild the shell after approval.
- Session start: read .claude/session-handoff.md and docs/repo-map.md only. Do not scan the repo.
- Session end: rewrite (not append) .claude/session-handoff.md, max 25 lines: state, decisions, open issues, next step.
- Version: bump APP_VERSION and version.json (vMAJOR.MINOR.PATCH) on substantive changes.

## Stack
- Single-file HTML/CSS/JS + CDN fonts. Python stdlib only; pip deps go to .pypackages (WebToApp Python 3.14).
- Android packaging: github.com/shiaho777/web-to-app. targetSdk 28 max for server runtimes. CORS bypass is on by default for static HTML. Fetch its README before deciding anything that depends on it.
- No external API. Offline first: every network call in try/catch with a graceful fallback.
- Mobile-first, Arabic RTL: direction:rtl, logical CSS properties, mirror directional icons.
- PWA (manifest + service worker) in every project unless excluded.
- Next.js/TypeScript only if I ask explicitly.

## Architecture
- One CONFIG block at the top: brand (name, logo, OKLCH palette, fonts), modules, Firebase, license expiry, update repo. No client-specific strings or colors anywhere else. New client = edit CONFIG only.
- Each module is a schema object (fields, types, filters, KPIs). One generic engine renders forms, lists, KPIs. New module = schema only.
- IndexedDB with versioned migrations (append to MIGRATIONS, never edit old ones). JSON export/import. Signals for state. Web Worker for search/sort. Hidden #test route covering core functions.
- Cloud only when multi-device sync is needed: Firebase Auth + Firestore (one project per client) and Cloudflare Pages/Workers. Firestore Security Rules and role checks are mandatory (see skill firebase-rules). The license check in the client is cosmetic; real enforcement belongs in rules.

## Design and security
- Design work: load skill design-system first.
- Sanitize all input, render with textContent, never innerHTML with data. Web Crypto for sensitive local data. Keep the CSP meta tight and add only the origins actually used.
- Pre-delivery: contrast >= 4.5:1, touch targets >= 44px, visible focus, no emoji, inline SVG icons only.

## Code hygiene
- No comments that reveal AI authorship. No purposeless lines. Comments only when necessary, short Arabic.
- Complete deployable code, no TODOs.
