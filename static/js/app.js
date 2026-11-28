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
  return item;
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
const THEMES = ["dark", "light", "system"];
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
  const current = document.documentElement.dataset.themePref || "dark";
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
applyTheme(document.documentElement.dataset.themePref || "dark");

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

// ---------- Request size limit (Vercel refuses requests over 4.5 MB before they reach NexSpace) ----------
const maxRequestMb = Number(document.body.dataset.maxRequestMb || 0);
export function tooBigToSend(files) {
  if (!maxRequestMb) return false;
  const total = Array.from(files).reduce((sum, f) => sum + (f?.size || 0), 0);
  return total > maxRequestMb * 1024 * 1024;
}
const tooBigMessage = () => `Files are too big to send together (max ${maxRequestMb} MB in total). Send fewer or smaller files.`;

// ---------- Direct uploads: files go straight to Cloudinary, then the form is sent with a short token ----------
// (Skips Vercel's 4.5 MB request limit. The server turns each token back into a normal upload and checks it.)
export const directUploads = document.body.dataset.directUpload === "1";
const maxFileMb = Number(document.body.dataset.maxDocMb || 10);
const DIRECT_PREFIX = "__direct__";

// Big files are sent in 9 MB pieces (Cloudinary's free plan takes 10 MB per file); NexSpace joins them and
// compresses the result so it fits. onProgress(bytesSent) is called after each piece.
export async function uploadDirect(file, onProgress = () => {}) {
  if (file.size > maxFileMb * 1024 * 1024) throw new Error(`${file.name} is too big. Files can be up to ${maxFileMb} MB.`);
  const permit = await api("/uploads/sign/", { method: "POST", body: { name: file.name, size: file.size, type: file.type } });
  const parts = permit.parts || [{ url: permit.url, fields: permit.fields }];
  const size = permit.part_size || file.size;
  for (let i = 0; i < parts.length; i += 1) {
    const piece = parts.length === 1 ? file : file.slice(i * size, (i + 1) * size);
    const data = new FormData();
    Object.entries(parts[i].fields).forEach(([key, value]) => data.append(key, value));
    data.append("file", piece, parts.length === 1 ? file.name : `${file.name}.part${i}`);
    let response;
    try {
      response = await fetch(parts[i].url, { method: "POST", body: data });
    } catch {
      throw new Error(`${file.name} didn't upload. Check your connection and try again.`);
    }
    if (!response.ok) throw new Error(`${file.name} didn't upload. Try again.`);
    onProgress(Math.min((i + 1) * size, file.size));
  }
  return permit.token;
}

const pickedInputs = (form) => [...form.querySelectorAll('input[type="file"]')]
  .filter((input) => input.name && !input.disabled && input.files && input.files.length);

function resetDirect(form) {
  form.querySelectorAll(`input[type="hidden"][name^="${DIRECT_PREFIX}"]`).forEach((el) => el.remove());
  form.querySelectorAll("input[type=file][data-direct-sent]").forEach((input) => {
    input.disabled = false;
    delete input.dataset.directSent;
  });
}

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const inputs = pickedInputs(form);
  if (!inputs.length) return;
  if (!directUploads) {  // files travel with the form: keep the whole request under Vercel's limit
    if (tooBigToSend(inputs.flatMap((i) => [...i.files]))) {
      event.preventDefault();
      event.stopImmediatePropagation();
      toast(tooBigMessage(), "error");
    }
    return;
  }
  event.preventDefault();
  event.stopImmediatePropagation();
  if (form.dataset.uploading) return;
  const files = inputs.flatMap((input) => [...input.files].map((file) => ({ input, file })));
  const tooBig = files.find(({ file }) => file.size > maxFileMb * 1024 * 1024);
  if (tooBig) { toast(`${tooBig.file.name} is too big. Files can be up to ${maxFileMb} MB.`, "error"); return; }
  const submitter = event.submitter;
  form.dataset.uploading = "1";
  submitter?.setAttribute("aria-busy", "true");
  const total = files.reduce((sum, { file }) => sum + file.size, 0);
  const big = total > 9 * 1024 * 1024;
  const label = files.length > 1 ? `Uploading ${files.length} files` : "Uploading";
  const note = toast(big ? `${label}… 0%` : `${label}…`);
  const sent = files.map(() => 0);
  const progress = (i) => (bytes) => {
    sent[i] = bytes;
    const text = note?.querySelector("p");
    if (big && text) text.textContent = `${label}… ${Math.round((100 * sent.reduce((a, b) => a + b, 0)) / total)}%`;
  };
  try {
    const tokens = [];
    for (let i = 0; i < files.length; i += 1) tokens.push(await uploadDirect(files[i].file, progress(i)));  // one at a time: kinder to slow connections
    if (big && note?.querySelector("p")) note.querySelector("p").textContent = "Compressing and saving…";
    tokens.forEach((token, i) => {
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = DIRECT_PREFIX + files[i].input.name;
      hidden.value = token;
      form.append(hidden);
    });
    inputs.forEach((input) => { input.disabled = true; input.dataset.directSent = "1"; });  // send tokens, not bytes
    if (!big) note?.remove?.();  // big files: keep "Compressing and saving…" up while the server works
    delete form.dataset.uploading;
    submitter?.removeAttribute("aria-busy");
    if (submitter && form.contains(submitter)) form.requestSubmit(submitter); else form.requestSubmit();
  } catch (err) {
    resetDirect(form);
    note?.remove?.();
    toast(err.message || "Upload failed. Try again.", "error");
    delete form.dataset.uploading;
    submitter?.removeAttribute("aria-busy");
  }
}, true);

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
window.addEventListener("pageshow", () => {
  document.querySelectorAll('[aria-busy="true"]').forEach((b) => b.removeAttribute("aria-busy"));
  document.querySelectorAll("form").forEach(resetDirect);  // coming back with the Back button: start fresh
});

