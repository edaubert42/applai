// The extension's loop with fetch mocked, run by Node's test runner (tests/test_extension.py).
import assert from "node:assert/strict";
import { test } from "node:test";

import { APPLAI, fillForms, readForms, readPages, sendForms } from "./background.js";
import { fillForm } from "./fill.js";
import { readForm, readHtml, readText } from "./forms.js";
import manifest from "./manifest.json" with { type: "json" };
import { hideOverlay, showOverlay } from "./overlay.js";
import { isConfirmed, submitForm } from "./send.js";

const OFFER = "https://ecole-des-tilleuls.jobteaser.com/fr/job-offers/4521-data-engineer";

async function readWith(pages, status = 200) {
  const calls = [];
  await readPages(async (url, options) => {
    calls.push([url, options]);
    if (url === `${APPLAI}/api/browser/pages`) return Response.json(pages);
    return new Response(url.startsWith(APPLAI) ? null : `<html>${url}</html>`, { status });
  });
  return calls;
}

test("it reports a page that fails to ApplAI, which asks for it again after the next scan", async () => {
  const report = { method: "POST", headers: { "Content-Type": "application/json" } };

  assert.deepEqual(await readWith([{ offer_id: 7, url: OFFER }], 403), [
    [`${APPLAI}/api/browser/pages`, {}],
    [OFFER, { credentials: "include" }],
    [`${APPLAI}/api/browser/pages/7/failure`, { ...report, body: JSON.stringify({ status: 403 }) }],
  ]);
});

test("it asks ApplAI, fetches each page with the browser's session, and posts its HTML", async () => {
  const html = JSON.stringify({ html: `<html>${OFFER}</html>` });
  const headers = { "Content-Type": "application/json" };

  assert.deepEqual(await readWith([{ offer_id: 7, url: OFFER }]), [
    [`${APPLAI}/api/browser/pages`, {}],
    [OFFER, { credentials: "include" }],
    [`${APPLAI}/api/browser/pages/7`, { method: "POST", headers, body: html }],
  ]);
});

const SIGN_IN = "https://www.jobteaser.com/fr/users/sign_in";

// A fetch answering 200 with this final URL: a Response's url and redirected are read-only, so a plain object.
async function readRedirected(finalUrl, redirected = true) {
  const calls = [];
  await readPages(async (url, options) => {
    calls.push([url, options]);
    if (url === `${APPLAI}/api/browser/pages`) return Response.json([{ offer_id: 7, url: OFFER }]);
    if (url !== OFFER) return new Response(null, { status: 204 });
    return { ok: true, status: 200, redirected, url: finalUrl, text: async () => "<html>Connexion</html>" };
  });
  return calls;
}

test("a page redirected outside the offer's host or path is a failure, never its HTML", async () => {
  const report = { method: "POST", headers: { "Content-Type": "application/json" } };

  assert.deepEqual((await readRedirected(SIGN_IN)).slice(2), [
    [`${APPLAI}/api/browser/pages/7/failure`, { ...report, body: JSON.stringify({ status: 200 }) }],
  ]);
  const elsewhere = await readRedirected("https://ecole-des-tilleuls.jobteaser.com/fr/job-offers/9-other");
  assert.equal(elsewhere[2][0], `${APPLAI}/api/browser/pages/7/failure`);
});

const GOOGLE = "https://www.google.fr/search?q=data+engineer&ibp=htl%3Bjobs";
const DRAWN = "<html>Postuler sur Lever</html>"; // the jobs Google's script draws, a fetch never sees

async function readGoogle(options) {
  const calls = [];
  const chrome = chromeWith(calls, options);
  chrome.scripting.executeScript = async ({ target, func }) =>
    func === readHtml ? calls.push(["run", target]) && [{ result: DRAWN }] : [];
  await readPages(
    async (url, init) => {
      calls.push([url, init]);
      return url === `${APPLAI}/api/browser/pages` ? Response.json([{ offer_id: 7, url: GOOGLE }]) : new Response(null, { status: 204 });
    },
    chrome,
    async () => {}, // no real wait
  );
  return plain(calls);
}

