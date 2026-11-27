// The ApplAI extension's service worker: it reads offer pages and forms, and fills waiting forms
// with this Chrome's session, blocking every submission in code (ADR 0003). Setup: docs/extension.md.
import { fillForm } from "./fill.js";
import { readForm, readHtml, readText } from "./forms.js";
import { hideOverlay, showOverlay } from "./overlay.js";
import { turnPage } from "./pages.js";
import { isConfirmed, submitForm } from "./send.js";

export const APPLAI = "http://127.0.0.1:8000"; // applai serve's address
const READ = "read-pages";
const LOADED_WITHIN_MS = 30_000; // a form's tab, then it is reported as not loaded
const TURNED_WITHIN_MS = 10_000; // a page's button clicked, then the page is reported as not turned
const TRIES = 3; // a page may draw its form after it loads: read it again 2 s later

// Ask ApplAI for the pages to read, fetch each with the browser's cookies, send back its HTML.
// Only JobTeaser's and Google's, as the manifest's host permissions. Google draws its jobs with
// JavaScript: its page is read in an inactive tab instead. A page that fails is reported: ApplAI
// asks for it again only after its next scan, never every minute.
export async function readPages(fetch = globalThis.fetch, browser = globalThis.chrome, wait = sleep) {
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
      if (onGoogle(url)) {
        body = { html: await inTab(url, browser, wait, (tab) => htmlOf(tab, browser, wait)) };
      } else {
        const page = await fetch(url, { credentials: "include" });
        // A session that expired answers 200 with a sign-in page: that is a failure, not the offer.
        if (page.ok && !leftTheOffer(page, url)) body = { html: await page.text() };
        else [path, body] = ["/failure", { status: page.status }];
      }
    } catch {
      [path, body] = ["/failure", {}]; // not fetched
    }
    await send(fetch, `/api/browser/pages/${offer_id}${path}`, body);
  }
}

// Whether a fetch was redirected to another host or path than the offer's.
function leftTheOffer(page, url) {
  if (!page.redirected) return false;
  try {
    const [to, from] = [new URL(page.url), new URL(url)];
    return to.host !== from.host || to.pathname.replace(/\/$/, "") !== from.pathname.replace(/\/$/, "");
  } catch {
    return true; // no readable final URL: do not trust the page
  }
}

// Ask ApplAI for the forms to read; open each in an inactive tab, read its controls there
// (forms.js), close the tab, send the fields back, or the page's text when it shows no control
// the reader knows. A form outside the manifest's hosts is reported, never opened. ApplAI asks
// again for a failed form only after its next scan. A form whose first page turns (pages.js) is
// read page by page: page 1 is posted as such and its tab kept, where each next page ApplAI
// asks for is read once the fill turned to it.
export async function readForms(fetch = globalThis.fetch, browser = globalThis.chrome, wait = sleep) {
  let forms;
  try {
    const asked = await fetch(`${APPLAI}/api/browser/forms`, {});
    if (!asked.ok) return;
    forms = await asked.json();
  } catch {
    return; // ApplAI is not running
  }
  for (const { application_id, url, page } of forms) {
    let path = "", body;
    try {
      body = page
        ? await onPage(application_id, url, page, browser, wait, async (tab) => ({ ...(await fieldsIn(tab, browser, wait)), page }))
        : await readIn(application_id, url, browser, wait);
    } catch (error) {
      const reason = ["not_allowed", "not_loaded"].includes(error?.message) ? error.message : "not_read";
      [path, body] = ["/failure", page ? { reason, page } : { reason }];
    }
    const sent = await send(fetch, `/api/browser/forms/${application_id}${path}`, body);
    if (sent?.status === 422) {
      // ApplAI refused the fields: a failure, not a form to open again every minute
      await send(fetch, `/api/browser/forms/${application_id}/failure`, page ? { reason: "not_read", page } : { reason: "not_read" });
    }
    // Page 1's tab is kept for the fill only once ApplAI has taken the post.
    if (!page && body.page && !sent?.ok) await dropTab(application_id, browser);
  }
}

// Open url in an inactive ApplAI tab, run work there once it has loaded, then close the tab,
// unless work called keep.
async function inTab(url, browser, wait, work) {
  if (!onHosts(url, browser.runtime.getManifest().host_permissions)) throw new Error("not_allowed");
  const tab = await browser.tabs.create({ url, active: false });
  let kept = false;
  try {
    await groupTab(tab, browser);
    await loaded(tab, browser, wait);
    return await framed(tab, browser, () => work(tab, () => (kept = true)));
  } finally {
    if (!kept) await browser.tabs.remove(tab.id).catch(() => {}); // the candidate closed it already
  }
}

