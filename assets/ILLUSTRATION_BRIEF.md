# AImport — Illustration Brief

> §9 of the redesign brief. These illustrations are **commissioned/generated artwork
> the founder supplies.** Claude Code did **not** fabricate or generate them. The site
> ships with labelled placeholder slots (token-coloured blocks with a label) at the exact
> aspect ratios below; finished art drops into those slots with no code changes.

## How the slots work
Each slot in `index.html` looks like:
```html
<!-- ILLUSTRATION: hero — see assets/ILLUSTRATION_BRIEF.md#hero
     prompt: <base> <hero ending> -->
<figure class="illus-slot" data-ratio="4/3" aria-hidden="true">
  <span class="illus-slot__tag">Illustration · hero (4:3)</span>
</figure>
```
To install real art, replace the inner `<span>` with:
```html
<img src="/static/illustrations/hero.webp" alt="" loading="lazy">
```
Keep the `data-ratio` so layout stays stable (no shift).

## Target style — "precision logistics surrealism"
Soft, semi-realistic 3D. Muted palette: customs blue (`#1E4F8A`), warm stone/canvas
(`#F7F5EF` / `#E7E3DA`), restrained amber accent (`#D8A342`), sage green (`#6E8F75`).
Generous negative space. Calm, precise, premium, enterprise. **No people. No cartoon style.
No text or numbers baked into the image. No cold neon / generic-AI-startup gradient.**
Matte materials, soft directional light, gentle shadows, a sense of ordered flow.

## Prompt base (prepend to every scene)
```
Precision logistics surrealism. Soft semi-realistic 3D render, matte materials,
soft directional studio light, gentle long shadows. Muted palette: customs blue,
warm stone and off-white, a single restrained amber accent, occasional sage green.
Lots of negative space, centered calm composition, premium enterprise feel.
No people, no text, no numbers, no logos, no cartoon styling, no neon.
Scene:
```

## Per-scene endings

### #hero  — aspect 4:3 (≈1200×900)
```
a single clean amber thread tracing an ordered path through a calm field of floating
muted-blue customs containers and document planes, resolving into one highlighted node
— a clear path through complexity. Quiet, confident, mostly empty space.
```

### #classification  — aspect 1:1 (≈900×900)
```
one matte stone product form on a soft pedestal, with thin blue connective lines
branching upward through a minimal tree of floating planes to a single highlighted
tier — the act of resolving one object to one place in a hierarchy.
```

### #audit-trail  — aspect 16:9 (≈1280×720)
```
a horizontal sequence of five soft 3D checkpoint nodes connected by one continuous
amber line, left to right, each node a slightly different muted form (cube, document,
seal, decision marker, export tray). Ordered, inevitable, calm.
```

### #duty-optimization  — aspect 1:1 (≈900×900)
```
two balanced matte pans of a minimal floating scale holding muted-blue coin-discs,
one pan a touch lower, a thin amber indicator line — defensible trade-offs weighed
precisely. Soft, quiet, no chaos.
```

### #cbam  — aspect 1:1 (≈900×900)  [hold — module not marketed live]
```
a single sage-green carbon-molecule form resolving out of soft blue industrial haze
into a clean measured shape on a pedestal — emissions made measurable and orderly.
```
*Note: CBAM Readiness is currently omitted from the live module set (founder decision).
This scene is briefed for future use; do not place it on the marketing page until the
module ships.*

## Delivery
- Format: `webp` (preferred) or high-quality `png`, transparent or canvas-coloured background.
- Place in `static/illustrations/` using the slot id as filename (`hero.webp`, etc.).
- Provide @1x and @2x if possible; the slot is responsive.
