// NexSpace shared UI behaviour. Everything here is progressive enhancement:
// every form and link still works with JavaScript disabled.

export function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRFToken": getCookie("csrftoken") || "" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const message = data?.error?.message || "Something went wrong. Try again.";
    throw Object.assign(new Error(message), { status: response.status, data });
  }
  return data;
}

// ---------- Toasts ----------
export function toast(message, level = "info") {
  let region = document.querySelector(".toasts");
  if (!region) {
    region = document.createElement("div");
    region.className = "toasts";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  const item = document.createElement("div");
  item.className = `toast ${level}`;
  const text = document.createElement("p");
  text.textContent = message;
  item.append(text, closeButton());
  region.append(item);
  autoDismiss(item);
}

function closeButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-btn";
  button.setAttribute("aria-label", "Dismiss");
  button.textContent = "×";
  button.dataset.dismiss = "";
  return button;
}

function dismiss(item) {
  item.dataset.leaving = "";
  setTimeout(() => item.remove(), 200);
}

function autoDismiss(item) {
  const ms = item.classList.contains("error") ? 9000 : 5000;
  setTimeout(() => item.isConnected && dismiss(item), ms);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dismiss]");
  if (button) dismiss(button.closest(".toast"));
});
document.querySelectorAll(".toast").forEach(autoDismiss);

// ---------- Theme ----------
const THEMES = ["system", "dark", "light"];
const THEME_LABELS = { system: "Theme: match device", dark: "Theme: dark", light: "Theme: light" };

function resolve(pref) {
  if (pref === "dark" || pref === "light") return pref;
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(pref) {
  document.documentElement.dataset.theme = resolve(pref);
  document.documentElement.dataset.themePref = pref;
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    btn.setAttribute("aria-label", THEME_LABELS[pref]);
    btn.title = THEME_LABELS[pref];
    btn.dataset.state = pref;
  });
}

matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (document.documentElement.dataset.themePref === "system") applyTheme("system");
});

document.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-theme-toggle]");
  if (!btn) return;
  const current = document.documentElement.dataset.themePref || "system";
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  applyTheme(next);
  try { localStorage.setItem("nexspace-theme", next); } catch { /* storage may be blocked */ }
  if (document.body.dataset.authenticated === "true") {
    try {
      await api("/api/auth/me/", { method: "PATCH", body: { theme: next } });
    } catch {
      toast("Couldn't save your theme to your account. It's saved on this device.", "warning");
    }
  }
});
applyTheme(document.documentElement.dataset.themePref || "system");

// ---------- Password reveal ----------
document.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-reveal]");
  if (!btn) return;
  const input = btn.parentElement.querySelector("input");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  btn.setAttribute("aria-pressed", String(!showing));
  btn.setAttribute("aria-label", showing ? "Show password" : "Hide password");
});

// ---------- Busy state on submit (prevents double submits) ----------
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.dataset.noBusy !== undefined) return;
  const submitter = event.submitter;
  if (submitter && submitter.matches(".btn")) {
    // Defer so the submitter's name/value is still sent
    setTimeout(() => { if (!event.defaultPrevented) submitter.setAttribute("aria-busy", "true"); }, 0);
  }
});
window.addEventListener("pageshow", () =>
  document.querySelectorAll('[aria-busy="true"]').forEach((b) => b.removeAttribute("aria-busy"))
);

// ---------- Avatar preview ----------
document.querySelectorAll("[data-avatar-input]").forEach((input) => {
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    const preview = document.querySelector("[data-avatar-preview]");
    if (!file || !preview) return;
    if (file.size > 5 * 1024 * 1024) {
      toast("Images must be 5 MB or smaller.", "error");
      input.value = "";
      return;
    }
    const img = document.createElement("img");
    img.alt = "New profile photo preview";
    img.src = URL.createObjectURL(file);
    preview.replaceChildren(img);
  });
});

// ---------- Character counters ----------
document.querySelectorAll("[data-count]").forEach((field) => {
  const counter = document.getElementById(field.dataset.count);
  const max = Number(field.getAttribute("maxlength"));
  const update = () => { counter.textContent = `${field.value.length} / ${max}`; };
  field.addEventListener("input", update);
  update();
});

// ---------- PWA: service worker, install button, private-cache cleanup ----------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
    if (document.body.dataset.authenticated !== "true") {
      // Signed out: drop the cached feed so the next person on this device can't see it.
      reg.active?.postMessage("clear-private-cache");
      if ("caches" in window) caches.delete("nexspace-pages");
    }
  }).catch(() => { /* PWA is optional */ });
}

let installPrompt = null;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  document.querySelectorAll("[data-install]").forEach((b) => { b.hidden = false; });
});
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-install]");
  if (!button || !installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  button.hidden = true;
});

// ---------- Unread notifications badge (polls every 60s while the tab is visible) ----------
async function refreshBadge() {
  const badges = document.querySelectorAll("[data-unread]");
  if (!badges.length || document.hidden) return;
  try {
    const { unread } = await api("/api/notifications/unread-count/");
    badges.forEach((b) => { b.textContent = unread > 99 ? "99+" : String(unread); b.hidden = unread === 0; });
  } catch { /* offline or signed out */ }
}
if (document.body.dataset.authenticated === "true") {
  setInterval(refreshBadge, 60000);
  document.addEventListener("visibilitychange", refreshBadge);
}
