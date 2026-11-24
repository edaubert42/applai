// The ApplAI extension's service worker: it reads the offer pages and the forms ApplAI asks for,
// with this Chrome's session, so the candidate never signs in anywhere for ApplAI. It reads
// only: no click, no typing, no submit. Setup: docs/extension.md.
import { readForm, readText } from "./forms.js";

export const APPLAI = "http://127.0.0.1:8000"; // applai serve's address
const READ = "read-pages";
const LOADED_WITHIN_MS = 30_000; // a form's tab, then it is reported as not loaded
const TRIES = 3; // a page may draw its form after it loads: read it again 2 s later

// Ask ApplAI for the pages to read, fetch each with the browser's cookies, send back its HTML.
// Only JobTeaser's and Google's, as the manifest's host permissions. A page that fails is reported: ApplAI
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
    if (!onAllowedHost(url)) continue;
    let path = "", body;
    try {
      const page = await fetch(url, { credentials: "include" });
      if (page.ok) body = { html: await page.text() };
      else [path, body] = ["/failure", { status: page.status }];
    } catch {
      [path, body] = ["/failure", {}]; // not fetched
    }
    await send(fetch, `/api/browser/pages/${offer_id}${path}`, body);
  }
}

// Ask ApplAI for the forms to read; open each in an inactive tab, read its controls there
// (forms.js), close the tab, send the fields back, or the page's text when it shows no control
// the reader knows. A form outside the manifest's hosts is reported, never opened. ApplAI asks
// again for a failed form only after its next scan.
export async function readForms(fetch = globalThis.fetch, browser = globalThis.chrome, wait = sleep) {
  let forms;
  try {
    const asked = await fetch(`${APPLAI}/api/browser/forms`, {});
    if (!asked.ok) return;
    forms = await asked.json();
  } catch {
    return; // ApplAI is not running
  }
  for (const { application_id, url } of forms) {
    let path = "", body;
    try {
      body = await readIn(url, browser, wait);
    } catch (error) {
      const reason = ["not_allowed", "not_loaded"].includes(error?.message) ? error.message : "not_read";
      [path, body] = ["/failure", { reason }];
    }
    const sent = await send(fetch, `/api/browser/forms/${application_id}${path}`, body);
    if (sent?.status === 422) {
      // ApplAI refused the fields: a failure, not a form to open again every minute
      await send(fetch, `/api/browser/forms/${application_id}/failure`, { reason: "not_read" });
    }
  }
}

async function readIn(url, browser, wait) {
  if (!onHosts(url, browser.runtime.getManifest().host_permissions)) throw new Error("not_allowed");
  const tab = await browser.tabs.create({ url, active: false });
  try {
    for (let waited = 0; (await browser.tabs.get(tab.id)).status !== "complete"; waited += 500) {
      if (waited >= LOADED_WITHIN_MS) throw new Error("not_loaded");
      await wait(500);
    }
    let fields = [];
    for (let tries = 0; tries < TRIES && !fields.length; tries++) {
      if (tries) await wait(2000);
      const [read] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: readForm });
      fields = read?.result ?? [];
    }
    if (fields.length) return { fields };
    // No control it knows: ApplAI's agent reads the fields in the page's text.
    const [read] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: readText });
    return { fields, text: read?.result ?? "" };
  } finally {
    await browser.tabs.remove(tab.id).catch(() => {}); // the candidate closed it already
  }
}

async function send(fetch, path, body) {
  try {
    return await fetch(`${APPLAI}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return null; // ApplAI stopped meanwhile: it asks for it again
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Whether url is https on a host of the manifest's "https://*.host/*" patterns.
function onHosts(url, patterns) {
  try {
    const { protocol, hostname } = new URL(url);
    const hosts = patterns.map((pattern) => pattern.match(/^https:\/\/\*\.([^/]+)\/\*$/)?.[1]);
    return protocol === "https:" && hosts.some((host) => host && `.${hostname}`.endsWith(`.${host}`));
  } catch {
    return false; // not a URL
  }
}

const HOSTS = [".jobteaser.com", ".google.com", ".google.fr"];

function onAllowedHost(url) {
  try {
    const { protocol, hostname } = new URL(url);
    return protocol === "https:" && HOSTS.some((host) => `.${hostname}`.endsWith(host));
  } catch {
    return false; // not a URL
  }
}

if (globalThis.chrome?.alarms) {
  // Chrome may clear alarms when it restarts: set the minute's one again when it is missing.
  chrome.alarms.get(READ).then((alarm) => alarm ?? chrome.alarms.create(READ, { periodInMinutes: 1 }));
  let reading = false; // a minute may not be enough to read every form: never two rounds at once
  chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name !== READ || reading) return;
    reading = true;
    try {
      await readPages();
      await readForms();
    } finally {
      reading = false;
    }
  });
}
