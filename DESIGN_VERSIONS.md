# Design Versions — read this first

This project has had **two** front-end designs. To avoid the confusion that prompted
this note, they are now cleanly separated by git. Do not mix them.

## ✅ ACTIVE — Original "Dark / Turquoise" design  (branch: `main`)
The design we are building and rebuilding step by step.

- **Look:** black/near-black backgrounds (`#1a1a1f`, `#04201e`, `#1c1c26`) with a
  turquoise accent (`#2dd4bf`, `#00d4c8`, `#3ee6d2`).
- **Where:** the working tree on `main`. CSS is inline `<style>` per HTML file.
- **Pages:** `index.html` (landing + PIN gate), `landing.html`,
  `server/classify.html`, `server/products.html`, `server/invoice.html`,
  `server/optimize.html`.
- **This is the source of truth going forward.**

## 🗄️ ARCHIVED — "Light / Cream rebrand" (brand-v2)  — DO NOT build on this
A later rebranding attempt to a light, cream/customs-blue system. Archived because it
caused confusion and was set aside. Preserved, not deleted.

- **Look:** cream canvas (`#F7F5EF`), customs-blue brand (`#1E4F8A`); tokenised CSS in
  `static/css/` (`design-tokens.css`, `base.css`, `marketing.css`), self-hosted fonts.
- **Where:** git branch `redesign/brand-v2` and tag `archive/light-rebrand-v2`
  (both pushed to `origin`). It is `main` + 10 rebrand commits.
- **To inspect it:** `git checkout archive/light-rebrand-v2` (then `git checkout main` to return).
- **Do not** reintroduce its files (`static/css/*`, `legacy/`, `REDESIGN_PLAN.md`,
  `DO_NOT_TOUCH.md`) into `main` unless explicitly asked.

---
_Separation performed 2026-06-22. Reverted from the light rebrand back to the original
dark+turquoise design to rebuild it step by step._
