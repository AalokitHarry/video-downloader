FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Deno: the JS runtime yt-dlp's YouTube extractor needs to solve signature
# challenges when resolving format URLs (https://github.com/yt-dlp/yt-dlp/wiki/EJS).
# Without it, YouTube extraction still runs (now that cookies get past the
# bot-check) but returns zero usable formats -- confirmed directly against
# this deployment ("No video formats found!"), not assumed.
# DENO_INSTALL=/usr/local puts the binary straight on PATH.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

RUN chmod +x start.sh
CMD ["./start.sh"]
