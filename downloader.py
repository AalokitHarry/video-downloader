"""yt-dlp wrapper: pure functions, typed errors, no Flask dependency."""
import glob
import html
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import yt_dlp

FFMPEG_PATH = shutil.which("ffmpeg")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Every anonymous approach tried against YouTube failed on this host --
# network-level blocks on cloud IPs (confirmed across two Render regions),
# and even a Cloudflare WARP proxy that got past that just hit a softer
# "sign in to confirm you're not a bot" check instead. Authenticated cookies
# from a real logged-in session are the one thing that check is actually
# checking for, so that's the approach now: yt-dlp gets a cookies.txt file
# via Render's Secret Files (never committed to source -- see README), read
# from COOKIES_FILE below only if it's actually present, so a deployment
# without cookies configured just falls back to failing normally instead of
# crashing.
_YOUTUBE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE
)
_YOUTUBE_COOKIES_FILE = "/etc/secrets/youtube_cookies.txt"

# yt-dlp's Instagram/Twitter extractors hard-fail on photo-only posts --
# confirmed by testing, not assumed -- so photo mode bypasses yt-dlp
# entirely and fetches the same og:image preview tag that link-preview
# crawlers use. Identifying as one of those crawlers is what unlocks it:
# these sites serve Open Graph tags to bots by design, for link previews,
# regardless of any login wall on the full page.
_CRAWLER_UA = "WhatsApp/2.19.81 A"
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE
)

# X/Twitter's syndication endpoint is what their own embed widgets use --
# public and unauthenticated by design, unlike Instagram's deprecated
# oEmbed (checked: it now redirects, requiring a Facebook app token since
# Meta locked it down).
_TWITTER_STATUS_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/\w+/status/(\d+)", re.IGNORECASE
)

# Instagram's oEmbed is dead, but its embed *widget* page (/embed/captioned/)
# is a different, still-active, non-deprecated endpoint -- Instagram
# continues to serve and maintain it specifically so third-party sites can
# embed posts, carousel navigation included. It ships the full carousel as
# a JSON fragment double-escaped inside Meta's internal page-bundle format
# (not a documented API), so this is read with a scoped, targeted regex
# rather than a full parse of that format -- same spirit as the og:image
# extraction, just against a richer field. Confirmed by testing against a
# real 6-slide carousel, not assumed.
_INSTAGRAM_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)", re.IGNORECASE
)
_IG_SIDECAR_ITEM_RE = re.compile(
    r'\\"shortcode\\":\\"([^"\\]+)\\".*?\\"display_url\\":\\"(.*?)\\"'
)

