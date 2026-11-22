import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { DECISION, aCard, aScan, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => vi.unstubAllGlobals());

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name]));

const bob = aCard({ id: 2, title: "Développeuse web", company: "Globex Studio", logo: null, score: 74 });

async function openScreen(routes = reviewRoutes({ to_review: [aCard(), bob] })) {
  const calls = fakeApi({ ...routes, ...Object.fromEntries([1, 2, 3, 4].map((id) => [DECISION(id), {}])),
    "POST /api/scan": {},
  });
  render(<App />);
  await screen.findByRole("heading", { name: "Ingénieur logiciel" });
  return calls;
}

function decisions(calls: { method: string; path: string; body: unknown }[]) {
  return calls.filter((call) => call.path.endsWith("/decision")).map((c) => [c.path, c.body]);
}

describe("a card", () => {
  it.each([
    ["fr", fr, "une alerte WTTJ"],
    ["en", en, "a WTTJ alert"],
  ] as const)("shows every field of the result, in %s", async (language, words, source) => {
    await openScreen();
    if (language === "en") fireEvent.click(screen.getByRole("button", { name: fr["language.switch"] }));
    const card = within(screen.getByRole("article", { name: /Ingénieur logiciel/ }));
    const lineOf = (text: string) => card.getByText(text).closest("li")?.textContent;

    expect(card.getByText("Acme Robotics")).toBeTruthy();
    expect(card.getByText(fill(words["card.score"], { n: 87 }))).toBeTruthy();
    for (const tag of ["Paris 11e", "Hybride", "CDI", "3 ans et plus", "Non indiqué"]) {
      expect(card.getByText(tag)).toBeTruthy();
    }
    expect(card.getByText("Acme Robotics fabrique des robots de cuisine.")).toBeTruthy();
    expect(card.getByText(words["card.about"])).toBeTruthy();
    expect(card.getByText(words["card.mission"])).toBeTruthy();
    expect(card.getByText("Écrire le service de commandes")).toBeTruthy();
    expect(lineOf("Le poste est en Python")).toMatch(/^\+/);
    expect(lineOf("Le salaire n’est pas indiqué")).toMatch(/^−/);
    expect(lineOf("L’équipe compte trois personnes")).toMatch(/^\?/);
    expect(card.getByText(fill(words["card.seen"], { source }))).toBeTruthy();
    const original = card.getByRole("link", { name: words["card.original"] });
    expect(original.getAttribute("href")).toBe("https://jobs.example.org/acme/ingenieur-logiciel");
    expect(card.getByRole("img", { hidden: true }).getAttribute("src")).toBe(
      "/api/logos/acme-robotics.png",
    );
    for (const key of ["card.interested", "card.notInterested", "card.later"] as const) {
      expect(card.getByRole("button", { name: words[key] })).toBeTruthy();
    }
  });

  it("shows the company's initials when it has no logo, or its logo fails", async () => {
    await openScreen();
    const second = within(screen.getByRole("article", { name: /Développeuse web/ }));
    expect(second.getByText("GS")).toBeTruthy();

    const first = within(screen.getByRole("article", { name: /Ingénieur logiciel/ }));
    fireEvent.error(first.getByRole("img", { hidden: true }));

    expect(first.getByText("AR")).toBeTruthy();
    expect(first.queryByRole("img", { hidden: true })).toBeNull();
  });

  it("shows the raw source when it has no readable name", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard({ source: "alert_unheard_of" })] }));
    expect(screen.getByText(fill(fr["card.seen"], { source: "alert_unheard_of" }))).toBeTruthy();
  });

  it("says when the offer's full text was not read, only its alert", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard(), aCard({ id: 2, title: "Data engineer", full_text_read: false })] }));
    expect(screen.getAllByText(fr["card.fullTextNotRead"])).toHaveLength(1);
  });

  it("stacks Pour and Contre on a phone: they wrap, and nothing is wider than the screen", async () => {
    await openScreen();
    const pour = screen.getAllByText(fr["card.pros"])[0]?.closest("section");
    const contre = screen.getAllByText(fr["card.cons"])[0]?.closest("section");

    for (const block of [pour, contre]) expect(block?.className).toMatch(/basis-70\b.*min-w-0/);
    expect(pour?.parentElement?.className).toMatch(/\bflex-wrap\b/);
    expect(screen.getByRole("main").className).toMatch(/\bmin-w-0\b|\boverflow-x-clip\b/);
  });
});

