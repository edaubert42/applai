import { fireEvent, render, screen, within } from "@testing-library/react";
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
  ] as const)("words why the extension could not fill a form, in %s", async (language, words) => {
    const errored = aRow({ id: 9, offer_id: 9, state: "answers_checked", fill_error: "fill_not_loaded" });
    await openScreen(applicationsRoutes({ in_progress: [errored] }), language);

    const alert = await screen.findByText(fill(words["applications.fill.error"], { error: words["message.fill_not_loaded"] }));
    expect(alert.getAttribute("role")).toBe("alert");
  });
});
