// The extension's loop, run by `node --test extension/` with fetch mocked.
import assert from "node:assert/strict";
import { test } from "node:test";

import { APPLAI, readPages } from "./background.js";

const OFFER = "https://ecole-des-tilleuls.jobteaser.com/fr/job-offers/4521-data-engineer";

function network(pages) {
  const calls = [];
  const fetch = async (url, options = {}) => {
    calls.push([url, options]);
    if (url === `${APPLAI}/api/browser/pages`) return Response.json(pages);
    if (url.startsWith(APPLAI)) return new Response(null, { status: 204 });
    return new Response(`<html>${url}</html>`);
  };
  return { calls, fetch };
}

test("it asks ApplAI, fetches each page with the browser's session, and posts its HTML", async () => {
  const { calls, fetch } = network([{ offer_id: 7, url: OFFER }]);

  await readPages(fetch);

  assert.deepEqual(calls, [
    [`${APPLAI}/api/browser/pages`, {}],
    [OFFER, { credentials: "include" }],
    [
      `${APPLAI}/api/browser/pages/7`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ html: `<html>${OFFER}</html>` }),
      },
    ],
  ]);
});

test("it fetches no page outside JobTeaser, whatever ApplAI asks", async () => {
  const { calls, fetch } = network([{ offer_id: 8, url: "https://jobs.example.org/8" }]);

  await readPages(fetch);

  assert.deepEqual(calls, [[`${APPLAI}/api/browser/pages`, {}]]);
});
