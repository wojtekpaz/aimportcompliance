# Deploying AImport (hosted, so survey links reach clients)

The client survey (`/survey/{token}`) is emailed to an importer who opens it on
their own device. That only works if AImport runs on a **public** server with a
real domain — a survey link built from `http://127.0.0.1:8000` is unreachable.
This guide deploys one shared instance to **Railway** (already wired via
`railpack.json`). The broker tools (`/`, `/classify`, `/invoice`, `/products`)
stay behind the PIN gate; only `/survey/*` is public, which is by design.

---

## 1. What the repo already does for you

- `railpack.json` — builds a Python app from `requirements.txt`, installs the
  OCR system packages (`tesseract-ocr`, `tesseract-ocr-pol`, `poppler-utils`),
  and starts it with:
  `uvicorn server.app:app --host 0.0.0.0 --port $PORT`
- `data_taric.sqlite` (the EU tariff DB) is committed, so classification works
  on the deployed box with no extra setup.

## 2. Create the service

1. Push this repo to GitHub (if not already).
2. Railway → **New Project → Deploy from GitHub repo** → pick this repo.
   Railpack auto-detects everything; no Dockerfile needed.

## 3. Environment variables (Railway → Variables)

| Variable | Required | Value / notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | `sk-ant-...` — the classification engine + question simplification need it. |
| `PUBLIC_BASE_URL` | **yes** | `https://<your-domain>` — every survey link is built from this. Set it to the domain Railway gives you (see step 5). No trailing slash. |
| `AIMPORT_DATA_DIR` | **strongly recommended** | `/data` — directory on a persistent volume where survey sessions + saved products live (see step 4). |
| `HOST` / `PORT` | no | Railway injects `$PORT`; the start command already binds `0.0.0.0`. |

## 4. Persistent volume (so survey data survives redeploys)

Survey sessions and saved products are stored in `saved_products.sqlite`. By
default that file is on the container's **ephemeral** disk and is wiped on every
redeploy. To keep it:

1. Railway → **Add Volume**, mount path `/data`.
2. Set `AIMPORT_DATA_DIR=/data`.

The app creates a fresh DB on the volume on first boot (the committed
`saved_products.sqlite` at the repo root is dev data and is ignored once
`AIMPORT_DATA_DIR` points elsewhere).

## 5. Domain + PUBLIC_BASE_URL (one chicken-and-egg step)

Railway assigns a domain like `aimport-production.up.railway.app` (or attach a
custom domain such as `app.aimport.co`). There's a small ordering quirk:

1. Deploy once (it builds fine without `PUBLIC_BASE_URL`).
2. Copy the assigned domain.
3. Set `PUBLIC_BASE_URL=https://that-domain` → redeploy.

From then on every emailed survey link is `https://that-domain/survey/{token}`.

---

## 6. Verify it works

1. Open `https://<domain>/` → enter the PIN (`0660`) → broker tools load.
2. Go to **Invoice scan**, upload an invoice with at least one ambiguous line
   (e.g. a product with no HS code and a vague description). When a line freezes,
   the **clarification banner** appears.
3. Click **Wyślij e-mail do klienta / Open email to client** → confirm the link
   in the email body is `https://<domain>/survey/...` (not `127.0.0.1`).
4. Open that link in a **private/incognito window** (no PIN, like a real client)
   → the Polish survey loads, you can submit, and the broker results view shows
   the resolved code.

---

## 7. Things to know before real multi-broker use

- **No broker authentication yet.** Every survey is attributed to a single
  `broker-local` id and the PIN (`0660`, `ACCESS_PIN` in `index.html`) is a
  velvet rope, not security. Add real broker logins + per-broker scoping before
  multiple firms share the instance. Change the PIN at minimum.
- **The API key now lives on a shared server**, not a laptop — rotate it if it
  was ever exposed locally, and keep it only in Railway Variables.
- **EBTI rulings DB (`bti.sqlite`, ~107 MB) is not committed** (gitignored). The
  "comparable BTI rulings" feature degrades gracefully to empty without it. To
  enable it in production, upload `bti.sqlite` to the volume, or commit it.
- **Survey language defaults to Polish** (`survey_sessions.language='pl'`).
- **Repo size:** `data_taric.sqlite` (~53 MB) is committed; clones/builds are
  a bit heavy but Railway handles it.

---

## 8. Run it on a plain VPS instead (alternative)

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-pol poppler-utils
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export PUBLIC_BASE_URL=https://your-domain
export AIMPORT_DATA_DIR=/var/lib/aimport      # persistent path
uvicorn server.app:app --host 0.0.0.0 --port 8000
```
Put it behind nginx/Caddy for TLS, point the domain at it, done.