test("a Google page is read in an inactive tab, where its jobs are drawn, never fetched", async () => {
  assert.deepEqual(await readGoogle(), [
    [`${APPLAI}/api/browser/pages`, {}],
    ["open", { url: GOOGLE, active: false }],
    ["run", { tabId: 3 }],
    ["close", 3],
    [`${APPLAI}/api/browser/pages/7`, { ...SENT, body: JSON.stringify({ html: DRAWN }) }],
  ]);
});

test("a Google tab that does not load is reported as a page that was not fetched", async () => {
  assert.deepEqual((await readGoogle({ status: "loading" })).slice(-2), [
    ["close", 3],
    [`${APPLAI}/api/browser/pages/7/failure`, { ...SENT, body: JSON.stringify({}) }],
  ]);
});

test("a page answering 200 without a redirect, or redirected within the offer's path, posts its HTML", async () => {
  const html = JSON.stringify({ html: "<html>Connexion</html>" });
  for (const calls of [await readRedirected(OFFER, false), await readRedirected(`${OFFER}/`)]) {
    assert.equal(calls[2][0], `${APPLAI}/api/browser/pages/7`);
    assert.equal(calls[2][1].body, html);
  }
});

test("it fetches no page outside JobTeaser and Google, whatever ApplAI asks", async () => {
  const calls = await readWith([{ offer_id: 8, url: "https://jobs.example.org/8" }]);

  assert.deepEqual(calls, [[`${APPLAI}/api/browser/pages`, {}]]);
});

const LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply";
const FIELDS = [{ label: "CV", type: "file", required: true, options: [] }];
const SENT = { method: "POST", headers: { "Content-Type": "application/json" } };

// Tab groups and the overlay, faked. Every group and overlay call goes to the log in order with the
// other calls; the helpers below return the other calls alone, and the log as `.log`.
const MARKS = ["group", "group-update", "overlay"];

function faked(log, { groups = [], inGroup = new Map() } = {}) {
  let next = 100;
  return {
    tabs: {
      group: async ({ tabIds, groupId, createProperties }) => {
        const id = groupId ?? next++;
        if (groupId === undefined) groups.push({ id, windowId: createProperties?.windowId });
        for (const tabId of tabIds) inGroup.set(tabId, id);
        log.push(["group", id, tabIds, createProperties]);
        return id;
      },
    },
    tabGroups: {
      query: async ({ windowId, title }) =>
        groups.filter((g) => g.title === title && (windowId === undefined || g.windowId === windowId)),
      update: async (id, props) =>
        log.push(["group-update", id, props]) && Object.assign(groups.find((g) => g.id === id), props),
    },
    groupOf: (id) => inGroup.get(id) ?? -1,
    // Whether the script is an overlay's: logged as such, nothing else runs.
    overlay: (tabId, func) => {
      if (func !== showOverlay && func !== hideOverlay) return false;
      log.push(["overlay", tabId, func === showOverlay ? "show" : "hide"]);
      return true;
    },
  };
}

function plain(log) {
  const calls = log.filter(([kind]) => !MARKS.includes(kind));
  return Object.defineProperty(calls, "log", { value: log }); // not enumerable: deepEqual ignores it
}

// Chrome's tabs and scripting, mocked: a tab with this status, a form with these fields and text.
function chromeWith(calls, { status = "complete", fields = FIELDS, text = "", groups = [] } = {}) {
  const fake = faked(calls, { groups });
  let opened = 2;
  return {
    runtime: { getManifest: () => manifest },
    tabs: {
      ...fake.tabs,
      create: async (options) => calls.push(["open", options]) && { id: ++opened, windowId: 1, groupId: -1 },
      get: async (id) => ({ id, status, windowId: 1, groupId: fake.groupOf(id) }),
      remove: async (id) => calls.push(["close", id]),
    },
    tabGroups: fake.tabGroups,
    scripting: {
      executeScript: async ({ target, func }) =>
        fake.overlay(target.tabId, func) ||
        (calls.push(["run", target, func]) && [{ result: func === readText ? text : fields }]),
    },
  };
}

