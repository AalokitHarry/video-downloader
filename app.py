import mimetypes
import os
import shutil
import threading

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from downloader import (
    FFMPEG_PATH,
    DownloadNetworkError,
    DownloadServerError,
    DownloadUserError,
    download_photo,
    download_video,
    validate_url,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")

# The site is reachable at both this custom domain and the Render-assigned
# *.onrender.com host. Without a canonical redirect, search engines can index
# both as separate pages with identical content, splitting ranking signals
# between them. 308 (not 301) so any non-GET request (e.g. /api/download)
# keeps its method and body across the redirect.
CANONICAL_HOST = "aiod.online"

app = Flask(__name__)
download_lock = threading.Lock()


@app.before_request
def _redirect_to_canonical_host():
    host = request.host.split(":")[0]
    if host in (CANONICAL_HOST, "localhost", "127.0.0.1"):
        return None
    target = f"https://{CANONICAL_HOST}{request.full_path if request.query_string else request.path}"
    return redirect(target, code=308)


class _CleanupOnClose:
    """Proxies an iterable/file wrapper, deleting tmp_dir once it's closed."""

    def __init__(self, wrapped, tmp_dir):
        self._wrapped = wrapped
        self._tmp_dir = tmp_dir

    def __iter__(self):
        return iter(self._wrapped)

    def close(self):
        try:
            close = getattr(self._wrapped, "close", None)
            if close is not None:
                close()
        finally:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)


def _reset_downloads_dir():
    if os.path.isdir(DOWNLOADS_DIR):
        for name in os.listdir(DOWNLOADS_DIR):
            if name == ".gitkeep":
                continue
            path = os.path.join(DOWNLOADS_DIR, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
    else:
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)


_reset_downloads_dir()
if not FFMPEG_PATH:
    print("WARNING: ffmpeg not found on PATH. Downloads requiring merge will fail.")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/robots.txt")
def robots_txt():
    body = f"User-agent: *\nAllow: /\n\nSitemap: https://{CANONICAL_HOST}/sitemap.xml\n"
    return body, 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = [
        (url_for("index"), "1.0", "weekly"),
        (url_for("terms"), "0.3", "monthly"),
        (url_for("privacy"), "0.3", "monthly"),
    ]
    urls = "\n".join(
        f"  <url><loc>https://{CANONICAL_HOST}{path}</loc>"
        f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        for path, priority, freq in pages
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return body, 200, {"Content-Type": "application/xml"}


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "")
    fmt = data.get("format", "video")

    try:
        url = validate_url(url)
    except DownloadUserError as e:
        return jsonify(error=str(e)), 400

    if not download_lock.acquire(blocking=False):
        return jsonify(error="A download is already in progress — please wait for it to finish."), 429

    try:
        try:
            if fmt == "photo":
                filepath, tmp_dir = download_photo(url, DOWNLOADS_DIR)
            else:
                filepath, tmp_dir = download_video(url, DOWNLOADS_DIR, audio_only=(fmt == "mp3"))
        except DownloadUserError as e:
            return jsonify(error=str(e)), 422
        except DownloadNetworkError as e:
            return jsonify(error=str(e)), 502
        except DownloadServerError as e:
            return jsonify(error=str(e)), 500
    finally:
        download_lock.release()

    filename = os.path.basename(filepath)
    if fmt == "photo":
        mimetype = mimetypes.guess_type(filepath)[0] or "image/jpeg"
    else:
        mimetype = "audio/mpeg" if fmt == "mp3" else "video/mp4"
    resp = send_file(filepath, as_attachment=True, download_name=filename, mimetype=mimetype)
    # send_file uses direct_passthrough, which makes Werkzeug return the raw
    # file iterable to the WSGI server without ever calling Response.close()
    # (see get_app_iter) -- so call_on_close callbacks never fire. Wrap the
    # file iterable itself instead; the WSGI server is required to call
    # .close() on whatever it gets back once the body is fully sent.
    resp.response = _CleanupOnClose(resp.response, tmp_dir)
    return resp


if __name__ == "__main__":
    # Local development only -- binds to localhost, not reachable from the
    # network. For hosting, use the Procfile/Dockerfile entrypoint (waitress),
    # which binds 0.0.0.0 explicitly. See README "Hosting" section.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
