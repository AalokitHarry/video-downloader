FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

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
