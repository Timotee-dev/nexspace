// Turn browser push on/off for this device (notification settings page).
import { api, toast } from "./app.js";

const button = document.querySelector("[data-push-toggle]");

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

async function currentSubscription() {
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

async function render() {
  const sub = await currentSubscription();
  button.textContent = sub ? "Turn off push on this device" : "Turn on push on this device";
  button.dataset.state = sub ? "on" : "off";
  button.hidden = false;
}

if (button) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    document.querySelector("[data-push-unsupported]")?.removeAttribute("hidden");
  } else {
    render();
    button.addEventListener("click", async () => {
      button.setAttribute("aria-busy", "true");
      try {
        const existing = await currentSubscription();
        if (existing) {
          await api("/api/push/subscriptions/", { method: "DELETE", body: { endpoint: existing.endpoint } });
          await existing.unsubscribe();
          toast("Push notifications are off on this device.", "success");
        } else {
          const permission = await Notification.requestPermission();
          if (permission !== "granted") {
            toast("Your browser blocked notifications. Allow them in the site settings to turn push on.", "error");
            return;
          }
          const reg = await navigator.serviceWorker.ready;
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(button.dataset.key),
          });
          await api("/api/push/subscriptions/", { method: "POST", body: sub.toJSON() });
          toast("Push notifications are on for this device.", "success");
        }
      } catch (error) {
        toast(error.message || "Couldn't change push settings.", "error");
      } finally {
        button.removeAttribute("aria-busy");
        render();
      }
    });
  }
}