describe("a decision", () => {
  it.each([
    ["Intéressé", "card.interested", { decision: "interested" }],
    ["Plus tard", "card.later", { decision: "later" }],
  ] as const)("by the %s button goes to the API, and the card leaves", async (_, key, body) => {
    const calls = await openScreen();
    const first = within(screen.getByRole("article", { name: /Ingénieur logiciel/ }));

    fireEvent.click(first.getByRole("button", { name: fr[key] }));

    await waitFor(() => expect(screen.queryByRole("heading", { name: "Ingénieur logiciel" })).toBeNull());
    expect(decisions(calls)).toEqual([["/api/offers/1/decision", body]]);
    expect(screen.getByRole("heading", { name: "Développeuse web" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: fill(fr["review.tab.to_review"], { n: 1 }) })).toBeTruthy();
  });

  it("by the arrow keys goes to the API for the card in view", async () => {
    const calls = await openScreen();

    fireEvent.keyDown(document, { key: "ArrowRight" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Ingénieur logiciel" })).toBeNull());
    fireEvent.keyDown(document, { key: "ArrowDown" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Développeuse web" })).toBeNull());

    expect(decisions(calls)).toEqual([
      ["/api/offers/1/decision", { decision: "interested" }],
      ["/api/offers/2/decision", { decision: "later" }],
    ]);
  });

  it("goes to the card the candidate scrolled to, not the first", async () => {
    let seen: (ids: string[]) => void = () => {};
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(private callback: IntersectionObserverCallback) {
          seen = (ids) =>
            this.callback(
              ids.map((id) => ({ target: document.getElementById(id), intersectionRatio: 0.9 }) as never),
              this as never,
            );
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const calls = await openScreen();

    seen([screen.getByRole("article", { name: /Développeuse web/ }).id]);
    fireEvent.keyDown(document, { key: "ArrowDown" });

    await waitFor(() => expect(decisions(calls)).toHaveLength(1));
    expect(decisions(calls)[0]?.[0]).toBe("/api/offers/2/decision");
  });

  it("keeps the card and says so when the API refuses", async () => {
    fakeApi({ ...reviewRoutes({ to_review: [aCard()] }), [DECISION(1)]: new Response("no", { status: 409 }) });
    render(<App />);
    await screen.findByRole("heading", { name: "Ingénieur logiciel" });

    fireEvent.keyDown(document, { key: "ArrowRight" });

    expect((await screen.findByRole("alert")).textContent).toBe(fr["decision.error"]);
    expect(screen.getByRole("heading", { name: "Ingénieur logiciel" })).toBeTruthy();
  });
});

describe("not interested", () => {
  const save = () => screen.getByRole("button", { name: fr["decline.save"] }) as HTMLButtonElement;

  it("keeps Save disabled until a reason is chosen, then saves the reasons and the note", async () => {
    const calls = await openScreen();

    fireEvent.keyDown(document, { key: "ArrowLeft" });

    expect(screen.getByText(fr["decline.title"])).toBeTruthy();
    expect(decisions(calls)).toEqual([]);
    expect(save().disabled).toBe(true);
    fireEvent.keyDown(document, { key: "Enter" });
    expect(decisions(calls)).toEqual([]);

    fireEvent.keyDown(document, { key: "3" });
    fireEvent.click(screen.getByRole("button", { name: new RegExp(fr["reason.salary"]) }));
    fireEvent.keyDown(document, { key: "3" }); // a second press takes the reason back
    expect(screen.getByRole("button", { name: /Expérience/ }).getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(document, { key: "7" });
    expect(save().disabled).toBe(false);
    fireEvent.change(screen.getByLabelText(fr["decline.note"]), { target: { value: "Trop loin" } });
    fireEvent.keyDown(screen.getByLabelText(fr["decline.note"]), { key: "Enter" });

    await waitFor(() => expect(decisions(calls)).toHaveLength(1));
    expect(decisions(calls)[0]).toEqual([
      "/api/offers/1/decision",
      { decision: "not_interested", reasons: ["salary", "other"], note: "Trop loin" },
    ]);
    await waitFor(() => expect(screen.queryByText(fr["decline.title"])).toBeNull());
    expect(screen.getByRole("heading", { name: "Développeuse web" })).toBeTruthy();
  });

  it("is cancelled by Escape, and by the Cancel button, without a decision", async () => {
    const calls = await openScreen();

    fireEvent.keyDown(document, { key: "ArrowLeft" });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText(fr["decline.title"])).toBeNull();
    fireEvent.click(within(screen.getByRole("article", { name: /Ingénieur/ })).getByRole("button", { name: fr["card.notInterested"] }));
    fireEvent.click(screen.getByRole("button", { name: fr["decline.cancel"] }));

    expect(screen.queryByText(fr["decline.title"])).toBeNull();
    expect(screen.getByRole("heading", { name: "Ingénieur logiciel" })).toBeTruthy();
    expect(decisions(calls)).toEqual([]);
  });
});

describe("the scan bar", () => {
  it("disables the button with the time left after a clean scan", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard()] }, aScan({ manual_scan_in_s: 1050 })));
    const button = await screen.findByRole("button", { name: fill(fr["scan.wait"], { n: 18 }) });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables the button after a scan with errors, which show", async () => {
    const failed = aScan({
      last_scan: { ...aScan().last_scan!, errors: ["Outlook connection expired: run applai mail login."] },
    });
    const calls = await openScreen(reviewRoutes({ to_review: [aCard()] }, failed));
    const button = (await screen.findByRole("button", { name: fr["scan.button"] })) as HTMLButtonElement;

    expect(button.disabled).toBe(false);
    expect(screen.getByText("Outlook connection expired: run applai mail login.")).toBeTruthy();
    fireEvent.click(button);

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/scan")).toBe(true));
  });

  it("shows the last scan, the new offers and the LinkedIn reads left", async () => {
    await openScreen();
    expect(await screen.findByText(fill(fr["scan.new"], { n: 4 }))).toBeTruthy();
    expect(screen.getByText(fill(fr["scan.linkedin"], { n: 3, max: 20 }))).toBeTruthy();
    expect(screen.getByRole("img", { name: fr["scan.ok"] })).toBeTruthy();
  });
});

