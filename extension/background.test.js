// The extension's loop with fetch mocked, run by Node's test runner (tests/test_extension.py).
import assert from "node:assert/strict";
import { test } from "node:test";

import { APPLAI, readPages } from "./background.js";

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
