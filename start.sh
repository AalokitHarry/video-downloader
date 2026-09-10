#!/bin/sh
set -e

# PO token provider, backgrounded -- yt-dlp's bgutil plugin talks to it on
# 127.0.0.1:4416 automatically. If it dies, YouTube downloads just fall back
# to failing with the bot-check error again; it doesn't take the app down.
node /opt/bgutil-provider/server/build/main.js &

# Cloudflare WARP proxy mode, entirely in a backgrounded subshell so a slow
# or failed registration never delays app startup or (thanks to `set -e`
# only applying to this script's own top-level commands, not a backgrounded
# subshell) crashes it. downloader.py only routes YouTube requests through
# 127.0.0.1:40000; if this never comes up, those requests just fail with a
# connection error like any other network hiccup -- everything else is
# unaffected.
(
    mkdir -p /run/dbus
    rm -f /run/dbus/pid
    dbus-daemon --config-file=/usr/share/dbus-1/system.conf

    warp-svc --accept-tos &

    ready=0
    i=0
    while [ "$i" -lt 30 ]; do
        if warp-cli --accept-tos status >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
        i=$((i + 1))
    done

    if [ "$ready" -eq 1 ]; then
        warp-cli --accept-tos registration new || true
        warp-cli --accept-tos mode proxy || true
        warp-cli --accept-tos proxy port 40000 || true
        warp-cli --accept-tos connect || true
    fi
) &

exec waitress-serve --host=0.0.0.0 --port="${PORT}" app:app
