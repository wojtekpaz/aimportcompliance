# AImport — Design System & Constraints
**For Claude Code: read this file in full before touching any HTML, CSS, or layout.**

---

## Theme

**White is the primary color.** The site uses a warm off-white background with liquid glass surface treatment. It is light, clean, and enterprise-grade — not dark, not colorful, not warm-beige.

The structural reference is `raft.ai`: copy its layout discipline, typographic scale, and restraint. Do not copy its dark palette.

Do not reference or imitate: generic SaaS templates, Tailwind UI demos, shadcn examples, or any design that could belong to a different product.

---

## Color tokens

```css
:root {
  --color-bg:        #F8F8FA;              /* off-white page background */
  --color-surface:   #FFFFFF;              /* card / panel backgrounds */
  --color-border:    rgba(0, 0, 0, 0.08); /* subtle dividers */
  --color-text:      #0D0D0D;             /* primary text */
  --color-muted:     #7A7A8A;             /* secondary / metadata text */
  --color-brand:     #2B4EFF;             /* brand blue — three uses only, see below */

  /* Liquid glass */
  --glass-bg:        rgba(255, 255, 255, 0.55);
  --glass-border:    rgba(255, 255, 255, 0.75);
  --glass-shadow:    0 4px 24px rgba(0, 0, 0, 0.07), 0 1px 4px rgba(0, 0, 0, 0.05);
  --glass-blur:      blur(18px) saturate(180%);
}
```

### Brand blue rules — non-negotiable

`#2B4EFF` is used in exactly **three places** only:

1. The primary CTA button background
2. Active nav link indicator
3. One accent element in the hero (a thin rule, an underline, or a small tag label)

Nowhere else. Not in card borders, not in hover states on text, not in icon fills, not in section backgrounds, not as a gradient component.

---

## Liquid glass — where to apply

Glass surfaces apply to contained, bounded elements only. Never to full-width section backgrounds or `<body>`.

| Element | Treatment |
|---|---|
| Nav / header | `background: var(--glass-bg)` + blur + `border-bottom: 1px solid var(--glass-border)` |
| Cards, feature panels, testimonial containers | `background: var(--glass-bg)` + blur + `border: 1px solid var(--glass-border)` + `border-radius: 16px` |
| Stats row wrapper | `background: var(--glass-bg)` + blur + `border: 1px solid var(--glass-border)` + `border-radius: 20px` + `padding: 40px 48px` |

Always pair `backdrop-filter` with `-webkit-backdrop-filter` for Safari.

Glass must NOT be applied to: `<body>`, `.hero`, full-width section wrappers, the footer, or any element that spans the full viewport width without a visible card boundary.

---

## Typography

```
Display / hero headline:  Inter, weight 700–800, size 56–72px, line-height 1.05, letter-spacing -0.03em
Section headlines:        Inter, weight 600, size 32–40px, line-height 1.1, letter-spacing -0.02em
Body text:                Inter, weight 400, size 15–16px, line-height 1.6
Labels / eyebrows:        Inter, weight 500, size 11px, letter-spacing 0.1em, uppercase, color: var(--color-muted)
Metric numbers:           Inter, weight 700, size 48–56px, line-height 1.0
```

Load Inter from Google Fonts. No other typefaces.

The hero headline must be **large enough to dominate the viewport** — if it fits comfortably at the current size, it is too small.

---

## Layout structure

Follow this exact page section order:

```
1. NAV          — logo left, text links center, one CTA button right
2. HERO         — full-width light, large headline, one-line subtext, single CTA
3. LOGO STRIP   — "Zaufali nam" trusted-by marquee, muted client logos
4. STATS ROW    — 3–4 large metric numbers with small labels, glass wrapper
5. FEATURE(S)   — alternating text + product screenshot pairs
6. TESTIMONIALS — plain: quote text, name, title, glass card
7. FINAL CTA    — single centered call to action
8. FOOTER       — minimal: logo, links, copyright
```

Do not add sections not in this list without explicit instruction.

---

## Nav

```
- Logo top-left: bird PNG at height 36px, width auto, no square crop, no background behind it
- Center: text links only, no background, no underline by default, color: var(--color-text)
- Right: one button "Umów demo" — background: var(--color-brand), color: #fff, border-radius: 4px
- No mega-menu, no dropdowns, no icons in nav
- Sticky: background var(--glass-bg), backdrop-filter var(--glass-blur), border-bottom 1px solid var(--glass-border)
```