async function readFormsWith(forms, options, answer = 204) {
  const calls = [];
  await readForms(
    async (url, init) => {
      calls.push([url, init]);
      if (url === `${APPLAI}/api/browser/forms`) return Response.json(forms);
      return new Response(null, { status: url.endsWith("/failure") ? 204 : answer });
    },
    chromeWith(calls, options),
    async () => {}, // no real wait
  );
  return plain(calls);
}

test("it opens each form in an inactive tab, reads it there, closes the tab and posts its fields", async () => {
  assert.deepEqual(await readFormsWith([{ application_id: 5, url: LEVER }]), [
    [`${APPLAI}/api/browser/forms`, {}],
    ["open", { url: LEVER, active: false }],
    ["run", { tabId: 3 }, readForm],
    ["close", 3],
    [`${APPLAI}/api/browser/forms/5`, { ...SENT, body: JSON.stringify({ fields: FIELDS }) }],
  ]);
});

test("it reports a failure when ApplAI refuses the fields, so the form is not opened every minute", async () => {
  const calls = await readFormsWith([{ application_id: 5, url: LEVER }], {}, 422);

  assert.deepEqual(calls.slice(-2), [
    [`${APPLAI}/api/browser/forms/5`, { ...SENT, body: JSON.stringify({ fields: FIELDS }) }],
    [`${APPLAI}/api/browser/forms/5/failure`, { ...SENT, body: JSON.stringify({ reason: "not_read" }) }],
  ]);
});

test("it opens no form outside its hosts, and reports a form that does not load", async () => {
  const outside = await readFormsWith([{ application_id: 5, url: "https://jobs.example.org/5" }]);
  const loading = await readFormsWith([{ application_id: 6, url: LEVER }], { status: "loading" });

  assert.deepEqual(outside, [
    [`${APPLAI}/api/browser/forms`, {}],
    [`${APPLAI}/api/browser/forms/5/failure`, { ...SENT, body: JSON.stringify({ reason: "not_allowed" }) }],
  ]);
  assert.deepEqual(loading.slice(1), [
    ["open", { url: LEVER, active: false }],
    ["close", 3],
    [`${APPLAI}/api/browser/forms/6/failure`, { ...SENT, body: JSON.stringify({ reason: "not_loaded" }) }],
  ]);
});

test("a form with no control it knows sends the page's text, and only reads in the page", async () => {
  const text = "Pivoine Analytics recrute. Nom complet * CV * Envoyer";
  const calls = await readFormsWith([{ application_id: 5, url: LEVER }], { fields: [], text });

  assert.deepEqual(calls.slice(1), [
    ["open", { url: LEVER, active: false }],
    ["run", { tabId: 3 }, readForm],
    ["run", { tabId: 3 }, readForm],
    ["run", { tabId: 3 }, readForm],
    ["run", { tabId: 3 }, readText],
    ["close", 3],
    [`${APPLAI}/api/browser/forms/5`, { ...SENT, body: JSON.stringify({ fields: [], text }) }],
  ]);
  // Only the two readers run in the page; that neither dispatches an input, click or submit
  // event is tested on a fictional page by the frontend (frontend/src/extension-forms.test.ts).
  const run = new Set(calls.filter(([kind]) => kind === "run").map(([, , func]) => func));
  assert.deepEqual(run, new Set([readForm, readText]));
});

const PLAN = [
  { field: 0, label: "Nom complet", kind: "fill", value: "Camille Moreau" },
  { field: 1, label: "CV", kind: "attach", value: "cv" },
];
const FILLED = [{ field: 0, value: "Camille Moreau" }, { field: 1, file: "cv.pdf" }];

function fillChromeWith(calls, { status = "complete", filled = FILLED, captureError = false, throws = false, groups = [], noGroups = false } = {}) {
  const fake = faked(calls, { groups });
  let opened = 8;
  return {
    runtime: { getManifest: () => manifest },
    tabGroups: noGroups ? undefined : fake.tabGroups, // as a Chrome without the permission
    tabs: {
      ...fake.tabs,
      create: async (options) => calls.push(["open", options]) && { id: ++opened, windowId: 1, groupId: -1 },
      get: async (id) => ({ id, status, windowId: 1, groupId: fake.groupOf(id) }),
      query: async () => [{ id: 1, active: true, windowId: 1 }],
      update: async (id, props) => calls.push(["update", id, props]) && { id },
      captureVisibleTab: async (windowId, opts) => {
        calls.push(["capture", windowId, opts]);
        if (captureError) throw new Error("minimized");
        return "data:image/png;base64,iVBORw0KGgo=";
      },
      remove: async (id) => calls.push(["close", id]),
    },
    scripting: {
      executeScript: async ({ target, func, args }) => {
        if (fake.overlay(target.tabId, func)) return [{ result: null }];
        calls.push(["run", target, func, args]);
        if (throws) throw new Error("tab crashed");
        return [{ result: filled }];
      },
    },
  };
}

