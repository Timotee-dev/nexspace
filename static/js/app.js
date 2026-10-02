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
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
if ("serviceWorker" in navigator && isLocal) {
  // On your laptop: no offline mode, so a stopped server shows a normal browser error
  // instead of "You're offline". Also removes any service worker installed earlier.
  navigator.serviceWorker.getRegistrations().then((regs) => regs.forEach((r) => r.unregister()));
  if ("caches" in window) caches.keys().then((keys) => keys.forEach((k) => caches.delete(k)));
} else if ("serviceWorker" in navigator) {
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

// ---------- Phones: slide-in sidebar (tap your photo, or swipe right; swipe left to close) ----------
(() => {
  const drawer = document.getElementById("app-sidebar");
  const backdrop = document.querySelector(".drawer-backdrop");
  const toggles = document.querySelectorAll("[data-drawer-open]");
  if (!drawer || !backdrop) return;
  const phone = matchMedia("(max-width: 767px)");
  const NO_SWIPE = ".chip-row, .tabs, .feed-tabs, .kind-picker, .settings-nav, .countdown-strip, .up-next, " +
    ".table-wrap, .post-images, .quick-links, input, textarea, select, [data-no-swipe]";
  let lastFocus = null;

  const isOpen = () => document.body.classList.contains("drawer-open");
  const setInert = () => { drawer.inert = phone.matches && !isOpen(); };

  function open() {
    lastFocus = document.activeElement;
    document.body.classList.add("drawer-open");
    toggles.forEach((t) => t.setAttribute("aria-expanded", "true"));
    setInert();
    drawer.querySelector("a, button")?.focus({ preventScroll: true });
  }
  function close() {
    document.body.classList.remove("drawer-open");
    toggles.forEach((t) => t.setAttribute("aria-expanded", "false"));
    setInert();
    lastFocus?.focus?.({ preventScroll: true });
  }

  toggles.forEach((t) => t.addEventListener("click", open));
  document.addEventListener("click", (e) => { if (e.target.closest("[data-drawer-close]")) close(); });
  drawer.addEventListener("click", (e) => { if (phone.matches && e.target.closest("a")) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
  phone.addEventListener("change", () => { if (!phone.matches) close(); setInert(); });
  setInert();

  // Swipe gestures that follow your finger
  let startX = 0, startY = 0, dx = 0, tracking = false, horizontal = null, startTime = 0;
  const width = () => drawer.getBoundingClientRect().width || 300;

  document.addEventListener("touchstart", (e) => {
    if (!phone.matches || e.touches.length !== 1) return;
    const t = e.touches[0];
    const target = e.target;
    if (!isOpen() && target.closest(NO_SWIPE)) return;  // let horizontal scrollers and forms scroll
    startX = t.clientX; startY = t.clientY; dx = 0; tracking = true; horizontal = null; startTime = Date.now();
  }, { passive: true });

  document.addEventListener("touchmove", (e) => {
    if (!tracking) return;
    const t = e.touches[0];
    const mx = t.clientX - startX, my = t.clientY - startY;
    if (horizontal === null && (Math.abs(mx) > 10 || Math.abs(my) > 10)) {
      horizontal = Math.abs(mx) > Math.abs(my) * 1.4 && (isOpen() ? mx < 0 : mx > 0);
      if (!horizontal) { tracking = false; return; }
      document.body.classList.add("drawer-dragging");
    }
    if (!horizontal) return;
    const w = width();
    dx = isOpen() ? Math.min(0, mx) : Math.max(0, Math.min(w, mx));
    const offset = isOpen() ? dx : dx - w;  // pixels from fully open
    drawer.style.transform = `translateX(${offset}px)`;
    backdrop.style.opacity = String(1 + offset / w);
  }, { passive: true });

  document.addEventListener("touchend", () => {
    if (!tracking) return;
    tracking = false;
    if (!horizontal) return;
    document.body.classList.remove("drawer-dragging");
    drawer.style.transform = "";
    backdrop.style.opacity = "";
    const fast = Math.abs(dx) / Math.max(Date.now() - startTime, 1) > 0.5;
    const far = Math.abs(dx) > width() * 0.35;
    if (isOpen()) { if (fast || far) close(); } else if (fast || far) open();
  });
})();

// ---------- Live updates (no page refresh; checks only while the tab is visible) ----------
function onVisibleInterval(fn, ms) {
  let timer = null;
  const start = () => { if (!timer) timer = setInterval(() => { if (!document.hidden) fn(); }, ms); };
  const stop = () => { clearInterval(timer); timer = null; };
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else { fn(); start(); } });
  start();
}

function setBadges(selector, count) {
  document.querySelectorAll(selector).forEach((b) => {
    b.textContent = count > 99 ? "99+" : String(count);
    b.hidden = count === 0;
  });
}

if (document.body.dataset.authenticated === "true") {
  onVisibleInterval(async () => {
    try {
      const r = await fetch("/live/", { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const { notifications, messages } = await r.json();
      setBadges("[data-unread]", notifications);
      setBadges("[data-unread-messages]", messages);
    } catch { /* offline */ }
  }, 15000);
}

// "Show new posts" / "Show new comments" bars
(() => {
  const feedEl = document.querySelector("[data-live-feed]");
  const commentsEl = document.querySelector("[data-live-comments]");
  const target = feedEl || commentsEl;
  if (!target) return;
  const pill = (feedEl ? document : commentsEl).querySelector("[data-live-pill]");
  const button = pill?.querySelector("[data-live-refresh]");
  const url = feedEl
    ? () => `/live/feed/?tab=${encodeURIComponent(feedEl.dataset.liveFeed)}&after=${feedEl.dataset.maxId || 0}`
    : () => `/live/post/${commentsEl.dataset.liveComments}/?after=${commentsEl.dataset.maxComment || 0}`;
  onVisibleInterval(async () => {
    try {
      const r = await fetch(url(), { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const { new: count } = await r.json();
      if (count > 0 && pill) {
        button.textContent = feedEl ? `Show ${count} new post${count > 1 ? "s" : ""}`
          : `Show ${count} new comment${count > 1 ? "s" : ""}`;
        pill.hidden = false;
      }
    } catch { /* offline */ }
  }, feedEl ? 30000 : 15000);
  button?.addEventListener("click", () => {
    if (feedEl) { window.scrollTo({ top: 0 }); location.reload(); }
    else { location.hash = "comments"; location.reload(); }
  });
})();

// ---------- Live chat ----------
(() => {
  const page = document.querySelector("[data-chat]");
  if (!page) return;
  const id = page.dataset.chat;
  const log = page.querySelector(".chat-log");
  const latest = log.querySelector("#latest");
  const form = page.querySelector("[data-chat-form]");
  const box = form?.querySelector("textarea");
  let lastId = Number(page.dataset.lastId || 0);
  const firstId = Number(page.dataset.firstId || 0);
  const csrf = () => document.querySelector("input[name=csrfmiddlewaretoken]")?.value || "";

  const nearBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
  const toBottom = () => latest.scrollIntoView({ block: "end" });
  toBottom();

  function bubble(m) {
    if (document.getElementById(`m-${m.id}`)) return;
    log.querySelector(".chat-empty")?.remove();
    const row = document.createElement("li");
    row.className = `bubble-row${m.mine ? " mine" : ""}`;
    row.id = `m-${m.id}`;
    row.dataset.id = m.id;
    const div = document.createElement("div");
    div.className = `bubble${m.deleted ? " bubble-deleted" : ""}`;
    if (m.deleted) div.innerHTML = "<i>Message deleted</i>";
    else div.innerHTML = m.html;  // server-escaped (linebreaks + links only)
    const time = document.createElement("span");
    time.className = "bubble-time";
    time.textContent = m.time;
    div.append(time);
    row.append(div);
    if (!m.deleted) {
      if (m.mine) {
        const f = document.createElement("form");
        f.method = "post";
        f.setAttribute("action", `/messages/${id}/delete/${m.id}/`);
        f.className = "bubble-action";
        f.dataset.deleteMessage = "";
        f.dataset.confirm = "Delete this message for both of you?";
        f.innerHTML = '<button class="action" aria-label="Delete message">Delete</button>';
        row.append(f);
      } else {
        const a = document.createElement("a");
        a.className = "bubble-action action";
        a.href = `/report/message/${m.id}/?next=${encodeURIComponent(location.pathname)}`;
        a.setAttribute("aria-label", "Report message");
        a.textContent = "Report";
        row.append(a);
      }
    }
    log.insertBefore(row, latest);
    lastId = Math.max(lastId, m.id);
  }

  async function poll() {
    try {
      const r = await fetch(`/messages/${id}/poll/?after=${lastId}&since_id=${firstId}`,
        { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const data = await r.json();
      const stick = nearBottom();
      data.messages.forEach(bubble);
      data.deleted.forEach((mid) => {
        const b = document.querySelector(`#m-${mid} .bubble`);
        if (b && !b.classList.contains("bubble-deleted")) {
          b.classList.add("bubble-deleted");
          b.innerHTML = "<i>Message deleted</i>";
          document.querySelector(`#m-${mid} .bubble-action`)?.remove();
        }
      });
      if (data.messages.length && stick) toBottom();
    } catch { /* offline */ }
  }
  onVisibleInterval(poll, 3000);

  if (form && box) {
    const grow = () => { box.style.height = "auto"; box.style.height = `${Math.min(box.scrollHeight, 160)}px`; };
    box.addEventListener("input", grow);
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && matchMedia("(hover: hover)").matches) {
        e.preventDefault();
        form.requestSubmit();
      }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = box.value.trim();
      if (!body) return;
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const r = await fetch(form.getAttribute("action"), {
          method: "POST", credentials: "same-origin",
          headers: { Accept: "application/json", "X-CSRFToken": csrf() },
          body: new URLSearchParams({ body }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data?.error?.message || "Message not sent. Try again.");
        bubble(data.message);
        box.value = "";
        grow();
        toBottom();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        button.disabled = false;
        box.focus();
      }
    });
  }

  log.addEventListener("submit", async (e) => {
    const f = e.target.closest("[data-delete-message]");
    if (!f) return;
    e.preventDefault();
    if (f.dataset.confirm && !confirm(f.dataset.confirm)) return;
    const r = await fetch(f.getAttribute("action"), { method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": csrf() } });
    if (r.ok) {
      const row = f.closest(".bubble-row");
      const b = row.querySelector(".bubble");
      b.classList.add("bubble-deleted");
      b.innerHTML = "<i>Message deleted</i>";
      f.remove();
    }
  }, true);
})();

// ---------- Repost without reloading ----------
document.addEventListener("submit", async (e) => {
  const form = e.target.closest("[data-repost]");
  if (!form) return;
  e.preventDefault();
  const action = form.querySelector("input[name=action]");
  try {
    // getAttribute: form.action would return the <input name="action"> instead of the URL
    const r = await fetch(form.getAttribute("action"), { method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": form.querySelector("input[name=csrfmiddlewaretoken]").value },
      body: new URLSearchParams({ action: action.value }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data?.error?.message || "Couldn't repost. Try again.");
    document.querySelectorAll(`[data-repost-summary="${form.dataset.repost}"]`).forEach((s) => {
      s.classList.toggle("is-on", data.reposted);
      s.querySelector("[data-repost-count]").textContent = data.count;
      s.closest("details").open = false;
    });
    document.querySelectorAll(`[data-repost="${form.dataset.repost}"]`).forEach((f) => {
      f.querySelector("input[name=action]").value = data.reposted ? "undo" : "repost";
      f.querySelector("button").textContent = data.reposted ? "Undo repost" : "Repost";
    });
    toast(data.reposted ? "Reposted." : "Repost removed.", "success");
  } catch (err) {
    toast(err.message, "error");
  }
});

// ---------- Sign-up: only show courses from the chosen department ----------
(() => {
  const deptSelect = document.querySelector('.signup-form select[name="department"]');
  if (!deptSelect) return;
  const sync = () => {
    let shown = 0;
    document.querySelectorAll(".course-picks [data-dept]").forEach((label) => {
      label.hidden = deptSelect.value !== "" && label.dataset.dept !== deptSelect.value;
      if (!label.hidden) shown += 1;
    });
    const empty = document.querySelector(".course-picks-empty");
    if (empty) empty.hidden = shown > 0;  // "No courses have been added for this department yet"
  };
  deptSelect.addEventListener("change", sync);
  sync();
})();
