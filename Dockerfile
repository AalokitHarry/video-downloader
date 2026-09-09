FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git curl gnupg unzip \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Deno: the JS runtime yt-dlp's YouTube extractor needs to solve signature/EJS
# challenges (https://github.com/yt-dlp/yt-dlp/wiki/EJS). Without it, yt-dlp
# drops the "web" client from its default client list entirely -- which is
# the only client our PO token provider above generates tokens for -- and
# falls back to clients (e.g. visionos) that get blocked outright on cloud
# IPs. DENO_INSTALL=/usr/local puts the binary straight on PATH.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# PO token provider server (https://github.com/Brainicism/bgutil-ytdlp-pot-provider):
# generates the token yt-dlp's YouTube extractor needs to avoid "Sign in to
# confirm you're not a bot" -- something cloud/datacenter IPs (like this
# container's) hit far more than residential connections, confirmed against
# this exact deployment. Runs locally on 127.0.0.1:4416; the matching PyPI
# plugin (requirements.txt) finds it there automatically, no extractor-args
# needed. Version pinned here to match the plugin version exactly, per the
# project's own compatibility guidance -- unlike yt-dlp itself, which we
# deliberately leave unpinned elsewhere for extractor freshness.
RUN git clone --single-branch --branch 1.3.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil-provider \
    && cd /opt/bgutil-provider/server \
    && npm ci \
    && npx tsc

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

RUN chmod +x start.sh
CMD ["./start.sh"]
