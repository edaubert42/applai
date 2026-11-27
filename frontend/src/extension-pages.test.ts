// The ApplAI extension turning a fictional form's pages (extension/pages.js), and its loop
// (extension/background.js) filling a 3-page form against a fake ApplAI.
import { expect, test } from "vitest";

import { APPLAI, fillForms, readForms } from "../../extension/background.js";
import { turnPage } from "../../extension/pages.js";

function page(buttons: string): HTMLButtonElement[] {
  document.body.innerHTML = `<form action="/apply" method="post">
    <h2>Vos coordonnées</h2><label>Ville <input name="city"></label>${buttons}</form>`;
  const clicked: HTMLButtonElement[] = [];
  document.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => clicked.push(b)));
  return clicked;
}

test("a page whose only button is « Envoyer ma candidature » is the final page: it is never clicked", () => {
  const clicked = page(`<button type="submit">Envoyer ma candidature</button>`);

  expect(turnPage(true)).toBe("final");
  expect(clicked).toEqual([]);
});

test("« Continuer et envoyer » is a send button, never clicked, even beside an allowed one", () => {
  for (const buttons of [`<button>Continuer et envoyer</button>`, `<button>Continuer</button><button>Continuer et envoyer</button>`]) {
    const clicked = page(buttons);

    expect(turnPage(true)).toBe("final");
    expect(clicked).toEqual([]);
  }
});

test("a review page is the final page, and a page with no allowed button enabled is not turned", () => {
  const review = page(`<button type="button">Suivant</button>`);
  document.querySelector("h2")!.textContent = "Relecture de votre candidature";
  expect(turnPage(true)).toBe("final");

  const blocked = page(`<button type="button" disabled>Continuer</button><button type="button">Retour</button>`);
  expect(turnPage(true)).toBe("none");
  expect([...review, ...blocked]).toEqual([]);
});

test("an allowed button is clicked, and the submit it starts is cancelled", () => {
  const clicked = page(`<button type="button">Précédent</button><button type="submit"> Enregistrer et continuer → </button>`);
  let submitted = 0;
  document.querySelector("form")!.addEventListener("submit", (e) => { if (!e.defaultPrevented) submitted += 1; });

  expect(turnPage(false)).toBe("next");
  expect(clicked).toEqual([]);
  expect(turnPage(true)).toBe("next");
  expect(clicked.map((b) => b.textContent?.trim())).toEqual(["Enregistrer et continuer →"]);
  expect(submitted).toBe(0);
});

// A fictional site's 3-page form at one URL: it keeps what is typed, page by page, as a site's own
// session does, and its « Suivant » turns a page only once every field of that page is filled.
const FORM_URL = "https://jobs.lever.co/pivoine-analytics/4f2a-data-engineer/apply";
const PAGES = [
  { fields: ["Prénom", "Nom", "E-mail"], button: `<button type="button">Suivant</button>` },
  { fields: ["Ville"], button: `<button type="button">Précédent</button><button type="button">Continuer</button>` },
  { fields: ["Confirmez votre e-mail"], button: `<button type="submit">Envoyer ma candidature</button>` },
];
const VALUES: Record<string, string> = {
  "Prénom": "Camille", "Nom": "Moreau", "E-mail": "camille.moreau@example.org",
  "Ville": "Lyon", "Confirmez votre e-mail": "camille.moreau@example.org",
};

function fictionalSite() {
  const site = { page: 1, values: {} as Record<string, string>, submits: 0, sent: 0 };
  const render = () => {
    const { fields, button } = PAGES[site.page - 1]!;
    document.body.innerHTML = `<form action="/apply" method="post">${fields
      .map((label, i) => `<label>${label} <input name="f${i}" required value="${site.values[label] ?? ""}"></label>`)
      .join("")}${button}</form>`;
    const form = document.querySelector("form")!;
    form.addEventListener("submit", (e) => { e.preventDefault(); site.submits += 1; });
    form.addEventListener("input", (e) => {
      const input = e.target as HTMLInputElement;
      site.values[fields[Number(input.name.slice(1))]!] = input.value;
    });
    for (const b of form.querySelectorAll("button")) {
      b.addEventListener("click", () => {
        if (b.textContent === "Envoyer ma candidature") site.sent += 1;
        if (!["Suivant", "Continuer"].includes(b.textContent ?? "")) return;
        if (fields.every((label) => site.values[label])) { site.page += 1; render(); }
      });
    }
  };
  return { site, render };
}

