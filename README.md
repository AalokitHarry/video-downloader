# Video Downloader

A local web app: paste a link, choose Video, MP3, or Photo, click Download,
get the highest-quality file saved to your machine. No ads, no watermarks,
no accounts.

Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp), which supports
YouTube, TikTok, Instagram, Pinterest, Reddit, X/Twitter, Facebook, and
hundreds of other sites. YouTube needs a bit more explanation -- see
"YouTube" below.

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

Instagram and X/Twitter are hit-or-miss: both increasingly gate video
playback behind a login even for their own website, not just for tools like
this one — if a link from either fails with "may be private, age-restricted,
or unavailable," that's almost always why, not a bug. This tool intentionally
doesn't support logging in with credentials/cookies to keep things simple and
account-free.

TikTok is fine on most networks, but it's been under an ISP-level block in
some countries (India since 2020, still in effect as of 2026) — if every
TikTok link fails on an otherwise-working connection, that's a network-level
block, not something this app (or a yt-dlp update) can fix.

Facebook works for public videos and Watch links the same way as the others.

Pinterest and Reddit both work reliably without logging in — video pins/posts
download through yt-dlp same as everything else, and photo pins/posts through
Photo mode (see below).

## YouTube

Every *anonymous* approach failed on this host, tested extensively, not
assumed: a plain request gets network-blocked (`429`/`403`) before any
client-selection or bot-check logic even runs, identical across two
different Render regions, so it's cloud/datacenter IPs in general, not one
region's range. Routing traffic through a Cloudflare WARP proxy got past
that network block, but then hit YouTube's session-level "Sign in to
confirm you're not a bot" check instead — and running WARP as a third
background process crashed the container under real load on a free-tier
instance's limited RAM. Both ruled out.

What's left, and what's wired up now: real authenticated cookies from a
logged-in YouTube session, read from `cookiefile` (see `_YOUTUBE_COOKIES_FILE`
in `downloader.py`). A valid session is the actual signal that "sign in to
confirm you're not a bot" check wants, independent of IP reputation, so it
should succeed where every anonymous attempt failed. The cookies file is
never committed to source -- it's provided via the host's secret-file
mechanism (e.g. Render's Secret Files, mounted at `/etc/secrets/`) so it
never touches git history or a public repo. If that path doesn't exist
(e.g. running locally without setting one up), YouTube requests just fail
the normal way instead of crashing.

**A real risk to know about**: this ties one actual Google account's
session to the server. Automated-looking traffic at volume could get that
account's YouTube session flagged or restricted by Google -- use a
secondary/throwaway account for this, not a primary personal one. Export
cookies with a browser extension (e.g. "Get cookies.txt LOCALLY") while
logged into youtube.com; sessions expire, so a stale export just fails the
same way as having none configured.

## How Photo mode works

Photo mode intentionally doesn't use yt-dlp. yt-dlp (what Video/MP3 run on)
hard-fails on photo-only posts on both Instagram and X rather than falling
back to the image (e.g. `No video could be found in this tweet`) — confirmed
by testing directly, not assumed, and true even with yt-dlp's own
thumbnail-only download path. So Photo mode bypasses it, with four
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
- **Pinterest**: uses its `PinResource` API (`/resource/PinResource/get/`)
  -- the same undocumented, unauthenticated endpoint Pinterest's own site
  calls client-side, and the one yt-dlp's own Pinterest extractor calls
  internally for video pins. Needed because Pinterest pin pages are fully
  client-rendered and serve no `og:image` (or any other image tag) to a
  plain request -- confirmed directly, not assumed, so the generic fallback
  below can't reach it. The response includes a sized image ladder up to
  `orig` (the unscaled original) for any pin, photo or video.
- **Everything else** (Facebook, Reddit, ...): falls back to the `og:image`
  preview tag, the same one link-preview crawlers (Slack, iMessage,
  WhatsApp) use -- requesting the page while identifying as one of those
  crawlers is what unlocks it, since these sites serve Open Graph tags to
  bots by design, independent of any login wall on the full page. This
  only ever yields *one* image (the preview), so a multi-photo post
  downloads just its cover slide there, at whatever resolution the site
  picked for previews. Instagram also falls back to this if its
  embed-page extraction ever comes up empty.

X and Instagram both zip the result into `photos.zip` when there's more
than one photo; Pinterest only ever returns the one pin image, and a single
photo from any site downloads as one plain file either way.

The line drawn here: `og:image`, X's syndication endpoint, Instagram's
embed-widget page, and Pinterest's `PinResource` API are all things each
site actively serves and maintains for exactly this kind of third-party use
(link previews, embeds, or -- for Pinterest -- their own site's own
rendering). Instagram's *oEmbed* API is different -- Meta deliberately
locked it behind an app token -- so this app doesn't try to route around
that one.

See `download_photo()` in `downloader.py`. All of this depends on each
site continuing to serve these endpoints/tags/formats the way it does
today, which could change without notice -- truer for Instagram's and
Pinterest's undocumented internal formats than for the other two.

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
