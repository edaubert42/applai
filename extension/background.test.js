// The extension's loop with fetch mocked, run by Node's test runner (tests/test_extension.py).
import assert from "node:assert/strict";
import { test } from "node:test";

import { APPLAI, fillForms, readForms, readPages, sendForms } from "./background.js";
import { fillForm } from "./fill.js";
import { readForm, readText } from "./forms.js";
import manifest from "./manifest.json" with { type: "json" };
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

test("it fetches Google offer pages with the browser's session too", async () => {
  const google = "https://www.google.fr/search?q=Data+engineer&udm=8&htidocid=1";
  const headers = { "Content-Type": "application/json" };
  assert.deepEqual(await readWith([{ offer_id: 9, url: google }]), [
    [`${APPLAI}/api/browser/pages`, {}],
    [google, { credentials: "include" }],
    [`${APPLAI}/api/browser/pages/9`, { method: "POST", headers, body: JSON.stringify({ html: `<html>${google}</html>` }) }],
  ]);
});

test("it fetches no page outside JobTeaser and Google, whatever ApplAI asks", async () => {
  const calls = await readWith([{ offer_id: 8, url: "https://jobs.example.org/8" }]);

  assert.deepEqual(calls, [[`${APPLAI}/api/browser/pages`, {}]]);
});

const LEVER = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply";
const FIELDS = [{ label: "CV", type: "file", required: true, options: [] }];
const SENT = { method: "POST", headers: { "Content-Type": "application/json" } };

// Chrome's tabs and scripting, mocked: a tab with this status, a form with these fields and text.
function chromeWith(calls, { status = "complete", fields = FIELDS, text = "" } = {}) {
  return {
    runtime: { getManifest: () => manifest },
    tabs: {
      create: async (options) => calls.push(["open", options]) && { id: 3 },
      get: async (id) => ({ id, status }),
      remove: async (id) => calls.push(["close", id]),
    },
    scripting: {
      executeScript: async ({ target, func }) =>
        calls.push(["run", target, func]) && [{ result: func === readText ? text : fields }],
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
  return calls;
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

function fillChromeWith(calls, { status = "complete", filled = FILLED, captureError = false } = {}) {
  return {
    runtime: { getManifest: () => manifest },
    tabs: {
      create: async (options) => calls.push(["open", options]) && { id: 9, windowId: 1 },
      get: async (id) => ({ id, status, windowId: 1 }),
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
      executeScript: async ({ target, func, args }) => calls.push(["run", target, func, args]) && [{ result: filled }],
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
  return calls;
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
  { tabs = [{ id: 7, url: LEVER, status: "complete" }], submit = { ok: true, beforeText: "Form" }, tabAfter, confirmedAfter = true, postStatus = 204, pending = new Map() } = {},
) {
  const calls = [];
  const chrome = {
    tabs: {
      create: async () => { throw new Error("must never open a tab"); },
      query: async (q) => calls.push(["query", q]) && tabs,
      get: async (id) => ({ id, status: "complete", url: tabAfter ?? LEVER }),
    },
    scripting: {
      executeScript: async ({ target, func }) =>
        calls.push(["run", target, func]) && [{ result: func === isConfirmed ? confirmedAfter : submit }],
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
  return calls;
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
