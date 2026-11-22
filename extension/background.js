// The ApplAI extension's service worker: it reads the offer pages ApplAI asks for, with this
// Chrome's session, so the candidate never signs in anywhere for ApplAI. It reads only: no tab,
// no click, no form, no typing. Setup: docs/extension.md.

export const APPLAI = "http://127.0.0.1:8000"; // applai serve's address
const READ = "read-pages";

// The sites it reads; the manifest's host permissions give no other one.
function readable(url) {
  const { protocol, hostname } = new URL(url);
  return protocol === "https:" && (hostname === "jobteaser.com" || hostname.endsWith(".jobteaser.com"));
}

// Ask ApplAI for the pages to read, fetch each with the browser's cookies, send back its HTML.
// A page that fails is asked for again at the next minute.
export async function readPages(fetch = globalThis.fetch) {
  let pages;
  try {
    const asked = await fetch(`${APPLAI}/api/browser/pages`, {});
    if (!asked.ok) return;
    pages = await asked.json();
  } catch {
    return; // ApplAI is not running
  }
  for (const { offer_id, url } of pages) {
    try {
      if (!readable(url)) continue;
      const page = await fetch(url, { credentials: "include" });
      if (!page.ok) continue;
      await fetch(`${APPLAI}/api/browser/pages/${offer_id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ html: await page.text() }),
      });
    } catch {
      // unreachable or not a URL: the next minute's ask tries again
    }
  }
}

if (globalThis.chrome?.alarms) {
  // Chrome may clear alarms when it restarts: set the minute's one again when it is missing.
  chrome.alarms.get(READ).then((alarm) => alarm ?? chrome.alarms.create(READ, { periodInMinutes: 1 }));
  chrome.alarms.onAlarm.addListener((alarm) => alarm.name === READ && readPages());
}
