# Phase 0 findings (2026-10-24)

## Root causes found in code
- "فشل التحقق" without reason: every adapter's validate() swallows the exception and returns False; server shows a fixed text.
- Home shows legacy dashboard below the composer: page-dash ships with class "page on".
- Whole palette uses light-dark()+oklch+color-mix (Chrome 111-123+). Older Android System WebView drops every color, so the C-fan look collapses.
- Fonts come only from Google CDN: offline start falls back to system font.

## External facts (verified from docs)
- Instagram API with Instagram Login: professional accounts (Creator/Business), no Facebook Page needed, host graph.instagram.com, scopes instagram_business_basic + instagram_business_content_publish. Standard Access is enough for accounts you own and add in the App Dashboard.
- Media must be a public URL (JPEG only for images); binary/resumable upload is not available for Instagram Login. Limit ~100 API posts / 24h (doc also says 50 for carousels).
- Long-lived token: 60 days; refresh with GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token when token is at least 24h old and not expired.
- Facebook Graph API cannot publish to personal profiles (Pages only since v3.0). Chosen: semi-automatic share.
- Gemini images: Interactions API, models gemini-3.1-flash-image / gemini-3.1-flash-lite-image / gemini-3-pro-image, response_format {type:image}; output in steps[model_output].content[type=image].data (base64).
- Gemini video: Veo shut down 2026-10-22; replacement gemini-omni-1.1-flash via Interactions with response_format {type:video, aspect_ratio 9:16|16:9, resolution}; inline base64 or delivery:"uri".
- Oxion: OpenAI-compatible https://oxionai.com/v1, Bearer cc_ key, 33 chat models (no image/video), capabilities from GET /models (reasoning, vision, tools, streaming, json_mode, web_search), pricing per 1k tokens.
- web-to-app README lists persistent notifications and a URL-polling foreground service but does not document them for Python projects nor a JS bridge API. Not relied on; battery exemption guide + heartbeat + catch-up on open instead.

## Not verifiable from here
- Live Oxion /models response and Arabic quality per model.
- Whether the Meta dashboard "Generate token" button returns a long-lived token (the app refreshes weekly either way).
- raw.githubusercontent.com content-type for video (images are served as image/jpeg).
