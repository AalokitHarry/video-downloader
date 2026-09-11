#!/bin/sh
set -e

# PO token provider, backgrounded -- yt-dlp's bgutil plugin talks to it on
# 127.0.0.1:4416 automatically. If it dies, YouTube downloads just fall back
# to failing with the bot-check error again; it doesn't take the app down.
node /opt/bgutil-provider/server/build/main.js &

exec waitress-serve --host=0.0.0.0 --port="${PORT}" app:app