async function fillFormsWith(fills, options = {}) {
  const calls = [];
  await fillForms(
    async (url, init) => {
      calls.push([url, init]);
      if (url === `${APPLAI}/api/browser/fills`) return Response.json(fills);
      if (url.includes("/files/cv")) {
        return new Response(new Uint8Array([37, 80, 68, 70]), {
          status: 200,
          headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="cv.pdf"' },
        });
      }
      return new Response(null, { status: 204 });
    },
    fillChromeWith(calls, options),
    async () => {},
  );
  return plain(calls);
}

test("fillForms fetches files, fills, captures screenshot, leaves tab open, and posts state", async () => {
  const calls = await fillFormsWith([{ application_id: 12, url: LEVER, actions: PLAN }]);
  assert.equal(calls.some(([kind]) => kind === "close"), false);
  const run = calls.find(([kind]) => kind === "run");
  assert.equal(run[2], fillForm);
  assert.deepEqual(run[3][0], PLAN);
  assert.equal(run[3][1].cv.name, "cv.pdf");
  assert.equal(run[3][1].cv.base64, Buffer.from("%PDF").toString("base64"));
  assert.deepEqual(calls.filter(([k]) => k === "update" || k === "capture"), [
    ["update", 9, { active: true }], ["capture", 1, { format: "png" }], ["update", 1, { active: true }],
  ]);
  assert.deepEqual(calls.at(-1), [
    `${APPLAI}/api/browser/fills/12`, { ...SENT, body: JSON.stringify({ fields: FILLED, screenshot: "iVBORw0KGgo=" }) },
  ]);
});

test("fillForms posts without screenshot on capture failure, and reports load or host errors", async () => {
  const noShot = await fillFormsWith([{ application_id: 12, url: LEVER, actions: PLAN }], { captureError: true });
  assert.deepEqual(noShot.at(-1), [`${APPLAI}/api/browser/fills/12`, { ...SENT, body: JSON.stringify({ fields: FILLED }) }]);
  const outside = await fillFormsWith([{ application_id: 13, url: "https://jobs.example.org/apply", actions: PLAN }]);
  assert.deepEqual(outside, [
    [`${APPLAI}/api/browser/fills`, {}],
    [`${APPLAI}/api/browser/fills/13/failure`, { ...SENT, body: JSON.stringify({ reason: "not_allowed" }) }],
  ]);
  const loading = await fillFormsWith([{ application_id: 14, url: LEVER, actions: PLAN }], { status: "loading" });
  assert.deepEqual(loading.at(-1), [`${APPLAI}/api/browser/fills/14/failure`, { ...SENT, body: JSON.stringify({ reason: "not_loaded" }) }]);
});

test("a failed fill closes its tab: a timeout reports not_loaded, a throwing script not_filled", async () => {
  const loading = await fillFormsWith([{ application_id: 14, url: LEVER, actions: PLAN }], { status: "loading" });
  assert.deepEqual(loading.filter(([k]) => k === "close"), [["close", 9]]);
  assert.deepEqual(loading.at(-1), [`${APPLAI}/api/browser/fills/14/failure`, { ...SENT, body: JSON.stringify({ reason: "not_loaded" }) }]);

  const throwing = await fillFormsWith([{ application_id: 15, url: LEVER, actions: PLAN }], { throws: true });
  assert.deepEqual(throwing.filter(([k]) => k === "close"), [["close", 9]]);
  assert.deepEqual(throwing.at(-1), [`${APPLAI}/api/browser/fills/15/failure`, { ...SENT, body: JSON.stringify({ reason: "not_filled" }) }]);
});

