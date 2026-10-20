// Feed interactions: optimistic votes, bookmarks, follows, share, replies,
// delete confirmation and infinite scroll. Every control is a real <form>,
// so all of this degrades to full-page posts without JavaScript.
import { api, toast } from "./app.js";

// ---------- Votes ----------
document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-vote]");
  if (!form) return;
  event.preventDefault();
  const [kind, id] = form.dataset.vote.split(":");
  const clicked = event.submitter;
  const value = Number(clicked?.value ?? 0);
  const prev = Number(form.dataset.state);
  const scoreEl = form.querySelector(".vote-score");
  const prevScore = Number(scoreEl.textContent);

  const render = (state, score) => {
    form.dataset.state = String(state);
    scoreEl.textContent = score;
    const up = form.querySelector(".vote-btn.up");
    const down = form.querySelector(".vote-btn.down");
    up.setAttribute("aria-pressed", String(state === 1));
    down.setAttribute("aria-pressed", String(state === -1));
    up.value = state === 1 ? "0" : "1";
    down.value = state === -1 ? "0" : "-1";
  };

  render(value, prevScore + (value - prev)); // optimistic
  form.dataset.bump = "";
  setTimeout(() => delete form.dataset.bump, 260);
  try {
    const url = kind === "post" ? `/api/posts/${id}/vote/` : `/api/comments/${id}/vote/`;
    const data = await api(url, { method: "POST", body: { value } });
    render(data.my_vote, data.score);
  } catch (error) {
    render(prev, prevScore);
    toast(error.message, "error");
  }
});

// ---------- Bookmarks ----------
document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-bookmark]");
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector("button");
  const was = button.getAttribute("aria-pressed") === "true";
  button.setAttribute("aria-pressed", String(!was));
  try {
    const data = await api(`/api/posts/${form.dataset.bookmark}/bookmark/`, { method: "POST" });
    button.setAttribute("aria-pressed", String(data.bookmarked));
    toast(data.bookmarked ? "Saved." : "Removed from saved.", "success");
  } catch (error) {
    button.setAttribute("aria-pressed", String(was));
    toast(error.message, "error");
  }
});

// ---------- Follows ----------
document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-follow]");
  if (!form) return;
  event.preventDefault();
  const [kind, key] = form.dataset.follow.split(":");
  const button = form.querySelector("button");
  const render = (following) => {
    button.setAttribute("aria-pressed", String(following));
    button.textContent = following ? "Following" : "Follow";
    if (button.dataset.quiet === undefined) {
      button.classList.toggle("btn-ghost", following);
      button.classList.toggle("btn-primary", !following);
    }
  };
  const was = button.getAttribute("aria-pressed") === "true";
  render(!was);
  try {
    const url = kind === "user" ? `/api/users/${key}/follow/` : `/api/topics/${key}/follow/`;
    const data = await api(url, { method: "POST" });
    render(data.following);
    const count = document.querySelector("[data-follower-count]");
    if (count && form.closest(".profile-card, .topic-head")) count.textContent = data.follower_count;
  } catch (error) {
    render(was);
    toast(error.message, "error");
  }
});

// ---------- Share / copy link ----------
document.addEventListener("click", async (event) => {
  const link = event.target.closest("[data-share]");
  if (!link) return;
  event.preventDefault();
  const url = link.dataset.share;
  if (navigator.share && matchMedia("(pointer: coarse)").matches) {
    try { await navigator.share({ url }); } catch { /* user cancelled */ }
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    toast("Link copied.", "success");
  } catch {
    window.location.href = url;
  }
});

// ---------- Confirm destructive actions ----------
document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
}, true);

// Close any open "more" menu when clicking elsewhere
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  });
});

// ---------- Reply toggles ----------
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-reply]");
  if (!button) return;
  const form = document.getElementById(`reply-${button.dataset.reply}`);
  const open = form.hidden;
  form.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  if (open) form.querySelector("textarea").focus();
});

// ---------- Infinite scroll ----------
const skeleton = () => {
  const wrap = document.createElement("div");
  wrap.setAttribute("aria-hidden", "true");
  wrap.innerHTML = Array.from({ length: 3 }, () =>
    '<div class="skeleton-post"><div class="sk sk-avatar"></div><div class="sk-lines">' +
    '<div class="sk sk-line" style="width:40%"></div><div class="sk sk-line"></div>' +
    '<div class="sk sk-line" style="width:75%"></div></div></div>').join("");
  return wrap;
};

function watchMore(feed) {
  const more = feed.querySelector(".feed-more[data-next]");
  if (!more || !("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver(async (entries) => {
    if (!entries[0].isIntersecting) return;
    observer.disconnect();
    const placeholder = skeleton();
    more.replaceWith(placeholder);
    try {
      const response = await fetch(more.dataset.next, { credentials: "same-origin" });
      if (!response.ok) throw new Error();
      const html = await response.text();
      const template = document.createElement("template");
      template.innerHTML = html;
      placeholder.replaceWith(template.content);
      watchMore(feed);
    } catch {
      placeholder.replaceWith(more);
      toast("Couldn't load more posts. Check your connection.", "error");
    }
  }, { rootMargin: "600px" });
  observer.observe(more);
}
document.querySelectorAll("[data-feed]").forEach(watchMore);

// Confirm specific submit buttons (e.g. "Remove", "Ban" in the moderation queue)
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-confirm-click]");
  if (button && !window.confirm(button.dataset.confirmClick)) event.preventDefault();
}, true);

// Show only the relevant target field in audience forms
document.querySelectorAll(".audience-form").forEach((form) => {
  const select = form.querySelector('[name="audience"]');
  const sync = () => {
    form.querySelectorAll(".aud").forEach((el) => { el.hidden = !el.classList.contains(`aud-${select.value}`); });
  };
  select?.addEventListener("change", sync);
  if (select) sync();
});

// Filter panels start open on wide screens
if (matchMedia("(min-width: 900px)").matches) {
  document.querySelectorAll("details[data-open-desktop]").forEach((d) => { d.open = true; });
}

// ---------- Live search (debounced; the form still works without JS) ----------
document.querySelectorAll("[data-live-search]").forEach((form) => {
  const input = form.querySelector('input[name="q"]');
  const results = document.querySelector("[data-search-results]");
  let timer = null;
  let controller = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const params = new URLSearchParams(new FormData(form));
      params.set("partial", "1");
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch(`${form.action}?${params}`, { signal: controller.signal, credentials: "same-origin" });
        if (!response.ok) return;
        results.innerHTML = await response.text();
        params.delete("partial");
        history.replaceState(null, "", `${form.action}?${params}`);
      } catch { /* aborted or offline */ }
    }, 300);
  });
});

// NexAI suggestion chips fill the question box
document.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-fill-question]");
  if (!chip) return;
  const box = document.getElementById("nexai-q");
  box.value = chip.dataset.fillQuestion;
  box.focus();
});
