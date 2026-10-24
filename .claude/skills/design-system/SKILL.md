---
name: design-system
description: Use for any UI, screen, prototype or visual design work. Derives a distinct visual system per product domain (OKLCH palette, Arabic typography, motion, components).
---

# Design pipeline (before any code)

1. Domain language: what this product's world looks, feels and moves like. Name the audience and the screen's single job.
2. Color: OKLCH tokens in CONFIG (primary, surface, ink, mute, line, accent, danger, ok), each as [light, dark] consumed through light-dark(). Fluid type with clamp(). 4/8px spacing grid.
3. Typography per domain, never reused across unrelated projects. Arabic: IBM Plex Sans Arabic, Readex Pro, Alexandria, Almarai, Noto Kufi Arabic, El Messiri, Reem Kufi, Amiri, Lalezar (display). Pair with a matching Latin face.
4. Component personality: cards, buttons, inputs, nav, empty states must express the domain.

Quality bar: the best-funded flagship in that exact category. Every project visually distinct.

# Screens
- One primary job per screen. Secondary actions live behind progressive disclosure: bottom nav (max 5), bottom sheets, a "more" screen.
- Dashboards: per-role KPIs, sparklines, progress rings, drill-down on tap, meaningful empty and loading states. Charts in inline SVG or canvas, no chart libraries.

# Motion and life
- Home is alive: ambient animated background, staggered entrance with @starting-style, 3D tilt on cards (pointer + deviceorientation).
- Navigation: View Transitions API, fallback to plain swap. Physical cubic-bezier easing.
- Native feel: swipe-back, pull-to-refresh, scroll-snap carousels, Popover API sheets, haptics via navigator.vibrate, skeletons instead of spinners.
- Every interactive element has hover, active and focus states. One signature interaction per app.
- Animate transform and opacity only. Respect prefers-reduced-motion (also disables haptics). Feature-detect every modern API.

# Icons
Inline SVG only, hand-crafted to the visual system. Never emoji, never icon fonts.

# Reject
Repeated palettes, generic SaaS cards, ALL-CAPS eyebrow labels, default purple gradients, default cream plus terracotta, flat lifeless nav.

# Pre-delivery check
Contrast >= 4.5:1, touch targets >= 44px, visible focus, consistent spacing, no emoji.