// ---------- Profile photo: crop, zoom, remove ----------
// Choosing a photo opens a square cropper (drag to move, slider to zoom). "Use photo" puts the cropped
// 512 x 512 picture back into the file field, so the form uploads exactly what the person saw.
const CROP_SIZE = 512;

function openCropper(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const dialog = document.createElement("dialog");
    dialog.className = "cropper";
    dialog.setAttribute("aria-label", "Adjust your profile photo");
    dialog.innerHTML = `
      <h2 class="section-title">Adjust your photo</h2>
      <p class="small muted">Drag to move it. Use the slider to zoom.</p>
      <div class="cropper-stage" tabindex="0" aria-label="Photo position. Use the arrow keys to move it."><img alt="" draggable="false"></div>
      <label class="cropper-zoom"><span class="small">Zoom</span><input type="range" min="1" max="4" step="0.01" value="1" aria-label="Zoom"></label>
      <div class="form-actions"><button type="button" class="btn btn-ghost btn-sm" data-crop-cancel>Cancel</button>
        <button type="button" class="btn btn-primary btn-sm" data-crop-use>Use photo</button></div>`;
    document.body.append(dialog);
    const stage = dialog.querySelector(".cropper-stage");
    const img = stage.querySelector("img");
    const zoom = dialog.querySelector("input[type=range]");
    let natural = { w: 1, h: 1 }, scale = 1, base = 1, x = 0, y = 0, drag = null;

    const box = () => stage.clientWidth;
    const clamp = () => {  // the photo always covers the whole square
      const w = natural.w * base * scale, h = natural.h * base * scale, s = box();
      x = Math.min(0, Math.max(s - w, x));
      y = Math.min(0, Math.max(s - h, y));
    };
    const draw = () => {
      clamp();
      img.style.width = `${natural.w * base * scale}px`;
      img.style.height = `${natural.h * base * scale}px`;
      img.style.transform = `translate(${x}px, ${y}px)`;
    };
    const finish = (result) => {
      dialog.close();
      dialog.remove();
      URL.revokeObjectURL(url);
      resolve(result);
    };

    img.addEventListener("load", () => {
      natural = { w: img.naturalWidth, h: img.naturalHeight };
      base = box() / Math.min(natural.w, natural.h);  // smallest side fills the square
      x = (box() - natural.w * base) / 2;
      y = (box() - natural.h * base) / 2;
      draw();
    });
    img.addEventListener("error", () => { toast("That photo couldn't be opened. Try a JPEG or PNG.", "error"); finish(null); });
    img.src = url;

    zoom.addEventListener("input", () => {
      const s = box(), cx = (s / 2 - x) / scale, cy = (s / 2 - y) / scale;  // zoom around the centre
      scale = Number(zoom.value);
      x = s / 2 - cx * scale;
      y = s / 2 - cy * scale;
      draw();
    });
    stage.addEventListener("pointerdown", (e) => { drag = { px: e.clientX, py: e.clientY, x, y }; stage.setPointerCapture(e.pointerId); });
    stage.addEventListener("pointermove", (e) => {
      if (!drag) return;
      x = drag.x + e.clientX - drag.px;
      y = drag.y + e.clientY - drag.py;
      draw();
    });
    stage.addEventListener("pointerup", () => { drag = null; });
    stage.addEventListener("pointercancel", () => { drag = null; });
    stage.addEventListener("keydown", (e) => {
      const step = { ArrowLeft: [10, 0], ArrowRight: [-10, 0], ArrowUp: [0, 10], ArrowDown: [0, -10] }[e.key];
      if (step) { e.preventDefault(); x += step[0]; y += step[1]; draw(); }
    });
    dialog.querySelector("[data-crop-cancel]").addEventListener("click", () => finish(null));
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish(null); });  // Esc key
    dialog.querySelector("[data-crop-use]").addEventListener("click", () => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = CROP_SIZE;
      const sx = -x / (base * scale), sy = -y / (base * scale), side = box() / (base * scale);
      canvas.getContext("2d").drawImage(img, sx, sy, side, side, 0, 0, CROP_SIZE, CROP_SIZE);
      canvas.toBlob((blob) => {
        if (!blob) { finish(null); return; }
        const name = (file.name.replace(/\.[^.]+$/, "") || "photo") + ".jpg";
        finish(new File([blob], name, { type: "image/jpeg" }));
      }, "image/jpeg", 0.9);
    });
    dialog.showModal();
    stage.focus();
  });
}