# Pinterest serves no og:image (or any other image tag) to crawlers -- its
# pin pages are client-rendered, confirmed directly, not assumed -- so the
# generic og:image fallback below can't reach it either. Its own PinResource
# API (the same undocumented endpoint yt-dlp's Pinterest extractor calls
# internally for video pins) is public and unauthenticated, and returns a
# sized image ladder up to "orig" for any pin, video or photo.
_PINTEREST_RE = re.compile(
    r"^https?://(?:[\w-]+\.)?pinterest\.[a-z.]+/pin/[\w-]*?(\d+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def _unescape_ig_json_string(s: str) -> str:
    # Values come from JSON double-escaped as a string literal inside
    # Meta's page-bundle format: forward slashes end up as three literal
    # backslashes (measured directly against real responses, not assumed),
    # unicode escapes stay as \uXXXX.
    s = s.replace("\\\\\\/", "/")
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


class DownloadUserError(Exception):
    """A problem caused by the user's input (bad/unsupported URL, private video, etc)."""


class DownloadNetworkError(Exception):
    """A problem reaching the source site."""


class DownloadServerError(Exception):
    """A problem with the server environment (e.g. ffmpeg missing)."""


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url or not _URL_RE.match(url):
        raise DownloadUserError("Please paste a valid video URL.")
    return url


def _categorize_download_error(e: yt_dlp.utils.DownloadError) -> Exception:
    msg = str(e).lower()
    if any(kw in msg for kw in ("unsupported url", "no extractor", "is not a valid url")):
        return DownloadUserError(
            "This link isn't supported. Try YouTube, TikTok, Instagram, X/Twitter, or Facebook."
        )
    if any(
        kw in msg
        for kw in ("no video formats found", "no video could be found", "no video in")
    ):
        return DownloadUserError(
            "Couldn't find a downloadable video in this link — it may be a photo "
            "post, a carousel with no video slide, or a video this site won't "
            "expose without logging in."
        )
    if any(
        kw in msg
        for kw in (
            "private",
            "age",
            "unavailable",
            "removed",
            "not available in your country",
            "sign in",
            "log in",
            "logged-in",
            "logged in",
            "login required",
            "authentication",
            "cookies",
            "empty media response",
        )
    ):
        return DownloadUserError(
            "Couldn't download this video — it may be private, age-restricted, "
            "or unavailable without logging in."
        )
    if any(kw in msg for kw in ("urlopen error", "timed out", "timeout", "connection", "network")):
        return DownloadNetworkError(
            "Network error while fetching the video. Check your connection and try again."
        )
    return DownloadUserError(
        "Couldn't download this video — it may be private, age-restricted, "
        "unsupported, or unavailable."
    )


def download_video(url: str, downloads_dir: str, audio_only: bool = False) -> tuple[str, str]:
    """Download the given URL's media at highest quality, as a single MP4
    (or MP3, if audio_only is set).

    Returns (filepath, tmp_dir). Caller owns cleanup of tmp_dir.
    Raises DownloadUserError / DownloadNetworkError / DownloadServerError.
    """
    if not FFMPEG_PATH:
        raise DownloadServerError(
            "Server is missing ffmpeg, required to merge/convert media. See README."
        )

    url = validate_url(url)
    tmp_dir = tempfile.mkdtemp(dir=downloads_dir)
    outtmpl = os.path.join(tmp_dir, "%(title).150B [%(id)s].%(ext)s")

    if audio_only:
        ydl_opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "socket_timeout": 30,
            "ffmpeg_location": FFMPEG_PATH,
        }
    else:
        ydl_opts = {
            "format": "bv*+ba/b",
            "format_sort": ["res", "ext:mp4:m4a"],
            "merge_output_format": "mp4",
            "noplaylist": True,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "socket_timeout": 30,
            "ffmpeg_location": FFMPEG_PATH,
        }

    if _YOUTUBE_RE.match(url) and os.path.exists(_YOUTUBE_COOKIES_FILE):
        ydl_opts["cookiefile"] = _YOUTUBE_COOKIES_FILE
        # "web" is the client an authenticated cookie jar actually applies
        # to and gives the best format/quality selection -- no PO token
        # provider needed this time since a real logged-in session is the
        # stronger signal the earlier anonymous attempts were missing.
        ydl_opts["extractor_args"] = {"youtube": {"player_client": ["web"]}}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise _categorize_download_error(e) from e
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadServerError(f"Unexpected server error: {e}") from e

    # Each request gets its own tmp_dir and postprocessing always converges
    # on one known extension (mp3 for audio, mp4 for video via
    # merge_output_format), so a glob is simpler and more robust across
    # yt-dlp versions than tracking filepaths through the info dict.
    pattern = "*.mp3" if audio_only else "*.mp4"
    matches = glob.glob(os.path.join(tmp_dir, pattern))
    if not matches:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadServerError("Download finished but the output file was not found.")

    return matches[0], tmp_dir


def _fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _CRAWLER_UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read(max_bytes)
    except urllib.error.URLError as e:
        raise DownloadNetworkError(
            "Network error while fetching the page. Check your connection and try again."
        ) from e
    return data, content_type