// ApplAI as the extension sees it: page 1 asked first, each page served once posted, the next
// asked once a page is reported turned, nothing after the final page.
function fakeApplai() {
  const posted: { page?: number }[] = [], reported: { page: number; final?: boolean; fields: { value: string }[] }[] = [];
  const last = () => reported.at(-1);
  const fetch = async (url: string, init?: RequestInit) => {
    const id = { application_id: 4, url: FORM_URL };
    const page = posted.length;
    if (url === `${APPLAI}/api/browser/forms`) {
      if (!page) return Response.json([id]);
      return Response.json(last()?.page === page && !last()?.final ? [{ ...id, page: page + 1 }] : []);
    }
    if (url === `${APPLAI}/api/browser/fills`) {
      if (!page || last()?.page === page) return Response.json([]);
      const actions = PAGES[page - 1]!.fields.map((label, field) => ({ field, label, kind: "fill", value: VALUES[label] }));
      return Response.json([{ ...id, page, actions }]);
    }
    const body = JSON.parse(String(init?.body));
    if (url === `${APPLAI}/api/browser/forms/4`) posted.push(body);
    else if (url === `${APPLAI}/api/browser/fills/4`) reported.push(body);
    else throw new Error(`unexpected ${url}`);
    return new Response(null, { status: 204 });
  };
  return { fetch, posted, reported };
}

function fakeChrome(render: () => void) {
  const open = new Set<number>(), stored: Record<string, unknown> = {};
  const chrome = {
    runtime: { getManifest: () => ({ host_permissions: ["https://*.lever.co/*"] }) },
    tabs: {
      create: async () => { open.add(9); render(); return { id: 9, windowId: 1, groupId: -1 }; },
      get: async (id: number) => { if (!open.has(id)) throw new Error("no tab"); return { id, status: "complete", windowId: 1 }; },
      remove: async (id: number) => { open.delete(id); },
      query: async () => [],
      update: async () => ({}),
      group: async () => 1,
      captureVisibleTab: async () => "data:image/png;base64,iVBORw0KGgo=",
    },
    tabGroups: { query: async () => [], update: async () => ({}) },
    scripting: {
      executeScript: async ({ func, args = [] }: { func: (...a: unknown[]) => unknown; args?: unknown[] }) => [{ result: func(...args) }],
    },
    storage: {
      local: {
        get: async (key: string) => ({ [key]: stored[key] }),
        set: async (items: Record<string, unknown>) => { Object.assign(stored, items); },
        remove: async (key: string) => { delete stored[key]; },
      },
    },
  };
  return { chrome, open };
}

test("a 3-page form with no open question is filled page by page and never sent", async () => {
  const { site, render } = fictionalSite();
  const applai = fakeApplai();
  const { chrome, open } = fakeChrome(render);

  for (let minute = 0; minute < 5; minute++) {
    await readForms(applai.fetch, chrome, async () => {});
    await fillForms(applai.fetch, chrome, async () => {});
  }

  expect(applai.posted.map((body) => body.page)).toEqual([1, 2, 3]);
  expect(applai.reported.map(({ page, final }) => [page, final])).toEqual([[1, undefined], [2, undefined], [3, true]]);
  expect(applai.reported.flatMap((body) => body.fields.map((f) => f.value))).toEqual(Object.values(VALUES));
  expect(site.values).toEqual(VALUES);
  expect([site.page, site.submits, site.sent]).toEqual([3, 0, 0]);
  expect([...open]).toEqual([9]); // the final page's tab stays open for the candidate
});
