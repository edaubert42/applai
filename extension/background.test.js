// The extension's loop with fetch mocked, run by Node's test runner (tests/test_extension.py).
import assert from "node:assert/strict";
import { test } from "node:test";

import { APPLAI, readForms, readPages } from "./background.js";
import { readForm } from "./forms.js";
import manifest from "./manifest.json" with { type: "json" };

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

// Chrome's tabs and scripting, mocked: a tab with this status, a form with these fields.
function chromeWith(calls, { status = "complete", fields = FIELDS } = {}) {
  return {
    runtime: { getManifest: () => manifest },
    tabs: {
      create: async (options) => calls.push(["open", options]) && { id: 3 },
      get: async (id) => ({ id, status }),
      remove: async (id) => calls.push(["close", id]),
    },
    scripting: {
      executeScript: async ({ target, func }) => calls.push(["run", target, func]) && [{ result: fields }],
    },
  };
}

async function readFormsWith(forms, options) {
  const calls = [];
  await readForms(
    async (url, init) => {
      calls.push([url, init]);
      return url === `${APPLAI}/api/browser/forms` ? Response.json(forms) : new Response(null, { status: 204 });
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