### Logo — non-negotiable

The logo must be the **bird PNG** already present in the codebase. To find it:
1. Search `index.html` for `<img` tags near the nav/header — the bird PNG is already loaded somewhere on the page
2. Use that exact `src` path — do not invent or guess a path
3. Render it as: `<img src="[found path]" alt="Aimport" height="36" style="width:auto;display:block;">`
4. No `border-radius` on the image or its container. No square clip. No background color behind it.

If the path cannot be found, leave: `<!-- LOGO: insert bird PNG src here -->` and do not substitute anything else.

---

## Hero section

```
- Background: linear-gradient(160deg, #EEF1FF 0%, #F8F8FA 50%, #EAF0FF 100%)
- No dark overlay, no full-bleed photograph, no animated blob, no gradient orb
- Headline: var(--color-text), 2–3 lines max, weight 800, size 64px minimum
- Subtext: var(--color-muted), one sentence, weight 400, size 16px
- CTA: single button "Umów demo", var(--color-brand) background, white text
- No floating card UI mockups, no rotated/angled elements
```

---

## Stats row

```
- 3–4 stats: large number + small label underneath
- Horizontal flex row inside a single glass wrapper panel
- Number color: var(--color-text)
- Label color: var(--color-muted)
- No individual card per stat — one panel, all stats inside
```

---

## Feature sections

```
- Two-column layout: text left, product screenshot right (alternate on second feature)
- Screenshot: framed in a glass panel, border: 1px solid var(--glass-border), border-radius: 12px
- Eyebrow label: uppercase, 11px, var(--color-muted)
- Headline: 32–36px, weight 600, var(--color-text)
- Body: 2–3 sentences max, no bullet lists, no icons alongside text
```

---

## Testimonials

```
- Glass card per testimonial (var(--glass-bg), blur, border, border-radius: 16px)
- Content: quote text, name, title — no decorative large quote marks, no colored backgrounds
- Company logo below attribution if available: filter: grayscale(1), max-height: 24px
- Carousel or stacked — no grid of 3 equal cards side by side
```

---

## Footer

```
- Background: #F0F2F8
- Border-top: 1px solid var(--color-border)
- Text: var(--color-text) primary, var(--color-muted) secondary
- No dark background, no white text
```

---

## Explicit prohibitions

If you find yourself generating any of the following, stop and redesign that element.

- Dark backgrounds (`#0A0A0A`, `#111111`, `#1E1E1E`, or similar) anywhere on the page
- Gradient backgrounds on cards or section wrappers (the hero gradient is the only permitted gradient)
- Colored text inside testimonials for emphasis
- Decorative numbered cards (01 / 02 / 03 in large circles/boxes) unless content is a genuine ordered sequence
- Large metric numbers inside individually bordered cards with icons
- Floating UI mockups in the hero — no angled or rotated card overlays
- Grid of 3 equal feature cards with icons at the top
- `border-radius` above `20px` on any element (nav: 0, cards: 16px, stats panel: 20px, CTA button: 4–6px)
- Exclamation points in any copy
- The words "revolutionary", "cutting-edge", "seamless", "powerful", "robust", "AI-powered" in headings
- Emoji anywhere on the page
- Box shadows heavier than `var(--glass-shadow)`
- Applying `backdrop-filter` to full-width elements or `<body>`

---

## Polish copy register

All customer-facing copy is in Polish. Register: professional customs-broker language — direct, factual, no marketing inflation.

```
Primary CTA:       "Umów demo"
Secondary CTA:     "Zobacz platformę"
Nav sign in:       "Zaloguj się"
Hero subtext:      one factual sentence describing what the product does
Testimonials:      real quotes only, never invented
Logo strip label:  "Zaufali nam"
```

Do not write Polish copy that reads like a translated English marketing headline. When in doubt, write less.

---

## Additive-only rule

When editing existing sections, change only what is explicitly instructed. Do not:
- Rewrite CSS classes not referenced in the instruction
- Change section order without explicit instruction
- Remove existing content to simplify

Every change must leave all working routes and functionality unbroken.

---

## File structure

```
index.html   — main landing page
style.css    — all styles (no inline styles except dynamic JS values)
script.js    — scroll animations, nav behavior only
```

Load Inter from:
`https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap`

No external UI libraries. No Tailwind. No Bootstrap. Vanilla HTML/CSS/JS only.