function fakeDom({ captcha = false, buttons = [], text = "Formulaire", href = LEVER, onClick } = {}) {
  const loc = { href };
  const doc = {
    defaultView: { location: loc },
    body: { textContent: text },
    querySelector: () => (captcha ? {} : null),
    querySelectorAll: () => buttons.map((b) => ({
      disabled: false, hidden: false, getAttribute: () => null,
      click: () => { b.clicked = true; onClick?.(doc, loc); }, ...b,
    })),
  };
  return doc;
}

test("submitForm and isConfirmed ignore pre-existing text, stop on captcha/no button, and detect new confirmation", () => {
  const runSubmit = new Function(`return (${submitForm.toString()})`)();
  const runConfirm = new Function(`return (${isConfirmed.toString()})`)();
  const capBtn = { clicked: false };
  assert.deepEqual(runSubmit(fakeDom({ captcha: true, buttons: [capBtn] })), { ok: false, reason: "captcha" });
  assert.equal(capBtn.clicked, false);
  assert.deepEqual(runSubmit(fakeDom({ buttons: [{ disabled: true }, { hidden: true }] })), { ok: false, reason: "no_button" });

  // Pre-existing "Thank you" / "confirmation" on the form never causes a false positive.
  const preText = "Thank you for reading our confirmation policy.";
  const unchanged = fakeDom({ buttons: [{}], text: preText });
  const r0 = runSubmit(unchanged);
  assert.equal(r0.ok, true);
  assert.equal(runConfirm(`${LEVER}/`, r0.beforeText, unchanged), false);

  // Newly appearing confirmation text or normalized URL change confirms submission.
  const btn = {};
  const byWords = fakeDom({ buttons: [btn], onClick: (doc) => { doc.body.textContent = "Merci, candidature envoyée !"; } });
  const r1 = runSubmit(byWords);
  assert.equal(btn.clicked, true);
  assert.equal(runConfirm(LEVER, r1.beforeText, byWords), true);

  const byUrl = fakeDom({ buttons: [{}], onClick: (_d, loc) => { loc.href = `${LEVER}/thanks`; } });
  const r2 = runSubmit(byUrl);
  assert.equal(runConfirm(LEVER, r2.beforeText, byUrl), true);
});

async function sendFormsWith(
  sends,
  { tabs = [{ id: 7, url: LEVER, status: "complete", windowId: 1, groupId: -1 }], groups = [], submit = { ok: true, beforeText: "Form" }, tabAfter, confirmedAfter = true, postStatus = 204, pending = new Map(), stored = {} } = {},
) {
  const calls = [];
  const fake = faked(calls, { groups });
  const chrome = {
    tabGroups: fake.tabGroups,
    storage: {
      // chrome.storage.local: what it holds (`stored`) survives a service-worker restart
      local: {
        get: async (key) => (key in stored ? { [key]: structuredClone(stored[key]) } : {}),
        set: async (items) => calls.push(["store", Object.keys(items)]) && Object.assign(stored, structuredClone(items)),
        remove: async (key) => { delete stored[key]; },
      },
    },
    tabs: {
      ...fake.tabs,
      create: async () => { throw new Error("must never open a tab"); },
      query: async (q) => calls.push(["query", q]) && tabs,
      get: async (id) => ({ id, status: "complete", url: tabAfter ?? LEVER }),
    },
    scripting: {
      executeScript: async ({ target, func }) =>
        fake.overlay(target.tabId, func) ||
        (calls.push(["run", target, func]) && [{ result: func === isConfirmed ? confirmedAfter : submit }]),
    },
  };
  await sendForms(
    async (url, init) => {
      calls.push([url, init]);
      if (url === `${APPLAI}/api/browser/sends`) return Response.json(sends);
      if (postStatus === null) throw new Error("down");
      return new Response(null, { status: postStatus });
    },
    chrome,
    async () => {},
    pending,
  );
  return plain(calls);
}