_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)
_HEIC_BRANDS = (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc", b"hevx")
_AVIF_BRANDS = (b"avif", b"avis")


def _sniff_image_ext(data: bytes) -> str | None:
    """Identify an image format from its actual bytes, not a (possibly
    wrong, missing, or stale-signed-URL-error-page) Content-Type header --
    confirmed necessary: a mismatched/defaulted extension on non-image
    bytes is exactly what produced a "corrupted" file for a user."""
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    for sig, ext in _IMAGE_SIGNATURES:
        if data.startswith(sig):
            return ext
    if len(data) > 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _HEIC_BRANDS:
            return ".heic"
        if brand in _AVIF_BRANDS:
            return ".avif"
    return None


def _save_image(image_url: str, dest_dir: str, base_name: str) -> str:
    image_bytes, _ = _fetch(image_url, max_bytes=50_000_000)
    ext = _sniff_image_ext(image_bytes)
    if ext is None:
        raise DownloadServerError(
            "The photo link didn't return a recognizable image — the source "
            "site may have changed, or the image link expired."
        )
    filepath = os.path.join(dest_dir, f"{base_name}{ext}")
    with open(filepath, "wb") as f:
        f.write(image_bytes)
    return filepath


def _save_all(urls: list[str], tmp_dir: str) -> tuple[str, str]:
    """Save one or more image URLs into tmp_dir; zip them if more than one."""
    try:
        saved = [_save_image(u, tmp_dir, f"photo_{i + 1}") for i, u in enumerate(urls)]
    except DownloadNetworkError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadServerError(f"Failed to download a photo: {e}") from e

    if len(saved) == 1:
        return saved[0], tmp_dir

    zip_path = os.path.join(tmp_dir, "photos.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for path in saved:
            zf.write(path, arcname=os.path.basename(path))
    return zip_path, tmp_dir


def _download_twitter_photos(tweet_id: str, downloads_dir: str) -> tuple[str, str]:
    api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=1"
    try:
        body, _ = _fetch(api_url, max_bytes=3_000_000)
        data = json.loads(body)
    except DownloadNetworkError:
        raise
    except Exception as e:
        raise DownloadServerError(f"Unexpected server error: {e}") from e

    photo_urls = [
        m["media_url_https"]
        for m in data.get("mediaDetails", [])
        if m.get("type") == "photo" and m.get("media_url_https")
    ]
    if not photo_urls:
        raise DownloadUserError(
            "Couldn't find a photo in this post — it may be a video post, "
            "deleted, or protected."
        )

    tmp_dir = tempfile.mkdtemp(dir=downloads_dir)
    return _save_all(photo_urls, tmp_dir)


def _download_instagram_photos(shortcode: str, downloads_dir: str) -> tuple[str, str] | None:
    """Try Instagram's embed-widget page for the full carousel. Returns None
    (rather than raising) if nothing is found there, so the caller can fall
    back to the generic og:image path instead of hard-failing."""
    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    try:
        page_bytes, _ = _fetch(embed_url, max_bytes=3_000_000)
    except DownloadNetworkError:
        raise
    except Exception:
        return None

    page_html = page_bytes.decode("utf-8", errors="ignore")
    pairs = _IG_SIDECAR_ITEM_RE.findall(page_html)
    if not pairs:
        return None

    seen = set()
    photo_urls = []
    for code, raw_url in pairs:
        url = _unescape_ig_json_string(raw_url)
        # The top-level post object repeats the first slide's shortcode+url
        # ahead of the real sidecar list -- skip that redundant entry
        # (confirmed by testing: its shortcode matches the URL's own).
        if code == shortcode and photo_urls:
            continue
        if url not in seen:
            seen.add(url)
            photo_urls.append(url)

    if not photo_urls:
        return None

    tmp_dir = tempfile.mkdtemp(dir=downloads_dir)
    return _save_all(photo_urls, tmp_dir)


def _download_pinterest_photo(pin_id: str, downloads_dir: str) -> tuple[str, str]:
    options = {"id": pin_id, "field_set_key": "unauth_react_main_pin"}
    api_url = (
        "https://www.pinterest.com/resource/PinResource/get/?data="
        + urllib.parse.quote(json.dumps({"options": options}))
    )
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "X-Pinterest-PWS-Handler": "www/[username].js",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        raise DownloadNetworkError(
            "Network error while fetching the pin. Check your connection and try again."
        ) from e

    try:
        pin_data = json.loads(body)["resource_response"]["data"]
        images = pin_data["images"]
    except Exception as e:
        raise DownloadServerError(f"Unexpected response from Pinterest: {e}") from e

    # "orig" is the unscaled original; every pin (photo or video) carries a
    # full sized-image ladder here regardless, confirmed directly.
    image_url = (images.get("orig") or {}).get("url")
    if not image_url:
        raise DownloadUserError(
            "Couldn't find a photo on this pin — it may have been removed."
        )

    tmp_dir = tempfile.mkdtemp(dir=downloads_dir)
    try:
        filepath = _save_image(image_url, tmp_dir, "photo")
    except DownloadNetworkError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadServerError(f"Failed to download the photo: {e}") from e

    return filepath, tmp_dir


def download_photo(url: str, downloads_dir: str) -> tuple[str, str]:
    """Fetch the photo(s) for the given post URL: all of them for X/Twitter
    (via its public syndication endpoint) and Instagram (via its embed
    widget page), zipped together if there's more than one; the single
    original-resolution image for Pinterest (via its PinResource API).
    Everything else falls back to the single og:image preview, the only
    image other sites expose without logging in (see README "How Photo
    mode works").

    Returns (filepath, tmp_dir). Caller owns cleanup of tmp_dir.
    Raises DownloadUserError / DownloadNetworkError / DownloadServerError.
    """
    url = validate_url(url)

    twitter_match = _TWITTER_STATUS_RE.match(url)
    if twitter_match:
        return _download_twitter_photos(twitter_match.group(1), downloads_dir)

    instagram_match = _INSTAGRAM_RE.match(url)
    if instagram_match:
        result = _download_instagram_photos(instagram_match.group(1), downloads_dir)
        if result is not None:
            return result
        # fall through to the generic og:image path below

    pinterest_match = _PINTEREST_RE.match(url)
    if pinterest_match:
        return _download_pinterest_photo(pinterest_match.group(1), downloads_dir)

    try:
        page_bytes, _ = _fetch(url, max_bytes=3_000_000)
    except DownloadNetworkError:
        raise
    except Exception as e:
        raise DownloadServerError(f"Unexpected server error: {e}") from e

    page_html = page_bytes.decode("utf-8", errors="ignore")
    match = _OG_IMAGE_RE.search(page_html) or _OG_IMAGE_RE_ALT.search(page_html)
    if not match:
        raise DownloadUserError(
            "Couldn't find a photo on this page — it may not have one, or this "
            "site won't show it without logging in."
        )
    image_url = html.unescape(match.group(1))

    tmp_dir = tempfile.mkdtemp(dir=downloads_dir)
    try:
        filepath = _save_image(image_url, tmp_dir, "photo")
    except DownloadNetworkError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise DownloadServerError(f"Failed to download the photo: {e}") from e

    return filepath, tmp_dir
