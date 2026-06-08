# WealthShock YouTube Shorts Automation

WealthShock is now a full automation system for finance shorts with:

- Trending topic discovery via YouTube Trending videos
- Gemini-powered viral scripts with controversy, stats, shock hooks, and CTAs
- Title A/B testing with two separate uploads per video
- Automatic hashtag research for maximum reach
- gTTS voiceovers with adaptive pacing and expressive pitch, no API key required
- Custom thumbnail generation with bold text overlays
- YouTube analytics-based upload timing
- Render self-healing deployment automation with GitHub Actions
- Local A/B test storage and performance tracking

## Requirements

- Python 3.11+
- The packages in `requirements.txt`
- A local `.env` file with your API keys and deployment secrets
- Optional Render secrets in GitHub Actions for auto-deploy

## Setup

1. Install dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your secrets:
   ```bash
   cp .env.example .env
   ```
3. Set values for:
   ```text
   GEMINI_API_KEY=
   PIXABAY_API_KEY=
   YOUTUBE_API_KEY=
   YOUTUBE_CLIENT_ID=
   YOUTUBE_CLIENT_SECRET=
   YOUTUBE_REFRESH_TOKEN=
   RENDER_API_KEY=
   RENDER_SERVICE_ID=
   ```
4. Run the pipeline locally:
   ```bash
   python3 main.py
   ```

## Production Deploy

- Push code to `main` and GitHub Actions will validate the app.
- If `RENDER_API_KEY` and `RENDER_SERVICE_ID` are configured in repository secrets,
  the deploy workflow will trigger a Render deployment automatically.

## Features

- `main.py` now builds YouTube Shorts from a single static background image plus adaptive gTTS voiceover.
- The pipeline uses streamed downloads and file-based uploads to reduce memory footprint for 512MB Render deployments.
- It generates two viral title variants and uploads both for A/B testing.
- Automatic thumbnails are generated with bold hook text and finance-focused branding.
- GitHub Actions run CI on push and can trigger Render deployments automatically when secrets are configured.
- It keeps local A/B test history in `ab_tests.json`.

## Notes

- `.env` is ignored by Git to keep your keys private.
- If your YouTube refresh token has the correct scopes, uploads and analytics work.
