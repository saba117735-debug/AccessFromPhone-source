/* The phone page's service worker. Two jobs:
 *   1. Show a notification when the PC sends one ("Plan ready – tap to approve", "Job finished"), even with the page closed.
 *   2. Keep the page's own files, so opening the page with no connection shows the app (and its "can't reach your PC" state)
 *      instead of the browser's error page.
 * It never stores anything from /api, /pair or a download: those always go to the PC. */
const VERSION = "__VERSION__";  // the server puts the app's version here, so an update replaces the stored files
const SHELL = "afp-shell-" + VERSION;
const FILES = [
  "/", "/static/app.css", "/static/glass.css", "/static/conn.js", "/static/offline.js", "/static/queue.js", "/static/titles.js", "/static/access.js", "/static/screen.js", "/static/ux.js", "/static/app.js", "/static/icon.svg", "/static/manifest.webmanifest",
  "/static/icon-192.png", "/static/icon-512.png", "/static/vendor/jsQR.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL)
      .then((cache) => Promise.all(FILES.map((file) => cache.add(new Request(file, { cache: "reload" })).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("afp-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  const isPage = url.pathname === "/";
  if (!isPage && !url.pathname.startsWith("/static/")) return;  // the PC's own answers are never kept
  // The page's address may carry a one-time pairing code, so the page is always kept under plain "/".
  const key = isPage ? new Request("/") : request;
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) { const copy = response.clone(); caches.open(SHELL).then((cache) => cache.put(key, copy)); }
        return response;
      })
      .catch(() => caches.match(key).then((hit) => hit || Response.error()))
  );
});

const APPLE = /iPhone|iPad|iPod/.test(self.navigator.userAgent);

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil((async () => {
    // "Your PC is going to sleep / shutting down": kept for a while, so that if the PC then goes quiet the page can say why, even though
    // it was closed when the notice came.
    if (typeof data.kind === "string" && data.kind.startsWith("pc_")) {
      try { await (await caches.open("afp-notice")).put("/__notice", new Response(JSON.stringify({ kind: data.kind, at: Date.now() }))); } catch (e) { /* storage is full */ }
    }
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    // The page is open in front of the person: tell it, and don't also pop a banner. (An iPhone insists every push shows one.)
    if (!APPLE && data.kind !== "test" && windows.some((w) => w.visibilityState === "visible")) {
      windows.forEach((w) => w.postMessage({ type: "push", job: data.job || "", kind: data.kind || "" }));
      return;
    }
    await self.registration.showNotification(data.title || "AccessFromPhone", {
      body: data.body || "",
      tag: data.tag || undefined,       // a newer message about the same request replaces the older one
      renotify: !!data.tag,
      icon: "/static/icon-192.png",
      badge: "/static/badge-96.png",
      requireInteraction: data.kind === "awaiting",  // a plan waiting for approval stays until it is dealt with
      data: { job: data.job || "" },
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const job = (event.notification.data && event.notification.data.job) || "";
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of windows) {
      if ("focus" in w) { await w.focus(); w.postMessage({ type: "open", job }); return; }
    }
    await self.clients.openWindow(job ? "/#job/" + encodeURIComponent(job) : "/");
  })());
});
