# Video Downloader

A local web app: paste a link, choose Video, MP3, or Photo, click Download,
get the highest-quality file saved to your machine. No ads, no watermarks,
no accounts.

Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp), which supports YouTube
(including Shorts), TikTok, Instagram, X/Twitter, Facebook, and hundreds of
other sites.

## Prerequisites

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on your `PATH` (used to merge video/audio into
  a single MP4, and to convert to MP3 for the audio-only option)

## Setup

```bash
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser. Paste a video URL and click
Download.

The server only binds to `127.0.0.1` (not your local network), and only
processes one download at a time.

## Supported sites

YouTube (including Shorts) works reliably anonymously. Instagram and
X/Twitter are hit-or-miss: both increasingly gate video playback behind a
login even for their own website, not just for tools like this one — if a
link from either fails with "may be private, age-restricted, or unavailable,"
that's almost always why, not a bug. This tool intentionally doesn't support
logging in with credentials/cookies to keep things simple and account-free.

TikTok is fine on most networks, but it's been under an ISP-level block in
some countries (India since 2020, still in effect as of 2026) — if every
TikTok link fails while YouTube works fine on the same connection, that's a
network-level block, not something this app (or a yt-dlp update) can fix.

Facebook works for public videos and Watch links the same way as the others.

## How Photo mode works

Photo mode intentionally doesn't use yt-dlp. yt-dlp (what Video/MP3 run on)
hard-fails on photo-only posts on both Instagram and X rather than falling
back to the image (e.g. `No video could be found in this tweet`) — confirmed
by testing directly, not assumed, and true even with yt-dlp's own
thumbnail-only download path. So Photo mode bypasses it, with three
different paths depending on the site:

- **X/Twitter**: uses the public `cdn.syndication.twimg.com` endpoint --
  what X's own embed widgets run on, unauthenticated by design.
- **Instagram**: uses its embed-*widget* page (`/embed/captioned/`), a
  different, still-active endpoint from the deprecated oEmbed API (that one
  now redirects and requires a Meta app token -- checked directly). This
  page ships the full carousel data for the widget's own navigation to
  work, as a JSON fragment double-escaped inside Meta's internal
  page-bundle format. That's not a documented API, so it's read with a
  scoped, targeted regex rather than a full parse of that format -- same
  spirit as the `og:image` extraction below, just against a richer field.
  Verified against a real 6-slide carousel: all 6 came back as distinct,
  valid, higher-resolution images than the `og:image` fallback gives.
- **Everything else** (Facebook, ...): falls back to the `og:image`
  preview tag, the same one link-preview crawlers (Slack, iMessage,
  WhatsApp) use -- requesting the page while identifying as one of those
  crawlers is what unlocks it, since these sites serve Open Graph tags to
  bots by design, independent of any login wall on the full page. This
  only ever yields *one* image (the preview), so a multi-photo post
  downloads just its cover slide there, at whatever resolution the site
  picked for previews. Instagram also falls back to this if its
  embed-page extraction ever comes up empty.

X and Instagram both zip the result into `photos.zip` when there's more
than one photo; a single photo still downloads as one plain file either way.

The line drawn here: `og:image`, X's syndication endpoint, and Instagram's
embed-widget page are all things each site actively serves and maintains
for exactly this kind of third-party use (link previews, embeds). Instagram's
*oEmbed* API is different -- Meta deliberately locked it behind an app
token -- so this app doesn't try to route around that one.

See `download_photo()` in `downloader.py`. All of this depends on each
site continuing to serve these endpoints/tags/formats the way it does
today, which could change without notice -- truer for Instagram's
undocumented internal format than for the other two.

## No length limit, by design

There's no cap on video length or file size — any link is attempted
regardless of duration, including live streams and live replays. Only one
download runs at a time app-wide (see `download_lock` in `app.py`), so an
hours-long video, or a still-running live stream, will tie up the app for
everyone else for as long as it takes. That's a deliberate tradeoff, not an
oversight — if this ever becomes a problem in practice, the fix is a
duration/live-status check before the download starts, or moving off the
single-lock model entirely.

## Hosting

The app is prepped for deployment but not tied to a specific platform yet.

**Docker (recommended — bundles ffmpeg automatically):**
```bash
docker build -t video-downloader .
docker run -p 8080:8080 video-downloader
```
Works as-is on any host that deploys from a `Dockerfile` (Render, Railway,
Fly.io, a VPS, etc).

**Buildpack-based hosts (no Docker):** the included `Procfile` runs the app
via [waitress](https://github.com/Pylons/waitress) (a production WSGI
server, unlike the Flask dev server used for local runs). These hosts still
need ffmpeg available in the build environment — check whether your platform
lets you add an apt package/buildpack for it, or prefer the Docker path
above if it doesn't.

**Before you make it public**, two things to know:
- The dev-server safeguard that binds to `127.0.0.1` only applies to
  `python app.py`. The Procfile/Docker entrypoints intentionally bind
  `0.0.0.0` so the host can reach the app — that's expected, but it means
  the app *is* internet-facing once deployed this way.
- Downloads are still serialized behind a single global lock (see
  `app.py`), so only one visitor's download runs at a time, site-wide.
  That's fine for light personal use; if this gets real traffic, that's the
  first thing to revisit (e.g. a job queue).

## Troubleshooting

- **A specific site suddenly stopped working**: sites change their internal
  APIs often, which breaks yt-dlp's extractors until it's updated. Update it:
  ```bash
  venv\Scripts\Activate.ps1
  pip install -U yt-dlp
  ```
- **"Server is missing ffmpeg"**: install ffmpeg and make sure it's on your
  `PATH` (test with `ffmpeg -version` in a new terminal).
- **A private/age-restricted/removed video fails**: expected — this tool only
  downloads publicly accessible videos.
