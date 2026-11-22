// The ApplAI extension's service worker: it reads the offer pages ApplAI asks for, with this
// Chrome's session, so the candidate never signs in anywhere for ApplAI. It reads only: no tab,
// no click, no form, no typing. Setup: docs/extension.md.

export const APPLAI = "http://127.0.0.1:8000"; // applai serve's address
const READ = "read-pages";

// Ask ApplAI for the pages to read, fetch each with the browser's cookies, send back its HTML.
// Only JobTeaser's, as the manifest's host permissions. A page that fails is reported: ApplAI
// asks for it again only after its next scan, never every minute.
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
    if (!onJobTeaser(url)) continue;
    let path = "", body;
    try {
      const page = await fetch(url, { credentials: "include" });
      if (page.ok) body = { html: await page.text() };
      else [path, body] = ["/failure", { status: page.status }];
    } catch {
      [path, body] = ["/failure", {}]; // not fetched
    }
    try {
      await fetch(`${APPLAI}/api/browser/pages/${offer_id}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      // ApplAI stopped meanwhile: it asks for the page again
    }
  }
}

function onJobTeaser(url) {
  try {
    const { protocol, hostname } = new URL(url);
    return protocol === "https:" && `.${hostname}`.endsWith(".jobteaser.com");
  } catch {
    return false; // not a URL
  }
}

if (globalThis.chrome?.alarms) {
  // Chrome may clear alarms when it restarts: set the minute's one again when it is missing.
  chrome.alarms.get(READ).then((alarm) => alarm ?? chrome.alarms.create(READ, { periodInMinutes: 1 }));
  chrome.alarms.onAlarm.addListener((alarm) => alarm.name === READ && readPages());
}