describe("the views", () => {
  it("count their offers, and the screened-out one shows its rule or score and a Rescue button", async () => {
    const byRule = aCard({ id: 3, title: "Directeur technique", state: "screened_out", score: null, tags: null, rule: "« Senior » dans le titre" });
    const byScore = aCard({ id: 4, title: "Comptable", company: "Initech", state: "triaged", score: 28 });
    const calls = await openScreen(
      reviewRoutes({ to_review: [aCard()], later: [bob], screened_out: [byRule, byScore] }),
    );

    expect(screen.getByRole("tab", { name: fill(fr["review.tab.later"], { n: 1 }) })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: fill(fr["review.tab.screened_out"], { n: 2 }) }));

    expect(screen.getByText(fill(fr["screened.rule"], { rule: "« Senior » dans le titre" }))).toBeTruthy();
    expect(screen.getByText(fill(fr["card.score"], { n: 28 }))).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Ingénieur logiciel" })).toBeNull();
    const rescues = screen.getAllByRole("button", { name: fr["screened.rescue"] });
    expect(rescues).toHaveLength(2);

    fireEvent.click(rescues[0]!);

    await waitFor(() => expect(decisions(calls)).toHaveLength(1));
    expect(decisions(calls)[0]).toEqual(["/api/offers/3/decision", { decision: "interested" }]);
    await waitFor(() => expect(screen.queryByText("Directeur technique")).toBeNull());
  });
});
