# Deploy: Vercel (UI + BYOK) + Render (API)

## Overview

- **Vercel** hosts the static Vite build. Users paste **ElevenLabs** and **FAL** keys in the browser; keys are stored in `localStorage` and sent as headers on each request (`X-Eleven-Api-Key`, `X-Fal-Key`).
- **Render** runs the FastAPI backend in **Docker** (includes **ffmpeg**). Ephemeral disk holds `backend/projects` for the lifetime of the instance.

## 1) Render (backend)

1. Create a **Web Service** → **Build from Dockerfile** (connect this Git repo).
2. Set **Dockerfile path**: `Dockerfile`  
   **Docker context**: `.` (repository root)
3. **Instance type**: Fabric/video jobs can take several minutes; use a plan that allows long HTTP requests (free tier may time out on heavy renders).
4. **Environment variables** (minimum):

   | Variable | Purpose |
   |----------|---------|
   | `CORS_ORIGINS` | Your Vercel URL(s), comma-separated, e.g. `https://my-app.vercel.app` |
   | `CORS_ORIGIN_REGEX` | Optional. Default in `render.yaml` allows `https://*.vercel.app` for previews. |

   Optional (for your own testing without BYOK): `ELEVEN_API_KEY`, `FAL_KEY`.

5. Deploy and copy the service URL, e.g. `https://vibe-lipsync-api.onrender.com`.

6. Smoke test: open `https://YOUR-SERVICE.onrender.com/api/health` in a browser.

### Render: use Docker if you can

If error paths look like `/opt/render/project/src/tools/...`, you are on **native Python**, not Docker. Native builds only install what `pip` sees—often just a root `requirements.txt`. This repo now has a **root** `requirements.txt` that pulls in `backend/requirements.txt` (including **Pillow** and **fal-client**).

- **Recommended:** **Web Service → Docker** with this repo’s `Dockerfile` (includes **ffmpeg** and all Python deps).
- **Native Python:** Set **Build Command** to `pip install -r requirements.txt` (repo root), **Start Command** to run uvicorn from `backend`, then **Clear build cache & deploy**.

## 2) Vercel (frontend)

1. **New Project** → import the same repo.
2. **Root Directory**: `ui` (important: `vercel.json` and `package.json` live there).
3. **Environment variables** (Production + Preview):

   | Name | Value |
   |------|--------|
   | `VITE_API_URL` | `https://YOUR-SERVICE.onrender.com` (no trailing slash) |

4. Deploy. Open your Vercel URL, enter API keys under **Your API keys (BYOK)**, click **Save keys & reload voices**, then run the workflow.

## 3) Custom domain

Add your domain in Vercel, then append the same origin to Render:

`CORS_ORIGINS=https://my-app.vercel.app,https://www.my-domain.com`

## 4) Local development

Unchanged: `npm run dev` from repo root, keys in `backend/.env` or BYOK panel.

## Headers (reference)

- `X-Eleven-Api-Key`: ElevenLabs `xi-api-key`
- `X-Fal-Key`: FAL API key (also accepts server `FAL_KEY` / `FAL_API_KEY`)

Server env vars still override when headers are omitted (typical for local `.env`).
