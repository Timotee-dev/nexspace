// Composer enhancements: poll options, attachment previews with remove,
// topic limit, and a local draft so nothing is lost on refresh.
import { toast } from "./app.js";

const form = document.querySelector("[data-composer]");
if (form) {
  const DRAFT_KEY = "nexspace-draft";
  const MAX = { images: 4, files: 3 };
  const MAX_BYTES = { images: 5 * 1024 * 1024, files: 10 * 1024 * 1024 };

  // ---- Poll options ----
  const options = form.querySelector("[data-poll-options]");
  const addButton = form.querySelector("[data-add-option]");
  const syncAdd = () => { addButton.hidden = options.children.length >= 6; };
  addButton?.addEventListener("click", () => {
    const n = options.children.length + 1;
    const input = document.createElement("input");
    Object.assign(input, { type: "text", name: "poll_option", maxLength: 80, placeholder: `Option ${n}` });
    input.setAttribute("aria-label", `Option ${n}`);
    options.append(input);
    input.focus();
    syncAdd();
  });
  if (options) syncAdd();

  // ---- Topics: max 3 ----
  const chips = form.querySelector("[data-max-topics]");
  const syncTopics = () => {
    const boxes = [...chips.querySelectorAll("input")];
    const full = boxes.filter((b) => b.checked).length >= Number(chips.dataset.maxTopics);
    boxes.forEach((b) => { b.disabled = full && !b.checked; });
  };
  chips?.addEventListener("change", syncTopics);
  if (chips) syncTopics();

  // ---- Attachments with previews and remove buttons ----
  const preview = form.querySelector("[data-attach-preview]");
  const selected = { images: [], files: [] };
  const inputs = Object.fromEntries([...form.querySelectorAll("[data-attach]")].map((i) => [i.dataset.attach, i]));

  const syncInput = (kind) => {
    const transfer = new DataTransfer();
    selected[kind].forEach((f) => transfer.items.add(f));
    inputs[kind].files = transfer.files;
  };

  const renderPreview = () => {
    preview.replaceChildren();
    for (const kind of ["images", "files"]) {
      selected[kind].forEach((file, index) => {
        const li = document.createElement("li");
        if (kind === "images") {
          const img = document.createElement("img");
          img.src = URL.createObjectURL(file);
          img.alt = "";
          li.append(img);
        } else {
          li.textContent = file.name;
        }
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.addEventListener("click", () => {
          selected[kind].splice(index, 1);
          syncInput(kind);
          renderPreview();
        });
        li.append(remove);
        preview.append(li);
      });
    }
  };

  for (const [kind, input] of Object.entries(inputs)) {
    input.addEventListener("change", () => {
      for (const file of input.files) {
        if (selected[kind].length >= MAX[kind]) { toast(`You can add up to ${MAX[kind]} ${kind}.`, "error"); break; }
        if (file.size > MAX_BYTES[kind]) { toast(`${file.name} is too large.`, "error"); continue; }
        selected[kind].push(file);
      }
      syncInput(kind);
      renderPreview();
    });
  }

  // ---- Local draft ----
  const fields = ["body", "title"].map((n) => form.elements[n]).filter(Boolean);
  const hasServerValues = fields.some((f) => f.value) || form.querySelector(".form-errors");
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (draft && !hasServerValues) {
      fields.forEach((f) => { if (draft[f.name]) f.value = draft[f.name]; });
      const kind = form.querySelector(`input[name="kind"][value="${draft.kind}"]`);
      if (kind && !new URLSearchParams(location.search).get("type")) kind.checked = true;
      if (draft.body || draft.title) toast("Draft restored.", "info");
      form.elements.body.dispatchEvent(new Event("input"));
    }
  } catch { /* storage unavailable */ }

  const saveDraft = () => {
    try {
      const kind = form.querySelector('input[name="kind"]:checked')?.value;
      const data = Object.fromEntries(fields.map((f) => [f.name, f.value]));
      if (data.body || data.title) localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...data, kind }));
      else localStorage.removeItem(DRAFT_KEY);
    } catch { /* ignore */ }
  };
  form.addEventListener("input", saveDraft);
  form.addEventListener("change", saveDraft);
  form.addEventListener("submit", () => { try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ } });
}
