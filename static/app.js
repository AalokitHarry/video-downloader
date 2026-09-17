const form = document.getElementById("download-form");
const input = document.getElementById("url-input");
const btn = document.getElementById("download-btn");
const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const cancelBtn = document.getElementById("cancel-btn");
const errorEl = document.getElementById("error");
const modeToggle = document.getElementById("mode-toggle");
const modeButtons = modeToggle.querySelectorAll(".mode-btn");

const LOADING_MESSAGES = {
  video: [
    "Fetching the highest quality…",
    "Talking to the source servers…",
    "Grabbing video and audio streams…",
    "Merging everything into one file…",
    "Bigger videos just take longer — hang tight…",
    "Still working on it…",
  ],
  mp3: [
    "Fetching the highest quality…",
    "Talking to the source servers…",
    "Grabbing the audio stream…",
    "Converting to MP3…",
    "Still working on it…",
  ],
  photo: [
    "Fetching the photo…",
    "Talking to the source servers…",
    "Almost there…",
  ],
};

let elapsedTimer = null;
let successTimer = null;
let mode = "video";
let activeController = null;

modeButtons.forEach((modeBtn) => {
  modeBtn.addEventListener("click", () => {
    mode = modeBtn.dataset.mode;
    modeToggle.dataset.mode = mode;
    modeButtons.forEach((b) => {
      const active = b === modeBtn;
      b.classList.toggle("is-active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    const placeholders = {
      video: "Paste a video link...",
      mp3: "Paste a link to extract audio...",
      photo: "Paste a link to grab the photo...",
    };
    input.placeholder = placeholders[mode];
  });
});

function setLoading(loading) {
  input.disabled = loading;
  btn.disabled = loading;
  btn.classList.toggle("is-loading", loading);

  statusEl.classList.toggle("is-visible", loading);
  if (loading) {
    const messages = LOADING_MESSAGES[mode] || LOADING_MESSAGES.video;
    let seconds = 0;
    let msgIndex = 0;
    statusText.textContent = messages[0];
    elapsedTimer = setInterval(() => {
      seconds += 1;
      if (seconds % 5 === 0) {
        msgIndex = (msgIndex + 1) % messages.length;
      }
      statusText.textContent = `${messages[msgIndex]} (${seconds}s)`;
    }, 1000);
  } else if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

function flashSuccess() {
  btn.classList.add("is-success");
  clearTimeout(successTimer);
  successTimer = setTimeout(() => {
    btn.classList.remove("is-success");
  }, 1600);
}

function showError(message) {
  errorEl.textContent = message;
  errorEl.classList.add("is-visible");
}

function clearError() {
  errorEl.classList.remove("is-visible");
}

function filenameFromContentDisposition(header) {
  const fallbacks = { video: "video.mp4", mp3: "audio.mp3", photo: "photo.jpg" };
  const fallback = fallbacks[mode] || fallbacks.video;
  if (!header) return fallback;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return match ? decodeURIComponent(match[1]) : fallback;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();

  const url = input.value.trim();
  if (!url) {
    showError("Please paste a valid video URL.");
    return;
  }

  setLoading(true);
  activeController = new AbortController();
  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format: mode }),
      signal: activeController.signal,
    });

    if (!res.ok) {
      let message = "Something went wrong. Please try again.";
      try {
        const data = await res.json();
        if (data && data.error) message = data.error;
      } catch (_) {
        /* ignore parse failure, use default message */
      }
      showError(message);
      return;
    }

    const blob = await res.blob();
    const filename = filenameFromContentDisposition(res.headers.get("Content-Disposition"));
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);

    flashSuccess();
  } catch (err) {
    if (err.name !== "AbortError") {
      showError("Network error while contacting the server. Please try again.");
    }
  } finally {
    setLoading(false);
    activeController = null;
  }
});

cancelBtn.addEventListener("click", () => {
  if (activeController) activeController.abort();
});

// Theme toggle: explicit choice always overrides the OS preference once
// made, persisted so it sticks across visits. No stored choice = follow
// prefers-color-scheme (handled entirely in CSS, nothing to do here).
const themeToggle = document.getElementById("theme-toggle");
const systemPrefersLight = window.matchMedia("(prefers-color-scheme: light)");

function isCurrentlyLight() {
  const stored = document.documentElement.getAttribute("data-theme");
  return stored ? stored === "light" : systemPrefersLight.matches;
}

function updateThemeToggleLabel() {
  themeToggle.setAttribute(
    "aria-label",
    isCurrentlyLight() ? "Switch to dark theme" : "Switch to light theme"
  );
}

themeToggle.addEventListener("click", () => {
  const next = isCurrentlyLight() ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("theme", next);
  } catch (e) {}
  updateThemeToggleLabel();
});

updateThemeToggleLabel();
systemPrefersLight.addEventListener("change", () => {
  if (!document.documentElement.getAttribute("data-theme")) updateThemeToggleLabel();
});