async function loaded(tab, browser, wait) {
  for (let waited = 0; (await browser.tabs.get(tab.id)).status !== "complete"; waited += 500) {
    if (waited >= LOADED_WITHIN_MS) throw new Error("not_loaded");
    await wait(500);
  }
}

// The tab of a form read page by page, and the page it shows, kept in chrome.storage.local as
// Chrome stops an idle service worker. A storage that fails keeps nothing: a new tab is opened.
const pageTabKey = (applicationId) => `page-tab:${applicationId}`;

async function keepTab(applicationId, tab, page, browser) {
  await browser.storage.local.set({ [pageTabKey(applicationId)]: { tabId: tab.id, page } }).catch(() => {});
}

async function dropTab(applicationId, browser) {
  const key = pageTabKey(applicationId);
  const kept = (await browser.storage.local.get(key).catch(() => ({})))[key];
  if (kept) await browser.tabs.remove(kept.tabId).catch(() => {});
  await browser.storage.local.remove(key).catch(() => {});
}

// Run work, framed, in the tab showing page: the tab kept for the form when it shows that page or
// an earlier one, else a new tab at the form's URL, page 1. The pages before it are turned there,
// never filled again: the site keeps what was entered. A tab that fails is left open.
async function onPage(applicationId, url, page, browser, wait, work) {
  if (!onHosts(url, browser.runtime.getManifest().host_permissions)) throw new Error("not_allowed");
  const kept = (await browser.storage.local.get(pageTabKey(applicationId)).catch(() => ({})))[pageTabKey(applicationId)];
  let tab = kept?.page <= page ? await browser.tabs.get(kept.tabId).catch(() => null) : null;
  let shown = kept?.page;
  if (!tab) {
    if (kept) await browser.tabs.remove(kept.tabId).catch(() => {}); // it shows a later page
    tab = await browser.tabs.create({ url, active: false });
    shown = 1;
    await keepTab(applicationId, tab, shown, browser);
  }
  await groupTab(tab, browser);
  await loaded(tab, browser, wait);
  return framed(tab, browser, async () => {
    for (; shown < page; shown++) {
      if ((await turned(tab, browser, wait)) !== "next") throw new Error("not_turned");
      await keepTab(applicationId, tab, shown + 1, browser);
    }
    return work(tab);
  });
}

// Click the page's next button (pages.js) and wait for other fields; "final" clicks nothing.
async function turned(tab, browser, wait) {
  const read = async () => JSON.stringify((await browser.scripting.executeScript({ target: { tabId: tab.id }, func: readForm }))[0]?.result);
  const before = await read();
  const [found] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: turnPage, args: [true] });
  if (found?.result === "final") return "final";
  if (found?.result !== "next") throw new Error("not_turned");
  for (let waited = 0; waited < TURNED_WITHIN_MS; waited += 500) {
    try {
      if ((await browser.tabs.get(tab.id)).status === "complete" && (await read()) !== before) return "next";
    } catch { /* the page is loading */ }
    await wait(500);
  }
  throw new Error("not_turned");
}

// Google's script draws the jobs after the page loads.
async function htmlOf(tab, browser, wait) {
  await wait(2000);
  const [read] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: readHtml });
  return read?.result ?? "";
}

async function readIn(applicationId, url, browser, wait) {
  return inTab(url, browser, wait, async (tab, keep) => {
    const read = await fieldsIn(tab, browser, wait);
    const [found] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: turnPage, args: [false] });
    if (found?.result !== "next") return read;
    keep();
    await keepTab(applicationId, tab, 1, browser);
    return { ...read, page: 1 };
  });
}

async function fieldsIn(tab, browser, wait) {
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
}

// Ask ApplAI for forms to fill; fetch attachments, fill in an inactive tab (fill.js), capture a
// screenshot, leave the tab open for the candidate, and post the filled state. A form filled
// page by page is filled in the tab showing the page served, then turned (pages.js), and the
// page posted with its number; the final page is not turned, and posted with its screenshot.
export async function fillForms(fetch = globalThis.fetch, browser = globalThis.chrome, wait = sleep) {
  let fills;
  try {
    const asked = await fetch(`${APPLAI}/api/browser/fills`, {});
    if (!asked.ok) return;
    fills = await asked.json();
  } catch { return; }
  for (const { application_id, url, page, actions } of fills) {
    let path = "", body;
    try {
      body = page
        ? await fillPage(application_id, url, page, actions, fetch, browser, wait)
        : await fillIn(application_id, url, actions, fetch, browser, wait);
    } catch (error) {
      const known = ["not_allowed", "not_loaded", "not_turned"];
      [path, body] = ["/failure", { reason: known.includes(error?.message) ? error.message : "not_filled" }];
    }
    const sent = await send(fetch, `/api/browser/fills/${application_id}${path}`, body);
    if (sent?.status === 422) {
      await send(fetch, `/api/browser/fills/${application_id}/failure`, { reason: "not_filled" });
    }
  }
}