document.querySelectorAll("[data-avatar-input]").forEach((input) => {
  const preview = document.querySelector("[data-avatar-preview]");
  const actions = input.closest(".avatar-edit-actions");
  const original = preview?.innerHTML;
  let chosen = null;  // the photo as picked, so "Adjust" can re-crop from the full picture

  const tools = document.createElement("div");
  tools.className = "avatar-picked";
  tools.hidden = true;
  tools.innerHTML = `<button type="button" class="btn btn-quiet btn-sm" data-avatar-adjust>Adjust</button>
    <button type="button" class="btn btn-quiet btn-sm" data-avatar-clear aria-label="Remove the photo you chose">\u00d7 Remove</button>`;
  actions?.insertBefore(tools, actions.querySelector(".field-help"));

  const show = (file) => {
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    const img = document.createElement("img");
    img.alt = "New profile photo preview";
    img.src = URL.createObjectURL(file);
    img.className = "avatar-new";
    preview?.replaceChildren(img);
    tools.hidden = false;
  };
  const clear = () => {
    input.value = "";
    chosen = null;
    if (preview) preview.innerHTML = original;
    tools.hidden = true;
  };
  const crop = async (file) => {
    const cropped = await openCropper(file);
    if (cropped) show(cropped);
    else if (!input.dataset.cropped) clear();
    if (cropped) input.dataset.cropped = "1";
  };

  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (!file) return;
    const maxMb = Number(document.body.dataset.maxDocMb || 100);
    if (file.size > maxMb * 1024 * 1024) {
      toast(`Images must be ${maxMb} MB or smaller.`, "error");
      clear();
      return;
    }
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
      toast("Choose a JPEG, PNG or WebP photo.", "error");
      clear();
      return;
    }
    chosen = file;
    delete input.dataset.cropped;
    if (typeof HTMLDialogElement === "function" && "DataTransfer" in window) crop(file);
    else show(file);  // very old browsers: upload as chosen (the server still resizes it)
  });
  tools.querySelector("[data-avatar-adjust]").addEventListener("click", () => chosen && crop(chosen));
  tools.querySelector("[data-avatar-clear]").addEventListener("click", clear);
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

  const isGroup = page.dataset.group === "true";

  function attachmentNodes(m) {
    return (m.attachments || []).map((a) => {
      const link = document.createElement("a");
      link.href = a.url;
      if (a.kind === "image") {
        link.className = "bubble-image";
        link.target = "_blank";
        link.rel = "noopener";
        const img = document.createElement("img");
        img.src = a.url;
        img.alt = `Photo: ${a.name}`;
        img.loading = "lazy";
        link.append(img);
      } else {
        link.className = "bubble-file";
        const ext = document.createElement("span");
        ext.className = "file-ext";
        ext.textContent = a.ext;
        const name = document.createElement("span");
        name.className = "file-name";
        name.textContent = a.name;
        link.append(ext, name);
      }
      return link;
    });
  }

  function bubble(m) {
    if (document.getElementById(`m-${m.id}`)) return;
    log.querySelector(".chat-empty")?.remove();
    const row = document.createElement("li");
    row.id = `m-${m.id}`;
    row.dataset.id = m.id;
    if (m.system) {
      row.className = "chat-system";
      row.textContent = m.html.replace(/<[^>]+>/g, "");  // plain text only
      log.insertBefore(row, latest);
      lastId = Math.max(lastId, m.id);
      return;
    }
    row.className = `bubble-row${m.mine ? " mine" : ""}`;
    const div = document.createElement("div");
    div.className = `bubble${m.deleted ? " bubble-deleted" : ""}`;
    if (isGroup && !m.mine) {
      const who = document.createElement("span");
      who.className = "bubble-sender";
      who.textContent = m.sender;
      div.append(who);
    }
    if (m.deleted) {
      const i = document.createElement("i");
      i.textContent = "Message deleted";
      div.append(i);
    } else {
      div.append(...attachmentNodes(m));
      if (m.html) {
        const text = document.createElement("span");
        text.className = "bubble-text";
        text.innerHTML = m.html;  // server-escaped (linebreaks + links only)
        div.append(text);
      }
    }
    const time = document.createElement("span");
    time.className = "bubble-time";
    time.textContent = m.time;
    div.append(time);
    row.append(div);
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
    const picker = form.querySelector("[data-chat-attach]");
    const chips = form.querySelector("[data-chat-files]");
    let pending = [];
    const maxDoc = Number(document.body.dataset.maxDocMb || 10) * 1024 * 1024;
    const renderChips = () => {
      chips.replaceChildren(...pending.map((f, i) => {
        const chip = document.createElement("span");
        chip.className = "chat-file-chip";
        const name = document.createElement("span");
        name.textContent = `${f.type.startsWith("image/") ? "🖼" : "📄"} ${f.name}`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Remove ${f.name}`);
        remove.addEventListener("click", () => { pending.splice(i, 1); renderChips(); });
        chip.append(name, remove);
        return chip;
      }));
      chips.hidden = pending.length === 0;
    };
    picker?.addEventListener("change", () => {
      for (const f of picker.files) {
        const limit = maxDoc;  // big files are compressed on the server
        if (f.size > limit) { toast(`${f.name} is too big (max ${Math.round(limit / 1048576)} MB).`, "error"); continue; }
        if (pending.length >= 4) { toast("Attach up to 4 photos or files at a time.", "error"); break; }
        pending.push(f);
      }
      picker.value = "";
      renderChips();
    });

    let sending = false;  // one message at a time, however many times Enter or Send is pressed
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (sending) return;
      const body = box.value.trim();
      if (!body && !pending.length) return;
      if (!directUploads && tooBigToSend(pending)) { toast(tooBigMessage(), "error"); return; }
      sending = true;
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      const data = new FormData();
      data.append("body", body);
      const files = pending;
      if (directUploads && files.length) {
        try {
          const big = files.reduce((sum, f) => sum + f.size, 0) > 9 * 1024 * 1024;
          const note = big ? toast("Uploading… this can take a minute for big files.") : null;
          for (const f of files) data.append(DIRECT_PREFIX + "files", await uploadDirect(f));
          note?.remove?.();
        } catch (err) {
          toast(err.message, "error");
          sending = false;
          button.disabled = false;
          return;
        }
      } else {
        files.forEach((f) => data.append("files", f));
      }
      box.value = "";  // clear straight away so the same text can't be sent twice
      pending = [];
      renderChips();
      grow();
      try {
        const r = await fetch(form.action, {
          method: "POST", credentials: "same-origin",
          headers: { Accept: "application/json", "X-CSRFToken": csrf() }, body: data,
        });
        if (r.status === 413) throw new Error("Those files are too big to send. Try smaller or fewer files.");
        const json = await r.json();
        if (!r.ok) throw new Error(json?.error?.message || "Message not sent. Try again.");
        bubble(json.message);
        toBottom();
      } catch (err) {
        if (!box.value) { box.value = body; grow(); }  // put the text back so nothing is lost
        if (!pending.length) { pending = files; renderChips(); }
        toast(err.message, "error");
      } finally {
        sending = false;
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


// ---------- People picker (new group / add people) ----------
document.querySelectorAll("[data-people-picker]").forEach((picker) => {
  const input = picker.querySelector("[data-people-filter]");
  const none = picker.querySelector("[data-people-none]");
  input?.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase().replace(/^@/, "");
    let shown = 0;
    picker.querySelectorAll("[data-person]").forEach((li) => {
      li.hidden = q !== "" && !li.dataset.person.includes(q);
      if (!li.hidden) shown += 1;
    });
    if (none) none.hidden = shown > 0;
  });
});

// ---------- Small behaviours (instead of inline onchange/onclick, which the CSP blocks) ----------
document.addEventListener("change", (event) => {
  const field = event.target.closest("[data-autosubmit]");
  if (field && field.form) field.form.requestSubmit ? field.form.requestSubmit() : field.form.submit();
});
document.addEventListener("click", (event) => {
  const field = event.target.closest("[data-select-on-click]");
  if (field) field.select();
});

// Role form (Admin → People): only show the box that the chosen role needs
// (course reps and level advisers: a level; lecturers: a course).
document.querySelectorAll("[data-role-form]").forEach((form) => {
  const role = form.querySelector('[name="role"]');
  const sync = () => form.querySelectorAll("[data-for-roles]").forEach((box) => {
    box.hidden = !box.dataset.forRoles.split(" ").includes(role.value);
  });
  role?.addEventListener("change", sync);
  if (role) sync();
});