test("sendForms uses open tabs only, reports failures, and retries reporting without double-submitting", async () => {
  const noTab = await sendFormsWith([{ application_id: 21, url: LEVER }], { tabs: [{ id: 2, url: OFFER }] });
  assert.deepEqual(noTab.at(-1), [`${APPLAI}/api/browser/sends/21/failure`, { ...SENT, body: JSON.stringify({ reason: "no_tab" }) }]);

  const ok = await sendFormsWith([{ application_id: 22, url: LEVER }]);
  assert.deepEqual(ok.at(-1), [`${APPLAI}/api/browser/sends/22`, { ...SENT, body: "{}" }]);

  const captcha = await sendFormsWith([{ application_id: 24, url: LEVER }], { submit: { ok: false, reason: "captcha" } });
  assert.deepEqual(captcha.at(-1), [`${APPLAI}/api/browser/sends/24/failure`, { ...SENT, body: JSON.stringify({ reason: "captcha" }) }]);

  const unconfirmed = await sendFormsWith([{ application_id: 25, url: LEVER }], { confirmedAfter: false });
  assert.deepEqual(unconfirmed.at(-1), [`${APPLAI}/api/browser/sends/25/failure`, { ...SENT, body: JSON.stringify({ reason: "not_confirmed" }) }]);

  // If the report POST fails, the next poll retries the report and NEVER calls submitForm again.
  const pending = new Map();
  await sendFormsWith([{ application_id: 26, url: LEVER }], { postStatus: null, pending });
  const retry = await sendFormsWith([{ application_id: 26, url: LEVER }], { postStatus: 204, pending });
  assert.equal(retry.some(([k, , fn]) => k === "run" && fn === submitForm), false);
  assert.deepEqual(retry.at(-1), [`${APPLAI}/api/browser/sends/26`, { ...SENT, body: "{}" }]);
});

test("a click's unreported result is stored before it is posted and survives a service-worker restart", async () => {
  const stored = {};
  const first = await sendFormsWith([{ application_id: 27, url: LEVER }], { postStatus: null, stored });
  const post = first.findIndex(([url]) => url === `${APPLAI}/api/browser/sends/27`);
  const store = first.findIndex(([k]) => k === "store");
  assert.ok(store !== -1 && store < post, "stored before it is posted");

  // The worker restarted: nothing in memory, only what chrome.storage kept.
  const restarted = await sendFormsWith([{ application_id: 27, url: LEVER }], { postStatus: 503, pending: new Map(), stored });
  assert.equal(restarted.some(([k, , fn]) => k === "run" && fn === submitForm), false);
  assert.deepEqual(restarted.at(-1), [`${APPLAI}/api/browser/sends/27`, { ...SENT, body: "{}" }]);
  assert.equal(Object.keys(stored).length, 1, "kept while ApplAI has not taken it");
});

test("a stored report is removed once ApplAI answers 2xx, 404 or 409", async () => {
  for (const status of [204, 404, 409]) {
    const stored = {};
    await sendFormsWith([{ application_id: 28, url: LEVER }], { postStatus: null, stored });
    assert.equal(Object.keys(stored).length, 1);
    await sendFormsWith([{ application_id: 28, url: LEVER }], { postStatus: status, pending: new Map(), stored });
    assert.deepEqual(stored, {}, `removed after ${status}`);
  }
});

// The ApplAI tab group, and the frame around a tab the extension uses.
const APPLAI_GROUP = { title: "ApplAI", color: "green" };
const grouping = (log) => log.filter(([kind]) => kind === "group" || kind === "group-update");
const firstOf = (log, kind) => log.findIndex(([k]) => k === kind);
const IN_GROUP = [{ id: 100, windowId: 1, title: "ApplAI", color: "green" }];
const TAB = { id: 7, url: LEVER, status: "complete", windowId: 1 };

// The calls of these kinds in order, each as a short label: "overlay show", "run fillForm", "close".
function steps(log, kinds) {
  const label = ([kind, , third]) => (kind === "run" ? `run ${third.name}` : kind === "overlay" ? `overlay ${third}` : kind);
  return log.filter(([kind]) => kinds.includes(kind)).map(label);
}

test("a form read adds its tab to a group titled ApplAI, a second tab in the window joins the same group", async () => {
  const { log } = await readFormsWith([{ application_id: 5, url: LEVER }, { application_id: 6, url: LEVER }]);

  assert.deepEqual(grouping(log), [
    ["group", 100, [3], { windowId: 1 }],
    ["group-update", 100, APPLAI_GROUP],
    ["group", 100, [4], undefined],
  ]);
  assert.ok(firstOf(log, "group") < firstOf(log, "run"), "grouped before it is read");
});