async function fillIn(applicationId, url, actions, fetch, browser, wait) {
  if (!onHosts(url, browser.runtime.getManifest().host_permissions)) throw new Error("not_allowed");
  const files = await fetchFiles(applicationId, actions, fetch);
  const tab = await browser.tabs.create({ url, active: false });
  let filled = false;
  try {
    await groupTab(tab, browser);
    await loaded(tab, browser, wait);
    // The frame is gone before the screenshot: it shows only the page.
    const fields = await framed(tab, browser, () => filledIn(tab, actions, files, browser, wait));
    const screenshot = await captureTab(tab, browser);
    filled = true;
    return screenshot ? { fields, screenshot } : { fields };
  } finally {
    // A filled form keeps its tab: Send uses it (ADR 0003). A failed fill leaves nothing open.
    if (!filled) await browser.tabs.remove(tab.id).catch(() => {}); // the candidate closed it already
  }
}

// A page filled and turned, or the final page: its tab stays open, filled or failed (ADR 0003).
async function fillPage(applicationId, url, page, actions, fetch, browser, wait) {
  if (!onHosts(url, browser.runtime.getManifest().host_permissions)) throw new Error("not_allowed");
  const files = await fetchFiles(applicationId, actions, fetch);
  const [tab, fields, found] = await onPage(applicationId, url, page, browser, wait, async (tab) => {
    const fields = await filledIn(tab, actions, files, browser, wait);
    const found = await turned(tab, browser, wait);
    if (found === "next") await keepTab(applicationId, tab, page + 1, browser);
    return [tab, fields, found];
  });
  if (found === "next") return { fields, page };
  const screenshot = await captureTab(tab, browser);
  return { fields, ...(screenshot ? { screenshot } : {}), page, final: true };
}

async function filledIn(tab, actions, files, browser, wait) {
  let fields = [];
  for (let tries = 0; tries < TRIES && !fields.length; tries++) {
    if (tries) await wait(2000);
    const [done] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: fillForm, args: [actions, files] });
    fields = done?.result ?? [];
  }
  return fields;
}

// A click's result until ApplAI has taken it: submit is never clicked twice for one application.
// Kept in chrome.storage.local, as Chrome stops an idle service worker and may itself close; this
// in-memory copy covers a storage write that failed.
const pendingSendReports = new Map();
const sendReportKey = (applicationId) => `send-report:${applicationId}`;

// Submit forms explicitly authorized by the candidate, using only already-open tabs (ADR 0003).
// A stored result is posted again and nothing is clicked; a storage read that fails clicks nothing.
export async function sendForms(
  fetch = globalThis.fetch,
  browser = globalThis.chrome,
  wait = sleep,
  pending = pendingSendReports,
) {
  let sends;
  try {
    const asked = await fetch(`${APPLAI}/api/browser/sends`, {});
    if (!asked.ok) return;
    sends = await asked.json();
  } catch { return; }
  for (const { application_id, url } of sends) {
    const key = sendReportKey(application_id);
    let report = pending.get(application_id) ?? (await browser.storage.local.get(key))[key];
    if (!report) {
      let path = "", body = {};
      try {
        const tab = ((await browser.tabs.query({})) ?? []).find((t) => sameFormUrl(t.url, url));
        if (!tab) [path, body] = ["/failure", { reason: "no_tab" }];
        else {
          await groupTab(tab, browser); // the candidate may have moved it out of the group
          [path, body] = await framed(tab, browser, async () => {
            const [ran] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: submitForm });
            const res = ran?.result;
            if (!res?.ok) return ["/failure", { reason: res?.reason ?? "not_sent" }];
            if (!(await confirmedIn(tab.id, url, res.beforeText ?? "", browser, wait))) {
              return ["/failure", { reason: "not_confirmed" }];
            }
            return ["", {}];
          });
        }
      } catch {
        [path, body] = ["/failure", { reason: "not_sent" }];
      }
      report = { path, body };
      pending.set(application_id, report);
      await browser.storage.local.set({ [key]: report }).catch(() => {}); // kept in memory meanwhile
    }
    const posted = await send(fetch, `/api/browser/sends/${application_id}${report.path}`, report.body);
    if (posted && (posted.ok || posted.status === 404 || posted.status === 409)) {
      pending.delete(application_id);
      await browser.storage.local.remove(key).catch(() => {}); // posted again next pass, then removed
    }
  }
}

