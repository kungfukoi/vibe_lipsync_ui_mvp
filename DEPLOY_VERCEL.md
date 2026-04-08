# Deploy backend on Vercel

This repo now includes a Vercel serverless entrypoint for the FastAPI backend.

## 1) Import the repo in Vercel

- In Vercel, create a new project from this repository.
- Root Directory: keep as repo root (`/`).

## 2) Configure environment variables

Set these in **Project Settings -> Environment Variables**:

- `ELEVEN_API_KEY` (required for TTS/STS)
- `FAL_KEY` (required for LTX render path)
- `CORS_ALLOW_ORIGINS` (optional, comma-separated exact origins)
- `CORS_ALLOW_ORIGIN_REGEX` (optional regex, useful for previews)
- `PROJECTS_DIR` (optional; defaults to `/tmp/lipsync_projects` on Vercel)

Recommended for Vercel previews:

- `CORS_ALLOW_ORIGIN_REGEX=https://.*\\.vercel\\.app`

## 3) Deploy

- Deploy from Vercel UI.
- Health check: `https://<your-project>.vercel.app/api/health`

## Important runtime note

Vercel serverless functions use ephemeral storage. Project files are written to `/tmp` by default on Vercel and are not persistent between cold starts. If you need durable files, store generated artifacts in external object storage.