test("a form fill adds its tab to a group titled ApplAI, a second tab in the window joins the same group", async () => {
  const { log } = await fillFormsWith([
    { application_id: 12, url: LEVER, actions: PLAN },
    { application_id: 13, url: LEVER, actions: PLAN },
  ]);

  assert.deepEqual(grouping(log), [
    ["group", 100, [9], { windowId: 1 }],
    ["group-update", 100, APPLAI_GROUP],
    ["group", 100, [10], undefined],
  ]);
  assert.ok(firstOf(log, "group") < firstOf(log, "run"), "grouped before it is filled");
});

test("a tab group that cannot be made never stops a fill", async () => {
  const { log } = await fillFormsWith([{ application_id: 12, url: LEVER, actions: PLAN }], { noGroups: true });

  assert.deepEqual(log.at(-1), [
    `${APPLAI}/api/browser/fills/12`,
    { ...SENT, body: JSON.stringify({ fields: FILLED, screenshot: "iVBORw0KGgo=" }) },
  ]);
});

test("sendForms puts back into the group a tab that is not in it, before it submits", async () => {
  const { log } = await sendFormsWith([{ application_id: 30, url: LEVER }], { tabs: [{ ...TAB, groupId: -1 }], groups: IN_GROUP });

  assert.deepEqual(grouping(log), [["group", 100, [7], undefined]]);
  assert.ok(firstOf(log, "group") < log.findIndex(([k, , fn]) => k === "run" && fn === submitForm));
  assert.deepEqual(log.at(-1), [`${APPLAI}/api/browser/sends/30`, { ...SENT, body: "{}" }]);
});

test("sendForms leaves a tab already in the group alone, and makes the group when none is there", async () => {
  const inside = await sendFormsWith([{ application_id: 31, url: LEVER }], { tabs: [{ ...TAB, groupId: 100 }], groups: IN_GROUP });
  const none = await sendFormsWith([{ application_id: 32, url: LEVER }], { tabs: [{ ...TAB, groupId: -1 }] });

  assert.deepEqual(grouping(inside.log), []);
  assert.deepEqual(grouping(none.log), [["group", 100, [7], { windowId: 1 }], ["group-update", 100, APPLAI_GROUP]]);
});

test("a fill shows the frame before fillForm runs and removes it before the screenshot", async () => {
  const { log } = await fillFormsWith([{ application_id: 12, url: LEVER, actions: PLAN }]);

  assert.deepEqual(steps(log, ["overlay", "run", "update", "capture"]), [
    "overlay show", "run fillForm", "overlay hide", "update", "capture", "update",
  ]);
  assert.deepEqual(log.filter(([k]) => k === "overlay").map(([, tabId]) => tabId), [9, 9]);
});

test("a fill that throws removes the frame, then closes the tab", async () => {
  const { log } = await fillFormsWith([{ application_id: 15, url: LEVER, actions: PLAN }], { throws: true });

  assert.deepEqual(steps(log, ["overlay", "run", "close"]), ["overlay show", "run fillForm", "overlay hide", "close"]);
});

test("a form read shows the frame while it reads, and removes it before it closes the tab", async () => {
  const { log } = await readFormsWith([{ application_id: 5, url: LEVER }]);

  assert.deepEqual(steps(log, ["overlay", "run", "close"]), ["overlay show", "run readForm", "overlay hide", "close"]);
});

test("sendForms shows the frame while it submits, and removes it whether or not the click went through", async () => {
  const ok = await sendFormsWith([{ application_id: 33, url: LEVER }]);
  const refused = await sendFormsWith([{ application_id: 34, url: LEVER }], { submit: { ok: false, reason: "captcha" } });

  assert.deepEqual(steps(ok.log, ["overlay", "run"]), ["overlay show", "run submitForm", "run isConfirmed", "overlay hide"]);
  assert.deepEqual(steps(refused.log, ["overlay", "run"]), ["overlay show", "run submitForm", "overlay hide"]);
});