const GROUP = { title: "ApplAI", color: "green" };

// Post the URLs of the tabs in the ApplAI tab group, and only those, so ApplAI shows whether a filled
// form's tab is still open. A group list that cannot be read posts nothing: ApplAI then says
// "unknown" after a while, never "closed". A post that fails changes nothing there.
export async function reportTabs(fetch = globalThis.fetch, browser = globalThis.chrome) {
  let urls;
  try {
    const groups = new Set((await browser.tabGroups.query({ title: GROUP.title })).map((g) => g.id));
    urls = ((await browser.tabs.query({})) ?? []).filter((t) => t.url && groups.has(t.groupId)).map((t) => t.url);
  } catch {
    return;
  }
  await send(fetch, "/api/browser/tabs", { urls });
}

// Put a tab the extension uses in its window's ApplAI tab group, made when missing, so the candidate
// sees which tabs it uses. A tab already in the group is left there. Never fatal: a tab that is not
// grouped is still read, filled or submitted.
async function groupTab(tab, browser) {
  try {
    const where = tab.windowId === undefined ? {} : { windowId: tab.windowId };
    const [group] = await browser.tabGroups.query({ ...where, title: GROUP.title });
    if (!group) {
      const created = await browser.tabs.group({ tabIds: [tab.id], createProperties: where });
      await browser.tabGroups.update(created, GROUP);
    } else if (tab.groupId !== group.id) {
      await browser.tabs.group({ groupId: group.id, tabIds: [tab.id] });
    }
  } catch { /* the tab goes on ungrouped */ }
}

// Run work while the page shows the frame of a tab in use, and remove it when the work ends, success
// or failure. A frame that cannot be drawn or removed (a page Chrome does not script) is not an error.
async function framed(tab, browser, work) {
  const run = async (func) => {
    try {
      await browser.scripting.executeScript({ target: { tabId: tab.id }, func });
    } catch { /* the page is gone or not scriptable */ }
  };
  await run(showOverlay);
  try {
    return await work();
  } finally {
    await run(hideOverlay);
  }
}

async function confirmedIn(tabId, url, beforeText, browser, wait) {
  for (let tries = 0; tries <= TRIES; tries++) {
    if (tries) await wait(1000);
    try {
      const tab = await browser.tabs.get(tabId);
      if (tab?.url && !sameFormUrl(tab.url, url)) return true;
      if (tab?.status === "complete") {
        const [chk] = await browser.scripting.executeScript({
          target: { tabId }, func: isConfirmed, args: [url, beforeText],
        });
        if (chk?.result) return true;
      }
    } catch { /* tab may be navigating */ }
  }
  return false;
}

function sameFormUrl(a, b) {
  if (!a || !b) return false;
  try {
    const u1 = new URL(a), u2 = new URL(b);
    return u1.origin === u2.origin && u1.pathname.replace(/\/$/, "") === u2.pathname.replace(/\/$/, "") && u1.search === u2.search;
  } catch { return a === b; }
}

async function fetchFiles(applicationId, actions, fetch) {
  const slots = [...new Set(actions.filter((a) => a.kind === "attach").map((a) => String(a.value)))];
  const files = {};
  for (const slot of slots) {
    const res = await fetch(`${APPLAI}/api/browser/fills/${applicationId}/files/${slot}`, {});
    if (!res.ok) throw new Error("not_filled");
    const match = (res.headers?.get("Content-Disposition") ?? "").match(/filename="?([^";]+)"?/i);
    let bin = "";
    for (const byte of new Uint8Array(await res.arrayBuffer())) bin += String.fromCharCode(byte);
    files[slot] = { name: match?.[1] ?? `${slot}.pdf`, type: res.headers?.get("Content-Type") ?? "application/pdf", base64: btoa(bin) };
  }
  return files;
}

async function captureTab(tab, browser) {
  let previous = null;
  try {
    previous = (await browser.tabs.query?.({ active: true, windowId: tab.windowId }))?.[0]?.id ?? null;
    await browser.tabs.update(tab.id, { active: true });
    const dataUrl = await browser.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    return dataUrl?.replace(/^data:image\/png;base64,/, "") || null;
  } catch { return null; } finally {
    if (previous !== null && previous !== tab.id) await browser.tabs.update(previous, { active: true }).catch(() => {});
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
const onGoogle = (url) => onHosts(url, ["https://*.google.com/*", "https://*.google.fr/*"]);

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
      await fillForms();
      await sendForms();
      await reportTabs();
    } finally {
      reading = false;
    }
  });
}
