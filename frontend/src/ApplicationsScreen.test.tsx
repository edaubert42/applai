import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { aRow, applicationsRoutes, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => vi.unstubAllGlobals());

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name]));

const groups = {
  in_progress: [aRow({ id: 3, offer_id: 3, title: "Assistant douane", state: "filled" })],
  waiting: [
    aRow(),
    aRow({ id: 2, offer_id: 2, title: "Analyste logistique", state: "form_checked" }),
  ],
  sent: [aRow({ id: 4, offer_id: 4, title: "Agent de transit", state: "sent" })],
  dropped: [],
};

async function openScreen(routes = applicationsRoutes(groups), language: "fr" | "en" = "fr") {
  fakeApi({ ...reviewRoutes(), ...routes });
  if (language === "en") localStorage.setItem("applai.language", "en"); // as chosen on Réglages
  render(<App />);
  const nav = language === "en" ? en["nav.applications"] : fr["nav.applications"];
  fireEvent.click(screen.getByRole("button", { name: nav }));
  await screen.findAllByRole("region");
}

const groupNamed = (name: string) => within(screen.getByRole("region", { name }));

describe("the Applications dashboard", () => {
  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("lists the four groups with their rows, in %s", async (language, words) => {
    await openScreen(undefined, language);

    for (const [group, count] of [["in_progress", 1], ["waiting", 2], ["sent", 1], ["dropped", 0]] as const) {
      expect(screen.getByRole("region", { name: fill(words[`applications.group.${group}`], { n: count }) })).toBeTruthy();
    }
    const waiting = groupNamed(fill(words["applications.group.waiting"], { n: 2 }));
    const rows = waiting.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    const first = within(rows[0] as HTMLElement);
    expect(first.getByRole("heading", { name: "Chef de quai" })).toBeTruthy();
    expect(first.getByText("Brindille Logistique")).toBeTruthy();
    expect(first.getByText(words["applications.state.drafted"])).toBeTruthy();
    expect(first.getByText(new Date("2026-11-20T09:30:00Z").toLocaleString(language, {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    }))).toBeTruthy();
    expect(first.getByRole("link", { name: words["applications.offer"] }).getAttribute("href")).toBe(
      "https://jobs.example.org/brindille-logistique/chef-de-quai",
    );
    expect(within(rows[1] as HTMLElement).getByText(words["applications.state.form_checked"])).toBeTruthy();
    expect(groupNamed(fill(words["applications.group.dropped"], { n: 0 })).getByText(words["applications.none"])).toBeTruthy();
  });

  it("keeps the API's order inside a group", async () => {
    await openScreen();

    const titles = groupNamed(fill(fr["applications.group.waiting"], { n: 2 }))
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);

    expect(titles).toEqual(["Chef de quai", "Analyste logistique"]);
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("marks an old-workflow application and shows its Notion status as written, in %s", async (language, words) => {
    const old = aRow({ id: null, offer_id: 9, title: "Gestionnaire de flotte", state: "Candidature envoyée", origin: "old_workflow" });
    await openScreen(applicationsRoutes({ in_progress: [old, ...groups.in_progress] }), language);

    const rows = groupNamed(fill(words["applications.group.in_progress"], { n: 2 })).getAllByRole("listitem");
    const [oldRow, ownRow] = rows.map((row) => within(row as HTMLElement));

    expect(oldRow?.getByText(words["applications.origin.old_workflow"])).toBeTruthy();
    expect(oldRow?.getByText("Candidature envoyée")).toBeTruthy();
    expect(ownRow?.queryByText(words["applications.origin.old_workflow"])).toBeNull();
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("shows why the form was not found and sends a pasted URL, in %s", async (language, words) => {
    const error = words["message.page_http"].replace("{detail}", "403");
    const created = aRow({ id: 7, offer_id: 7, state: "created", form_url_error: "page_http:403" });
    const lever = "https://jobs.lever.co/brindille-logistique/chef-de-quai";
    const calls = fakeApi({
      ...reviewRoutes(),
      ...applicationsRoutes({ in_progress: [created] }),
      "PUT /api/applications/7/form-url": { form_url: lever, form_url_error: null },
    });
    if (language === "en") localStorage.setItem("applai.language", "en"); // as chosen on Réglages
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: words["nav.applications"] }));
    const row = within(await screen.findByRole("listitem"));

    expect(row.getByText(error)).toBeTruthy();
    fireEvent.change(row.getByLabelText(words["applications.form_url.label"]), { target: { value: lever } });
    fireEvent.click(row.getByRole("button", { name: words["applications.form_url.save"] }));

    const link = await row.findByRole("link", { name: words["applications.form"] });
    expect(link.getAttribute("href")).toBe(lever);
    expect(row.queryByText(error)).toBeNull();
    expect(calls.filter((call) => call.method === "PUT")).toEqual([
      { method: "PUT", path: "/api/applications/7/form-url", body: { url: lever } },
    ]);
  });

  describe("a pasted form URL that fails", () => {
    const lever = "https://jobs.lever.co/brindille-logistique/chef-de-quai";

    async function paste(answer: unknown, language: "fr" | "en" = "fr", afterwards = aRow({ id: 7, offer_id: 7, state: "created" })) {
      // `answer` is the PUT's reply: a Response, or a function that rejects.
      const created = aRow({ id: 7, offer_id: 7, state: "created" });
      let loaded = 0; // the board's first load shows the row created, the next ones as it is now
      const calls = fakeApi({
        ...reviewRoutes(),
        ...applicationsRoutes(),
        "GET /api/applications": () =>
          applicationsRoutes({ in_progress: [loaded++ === 0 ? created : afterwards] })["GET /api/applications"],
        "PUT /api/applications/7/form-url": answer,
      });
      if (language === "en") localStorage.setItem("applai.language", "en");
      render(<App />);
      const words = language === "en" ? en : fr;
      fireEvent.click(screen.getByRole("button", { name: words["nav.applications"] }));
      const row = within(await screen.findByRole("listitem"));
      fireEvent.change(row.getByLabelText(words["applications.form_url.label"]), { target: { value: lever } });
      fireEvent.click(row.getByRole("button", { name: words["applications.form_url.save"] }));
      return { calls, words, row };
    }

    it.each([
      ["fr", fr],
      ["en", en],
    ] as const)("says the application is past form capture on a 409 and loads the row again, in %s", async (language, words) => {
      const { calls, row } = await paste(new Response("{}", { status: 409 }), language, aRow({ id: 7, offer_id: 7, state: "drafted" }));

      expect((await row.findByRole("alert")).textContent).toBe(words["applications.form_url.past"]);
      expect(await row.findByText(words["applications.state.drafted"])).toBeTruthy();
      expect(calls.filter((call) => call.method === "GET" && call.path === "/api/applications")).toHaveLength(2);
    });

    it.each([
      ["fr", fr],
      ["en", en],
    ] as const)("keeps the scheme message on a 422, in %s", async (language, words) => {
      const { row } = await paste(new Response("{}", { status: 422 }), language);

      expect((await row.findByRole("alert")).textContent).toBe(words["applications.form_url.failed"]);
    });

    it.each([
      ["fr", fr],
      ["en", en],
    ] as const)("says ApplAI is not reachable when the request is rejected, in %s", async (language, words) => {
      const { row } = await paste(() => Promise.reject(new TypeError("Failed to fetch")), language);

      expect((await row.findByRole("alert")).textContent).toBe(words["applications.form_url.unreachable"]);
    });

    it("says the server failed on another status", async () => {
      const { row } = await paste(new Response("boom", { status: 500 }));

      expect((await row.findByRole("alert")).textContent).toBe(fr["applications.form_url.error"]);
    });
  });

  it("stays one column on a phone: stacked groups, wrapping rows, no grid", async () => {
    await openScreen();

    const regions = screen.getAllByRole("region");
    const container = regions[0]?.parentElement as HTMLElement;
    expect(regions.every((region) => region.parentElement === container)).toBe(true);
    expect(container.className).not.toMatch(/\b(grid|columns|flex-row)\b|(?:grid-cols|columns)-/);
    for (const row of screen.getAllByRole("listitem")) expect(row.className).toContain("flex-wrap");
  });

  it("tells the candidate when the applications could not be loaded", async () => {
    fakeApi(reviewRoutes());
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));

    expect((await screen.findByRole("alert")).textContent).toBe(fr["applications.error"]);
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("warns an answers_checked row to check the Chrome extension when disconnected or failed, in %s", async (language, words) => {
    const waiting = aRow({ id: 8, offer_id: 8, state: "answers_checked" });
    const errored = aRow({ id: 9, offer_id: 9, title: "Chef de produit", state: "answers_checked", fill_error: "form not loaded" });
    await openScreen(
      {
        ...applicationsRoutes({ in_progress: [waiting, errored] }),
        "GET /api/browser/status": { connected: false, last_asked_at: null },
      },
      language,
    );

    const rows = groupNamed(fill(words["applications.group.in_progress"], { n: 2 })).getAllByRole("listitem");
    expect(within(rows[0] as HTMLElement).getByRole("alert").textContent).toContain(
      words["applications.fill.extension_disconnected"],
    );
    expect(within(rows[1] as HTMLElement).getByRole("alert").textContent).toContain("form not loaded");
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("shows whether a filled form's tab is open, closed, or says nothing when unknown, in %s", async (language, words) => {
    const open = aRow({ id: 1, offer_id: 1, title: "Chef de quai", state: "filled", tab_open: true });
    const closed = aRow({ id: 2, offer_id: 2, title: "Magasinier", state: "reviewed", tab_open: false });
    const unknown = aRow({ id: 3, offer_id: 3, title: "Cariste", state: "validated", tab_open: null });
    await openScreen(applicationsRoutes({ in_progress: [open], waiting: [closed, unknown] }), language);

    const [inProgress, waiting] = [
      groupNamed(fill(words["applications.group.in_progress"], { n: 1 })),
      groupNamed(fill(words["applications.group.waiting"], { n: 2 })),
    ];
    expect(inProgress.getByText(words["applications.tab.open"])).toBeTruthy();
    expect(waiting.getByText(words["applications.tab.closed"])).toBeTruthy();
    expect(waiting.queryByText(words["applications.tab.open"])).toBeNull();
    const rows = waiting.getAllByRole("listitem");
    expect(within(rows[1] as HTMLElement).queryByText(/^(Tab|Onglet) /)).toBeNull();
  });

  it("says the tabs in French as the issue words them", () => {
    expect([fr["applications.tab.open"], fr["applications.tab.closed"]]).toEqual(["Onglet ouvert", "Onglet fermé"]);
    expect([en["applications.tab.open"], en["applications.tab.closed"]]).toEqual(["Tab open", "Tab closed"]);
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("words why the extension could not fill a form, in %s", async (language, words) => {
    const errored = aRow({ id: 9, offer_id: 9, state: "answers_checked", fill_error: "fill_not_loaded" });
    await openScreen(applicationsRoutes({ in_progress: [errored] }), language);

    const alert = await screen.findByText(fill(words["applications.fill.error"], { error: words["message.fill_not_loaded"] }));
    expect(alert.getAttribute("role")).toBe("alert");
  });
});

describe("who acts next on an application", () => {
  const rowOf = (title: string) => within(screen.getByRole("heading", { name: title }).closest("li") as HTMLElement);

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("shows a spinning mark and the stage's name on a row an agent runs on, in %s", async (language, words) => {
    const agents = ["form_capture", "drafting", "drafting_judge", "cv_tailoring", "filling", "review"] as const;
    const rows = agents.map((agent, i) => aRow({ id: i + 1, offer_id: i + 1, title: `Poste ${agent}`, state: "created", running: agent }));
    await openScreen(applicationsRoutes({ in_progress: rows }), language);

    for (const agent of agents) {
      const status = rowOf(`Poste ${agent}`).getByRole("status");
      expect(status.textContent).toBe(words[`applications.running.${agent}`]);
      const mark = status.querySelector("svg") as SVGElement;
      // Tailwind's motion-safe: variant spins it only unless the candidate asked for reduced motion.
      expect([...mark.classList]).toContain("motion-safe:animate-spin");
      expect([...mark.classList]).not.toContain("animate-spin");
    }
    expect(words["applications.running.drafting_judge"]).toBe(words["applications.running.drafting"]);
  });

  it.each(["fr", "en"] as const)("shows no spinner on a row no agent runs on, in %s", async (language) => {
    await openScreen(applicationsRoutes({ in_progress: [aRow({ state: "created" })] }), language);

    expect(screen.queryByRole("status")).toBeNull();
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("names the gate on a row whose next move is the candidate's, in %s", async (language, words) => {
    const gates = ["drafted", "reviewed", "form_checked", "validated"] as const;
    const rows = gates.map((state, i) => aRow({ id: i + 1, offer_id: i + 1, title: `Poste ${state}`, state }));
    await openScreen(applicationsRoutes({ waiting: rows }), language);

    for (const state of gates) {
      expect(rowOf(`Poste ${state}`).getByText(words[`applications.next.${state}`])).toBeTruthy();
    }
  });

  it("words the four gates in French as the issue does", () => {
    expect([fr["applications.next.drafted"], fr["applications.next.reviewed"]]).toEqual([
      "À vous : vérifier les textes",
      "À vous : vérifier le formulaire",
    ]);
    expect([fr["applications.next.form_checked"], fr["applications.next.validated"]]).toEqual([
      "À vous : validation finale",
      "À vous : envoyer",
    ]);
  });

  it("shows no gate pill on a row the candidate has nothing to do on", async () => {
    const filled = aRow({ state: "filled" });
    const sent = aRow({ id: 2, offer_id: 2, title: "Envoyée", state: "sent" });
    await openScreen(applicationsRoutes({ in_progress: [filled], sent: [sent] }));

    expect(screen.queryByText(/^À vous/)).toBeNull();
  });

  it("shows the running agent, not the gate, while an agent works on a waiting row", async () => {
    const row = aRow({ state: "drafted", running: "drafting_judge" });
    await openScreen(applicationsRoutes({ waiting: [row] }));

    expect(rowOf("Chef de quai").getByRole("status").textContent).toBe(fr["applications.running.drafting_judge"]);
    expect(screen.queryByText(fr["applications.next.drafted"])).toBeNull();
  });

  it("keeps the gate on a row whose fill_error the row does not show, past answers_checked", async () => {
    const reviewed = aRow({ state: "reviewed", fill_error: "fill_again_not_loaded" });
    await openScreen(applicationsRoutes({ waiting: [reviewed] }));

    expect(rowOf("Chef de quai").getByText(fr["applications.next.reviewed"])).toBeTruthy();
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("shows a failure and no spinner on a row with a draft_error or a fill_error, in %s", async (language, words) => {
    const drafting = aRow({ id: 1, offer_id: 1, title: "Poste rédaction", state: "form_captured", running: "drafting", draft_error: "drafting_failed" });
    const filling = aRow({ id: 2, offer_id: 2, title: "Poste remplissage", state: "answers_checked", running: "filling", fill_error: "fill_not_loaded" });
    await openScreen(applicationsRoutes({ in_progress: [drafting, filling] }), language);

    const failedDrafting = rowOf("Poste rédaction");
    expect(failedDrafting.getByRole("alert").textContent).toBe(
      fill(words["applications.draft.error"], { error: "drafting_failed" }),
    );
    expect(failedDrafting.queryByRole("status")).toBeNull();
    const failedFilling = rowOf("Poste remplissage");
    expect(failedFilling.getByRole("alert").textContent).toBe(
      fill(words["applications.fill.error"], { error: words["message.fill_not_loaded"] }),
    );
    expect(failedFilling.queryByRole("status")).toBeNull();
  });
});

describe("dropping an application", () => {
  const states = ["created", "form_captured", "drafted", "answers_checked", "filled", "reviewed", "form_checked", "validated"];
  const open = states.map((state, i) => aRow({ id: i + 1, offer_id: i + 1, title: `Poste ${state}`, state }));
  const sent = aRow({ id: 20, offer_id: 20, title: "Envoyée", state: "sent" });
  const dropped = aRow({ id: 21, offer_id: 21, title: "Abandonnée", state: "dropped", reasons: ["salary", "offer_closed"] });
  const old = aRow({ id: null, offer_id: 22, title: "Ancienne", state: "Candidature envoyée", origin: "old_workflow" });
  const rowOf = (title: string) => within(screen.getByRole("heading", { name: title }).closest("li") as HTMLElement);
  const boardOf = (groups: Parameters<typeof applicationsRoutes>[0]) => applicationsRoutes(groups)["GET /api/applications"];

  async function openBoard(routes: Record<string, unknown>, language: "fr" | "en" = "fr") {
    const calls = fakeApi({ ...reviewRoutes(), ...applicationsRoutes(), ...routes });
    if (language === "en") localStorage.setItem("applai.language", "en");
    render(<App />);
    const words = language === "en" ? en : fr;
    fireEvent.click(screen.getByRole("button", { name: words["nav.applications"] }));
    await screen.findAllByRole("region");
    return calls;
  }

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("offers Drop on every application not sent or dropped, whatever its state, in %s", async (language, words) => {
    await openBoard(applicationsRoutes({ in_progress: [...open, old], sent: [sent], dropped: [dropped] }), language);

    for (const state of states) {
      expect(rowOf(`Poste ${state}`).getByRole("button", { name: words["validation.drop"] })).toBeTruthy();
    }
    for (const title of ["Envoyée", "Abandonnée", "Ancienne"]) {
      expect(rowOf(title).queryByRole("button", { name: words["validation.drop"] })).toBeNull();
    }
    expect(rowOf("Abandonnée").getByText(words["reason.salary"])).toBeTruthy();
    expect(rowOf("Abandonnée").getByText(words["reason.offer_closed"])).toBeTruthy();
  });

  it.each([
    ["fr", fr],
    ["en", en],
  ] as const)("asks why in the decline panel, with Offer closed, then lists the row under Dropped, in %s", async (language, words) => {
    const capturing = aRow({ id: 5, offer_id: 5, title: "Agent de quai", state: "form_captured" });
    let board = boardOf({ in_progress: [capturing] });
    const calls = await openBoard(
      {
        "GET /api/applications": () => board,
        "POST /api/applications/5/drop": () => {
          board = boardOf({ dropped: [{ ...capturing, state: "dropped", reasons: ["offer_closed"] }] });
          return new Response(null, { status: 204 });
        },
      },
      language,
    );
    fireEvent.click(screen.getByRole("button", { name: words["validation.drop"] }));

    expect(screen.getByText(words["drop.title"])).toBeTruthy();
    expect(screen.getByText(words["drop.keys"])).toBeTruthy();
    const pills = ["company", "mission", "experience", "skills", "salary", "location", "other", "offer_closed"] as const;
    for (const [i, reason] of pills.entries()) {
      expect(screen.getByRole("button", { name: new RegExp(`^${i + 1}\\s*${words[`reason.${reason}`]}$`) })).toBeTruthy();
    }
    const save = screen.getByRole("button", { name: words["decline.save"] }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.keyDown(document, { key: "8" });
    fireEvent.change(screen.getByLabelText(words["decline.note"]), { target: { value: "Retirée du site" } });
    fireEvent.click(save);

    const droppedGroup = within(await screen.findByRole("region", { name: fill(words["applications.group.dropped"], { n: 1 }) }));
    expect(droppedGroup.getByText(words["reason.offer_closed"])).toBeTruthy();
    expect(screen.queryByText(words["drop.title"])).toBeNull();
    expect(calls.filter((call) => call.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/5/drop", body: { reasons: ["offer_closed"], note: "Retirée du site" } },
    ]);
  });

  it("is cancelled without a call; a failed drop keeps the panel and says so, and a retry drops once", async () => {
    let fails = true;
    let board = boardOf({ waiting: [aRow()] });
    const calls = await openBoard({
      "GET /api/applications": () => board,
      "POST /api/applications/1/drop": () => {
        if (fails) return new Response("boom", { status: 500 });
        board = boardOf({ dropped: [aRow({ state: "dropped", reasons: ["company"] })] });
        return new Response(null, { status: 204 });
      },
    });
    fireEvent.click(screen.getByRole("button", { name: fr["validation.drop"] }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText(fr["drop.title"])).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: fr["validation.drop"] }));
    fireEvent.keyDown(document, { key: "1" });
    fireEvent.keyDown(document, { key: "Enter" });
    expect((await screen.findByRole("alert")).textContent).toBe(fr["applications.drop_failed"]);
    expect(screen.getByText(fr["drop.title"])).toBeTruthy();
    expect(screen.getByText(fr["applications.state.drafted"])).toBeTruthy();

    fails = false;
    fireEvent.keyDown(document, { key: "Enter" });
    await waitFor(() => expect(screen.queryByText(fr["drop.title"])).toBeNull());
    expect(await screen.findByText(fr["applications.state.dropped"])).toBeTruthy();
    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([
      { reasons: ["company"] },
      { reasons: ["company"] },
    ]);
  });

  it("loads the board again when the application moved on meanwhile", async () => {
    let board = boardOf({ waiting: [aRow({ state: "validated" })] });
    await openBoard({
      "GET /api/applications": () => board,
      "POST /api/applications/1/drop": () => {
        board = boardOf({ sent: [aRow({ state: "sent" })] });
        return new Response("{}", { status: 409 });
      },
    });
    fireEvent.click(screen.getByRole("button", { name: fr["validation.drop"] }));
    fireEvent.keyDown(document, { key: "2" });
    fireEvent.keyDown(document, { key: "Enter" });

    expect(await screen.findByText(fr["applications.state.sent"])).toBeTruthy();
    expect(screen.queryByText(fr["drop.title"])).toBeNull();
    expect(screen.queryByRole("button", { name: fr["validation.drop"] })).toBeNull();
  });

  it("leaves digits and Enter typed in a URL field to that field while the panel is open", async () => {
    const calls = await openBoard(applicationsRoutes({ in_progress: [aRow({ id: 7, offer_id: 7, state: "created" })] }));
    fireEvent.click(screen.getByRole("button", { name: fr["validation.drop"] }));
    fireEvent.keyDown(document, { key: "8" }); // a reason chosen: Enter would save
    const url = screen.getByLabelText(fr["applications.form_url.label"]);
    fireEvent.keyDown(url, { key: "1" });
    fireEvent.keyDown(url, { key: "Enter" });

    expect(screen.getByRole("button", { name: new RegExp(fr["reason.company"]) }).getAttribute("aria-pressed")).toBe("false");
    expect(calls.filter((call) => call.method === "POST")).toEqual([]);
  });
});
