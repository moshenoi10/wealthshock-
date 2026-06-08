# WealthShock YouTube Shorts Automation

WealthShock is now a full automation system for finance shorts with:

- Trending topic discovery via Google Trends RSS
- Gemini-powered viral scripts with controversy, stats, shock hooks, and CTAs
- Title A/B testing with two separate uploads per video
- Automatic hashtag research for maximum reach
- Dynamic ElevenLabs voiceover with varied pace and emphasis
- Custom thumbnail generation with bold text overlays
- YouTube analytics-based upload timing
- Local A/B test storage and performance tracking

## Requirements

- Python 3.9+
- The packages in `requirements.txt`
- A local `.env` file with your API keys

## Setup

1. Install dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
2. Create `.env` with your keys:
   ```text
   GEMINI_API_KEY=...
   ELEVENLABS_API_KEY=...
   PIXABAY_API_KEY=...
   YOUTUBE_API_KEY=...
   YOUTUBE_CLIENT_ID=...
   YOUTUBE_CLIENT_SECRET=...
   YOUTUBE_REFRESH_TOKEN=...
   ```
3. Run the pipeline:
   ```bash
   python3 main.py
   ```

## Features

- `main.py` now uses real trending data and adaptive YouTube analytics scheduling.
- It generates two viral title variants and uploads both for A/B testing.
- It creates a compelling thumbnail automatically.
- It keeps local A/B test history in `ab_tests.json`.

## Notes

- `.env` is ignored by Git to keep your keys private.
- If your YouTube refresh token has the correct scopes, uploads and analytics work.
