import { existsSync, readFileSync } from "node:fs";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { DECISION, REVIEW, aCard, aScan, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

const indexCss = readFileSync(existsSync("src/index.css") ? "src/index.css" : "frontend/src/index.css", "utf8");

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const fill = (template: string, values: Record<string, string | number>) =>
  template.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name]));

const bob = aCard({ id: 2, title: "Développeuse web", company: "Globex Studio", logo: null, score: 74 });

/** The screen with its offers as rows; the first one opened unless told not to. */
async function openScreen(
  routes: Record<string, unknown> = reviewRoutes({ to_review: [aCard(), bob] }),
  opened = true,
) {
  const calls = fakeApi({ ...routes, ...Object.fromEntries([1, 2, 3, 4].map((id) => [DECISION(id), {}])),
    "POST /api/scan": {},
  });
  render(<App />);
  const first = await screen.findByRole("button", { name: /Ingénieur logiciel|Offre 1(?!\d)/ });
  if (opened) fireEvent.click(first);
  return calls;
}

const manyOffers = (n: number) =>
  Array.from({ length: n }, (_, index) =>
    aCard({ id: index + 1, title: `Offre ${index + 1}`, company: `Société ${index + 1}`, logo: null }),
  );
const rows = () => screen.queryAllByRole("button", { expanded: false });
const rowOf = (title: RegExp | string) => screen.getByRole("button", { name: title });

function decisions(calls: { method: string; path: string; body: unknown }[]) {
  return calls.filter((call) => call.path.endsWith("/decision")).map((c) => [c.path, c.body]);
}

describe("a card", () => {
  it.each([
    ["fr", fr, "une alerte WTTJ"],
    ["en", en, "a WTTJ alert"],
  ] as const)("shows every field of the result, in %s", async (language, words, source) => {
    if (language === "en") localStorage.setItem("applai.language", "en"); // as chosen on Réglages
    await openScreen();
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
    expect(within(rowOf(/Développeuse web/)).getByText("GS")).toBeTruthy();

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
    expect(screen.queryByText(fr["card.fullTextNotRead"])).toBeNull();
    fireEvent.click(rowOf(/Data engineer/));
    expect(screen.getAllByText(fr["card.fullTextNotRead"])).toHaveLength(1);
  });

  it("offers to read again only an offer that can be, then shows it is pending and why it failed", async () => {
    let cards = [aCard({ can_reread: true, full_text_read: false }), bob];
    const routes = { ...reviewRoutes(), [REVIEW("to_review")]: () => cards, "POST /api/offers/1/reread": {} };
    const calls = await openScreen(routes, false);
    fireEvent.click(rowOf(/Développeuse web/));
    expect(screen.queryByRole("button", { name: fr["card.reread"] })).toBeNull();
    fireEvent.click(rowOf(/Ingénieur logiciel/));

    cards = [aCard({ rereading: true, full_text_read: false }), bob];
    fireEvent.click(screen.getByRole("button", { name: fr["card.reread"] }));

    expect((await screen.findByRole("status")).textContent).toBe(fr["card.rereading"]);
    expect(calls.some((call) => call.method === "POST" && call.path === "/api/offers/1/reread")).toBe(true);
    expect(screen.queryByRole("button", { name: fr["card.reread"] })).toBeNull();
  });

  it("says why the last read failed, and that the extension is not connected when refused", async () => {
    const failed = aCard({ can_reread: true, reread_error: "the page answered HTTP 404" });
    await openScreen(reviewRoutes({ to_review: [failed] }));
    expect(screen.getByRole("alert").textContent).toBe(
      fill(fr["card.rereadError"], { reason: "the page answered HTTP 404" }),
    );

    fireEvent.click(screen.getByRole("button", { name: fr["card.reread"] })); // no route: refused

    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getByText(fr["card.rereadRefused"])).toBeTruthy();
  });

  it("stacks Pour and Contre on a phone: they wrap, and nothing is wider than the screen", async () => {
    await openScreen();
    const pour = screen.getAllByText(fr["card.pros"])[0]?.closest("section");
    const contre = screen.getAllByText(fr["card.cons"])[0]?.closest("section");

    for (const block of [pour, contre]) expect(block?.className).toMatch(/basis-70\b.*min-w-0/);
    expect(pour?.parentElement?.className).toMatch(/\bflex-wrap\b/);
    expect(screen.getByRole("main").className).toMatch(/\bmin-w-0\b|\boverflow-x-clip\b/);
  });

  it("gives the card's decision buttons cursor: pointer and a hover class", async () => {
    await openScreen();
    const card = within(screen.getByRole("article", { name: /Ingénieur logiciel/ }));

    for (const key of ["card.notInterested", "card.later", "card.interested"] as const) {
      const button = card.getByRole("button", { name: fr[key] });
      expect(button.className).toMatch(/\bcursor-pointer\b/);
      expect(button.className).toMatch(/\bhover:/);
    }
    expect(indexCss).toMatch(/button:not\(:disabled\)[\s\S]*cursor:\s*pointer/);
    expect(indexCss).toMatch(/button:disabled[\s\S]*cursor:\s*not-allowed/);
  });
});

describe("the list of offers", () => {
  it("shows every offer as a row with its logo, title, company, score and contract, and no card open", async () => {
    const more = manyOffers(18).map((card) => ({ ...card, id: card.id + 10, title: `Autre ${card.id}` }));
    await openScreen(reviewRoutes({ to_review: [aCard(), bob, ...more] }), false);

    expect(rows()).toHaveLength(20);
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Ingénieur logiciel" })).toBeNull();
    const row = within(rowOf(/Ingénieur logiciel/));
    for (const text of ["Ingénieur logiciel", "Acme Robotics", fill(fr["card.score"], { n: 87 }), "CDI"]) {
      expect(row.getByText(text)).toBeTruthy();
    }
    for (const text of ["Paris 11e", "Hybride"]) expect(row.queryByText(text)).toBeNull();
    expect(row.getByRole("img", { hidden: true }).getAttribute("src")).toBe("/api/logos/acme-robotics.png");
    expect(within(rowOf(/Développeuse web/)).getByText("GS")).toBeTruthy();
  });

  it.each([
    ["permanent", "fr", "CDI"],
    ["fixed_term", "fr", "CDD"],
    ["internship", "fr", "Stage"],
    ["apprenticeship", "fr", "Alternance"],
    ["permanent", "en", "Permanent"],
    ["fixed_term", "en", "Fixed-term"],
    ["internship", "en", "Internship"],
    ["apprenticeship", "en", "Apprenticeship"],
  ] as const)("words the %s contract of a row in %s as %s", async (contractType, language, word) => {
    if (language === "en") localStorage.setItem("applai.language", "en");
    await openScreen(reviewRoutes({ to_review: [aCard({ contract_type: contractType }), bob] }), false);

    expect(within(rowOf(/Ingénieur logiciel/)).getByText(word)).toBeTruthy();
  });

  it("shows no contract on the row of an offer with no contract type", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard({ contract_type: null }), bob] }), false);

    const row = within(rowOf(/Ingénieur logiciel/));
    for (const word of ["CDI", "CDD", "Stage", "Alternance"]) expect(row.queryByText(word)).toBeNull();
    expect(row.getByText("Acme Robotics")).toBeTruthy();
  });

  it("shows the site logo next to the offer's age on the title line and the contract type next to the company", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-11-25T11:00:00Z").getTime());
    await openScreen(
      reviewRoutes({
        to_review: [
          aCard({ source: "alert_wttj", seen_at: "2026-11-22T09:00:00Z" }),
          aCard({
            id: 2,
            title: "Développeuse web",
            company: "Globex Studio",
            source: null,
            contract_type: null,
            seen_at: "2026-11-25T09:00:00Z",
          }),
        ],
      }),
      false,
    );

    const first = rowOf(/Ingénieur logiciel/);
    const titleLine = within(first).getByText("Ingénieur logiciel").parentElement!;
    const companyLine = within(first).getByText("Acme Robotics").parentElement!;
    expect(titleLine.querySelector('[data-logo="alert_wttj"]')).toBeTruthy();
    expect(within(titleLine).getByText("il y a 3 j")).toBeTruthy();
    expect(titleLine.textContent).not.toContain("·");
    expect(within(companyLine).getByText("CDI")).toBeTruthy();

    const second = rowOf(/Développeuse web/);
    const secondTitleLine = within(second).getByText("Développeuse web").parentElement!;
    expect(secondTitleLine.querySelector("[data-logo]")).toBeNull();
    expect(within(secondTitleLine).getByText("il y a 2 h")).toBeTruthy();
    expect(secondTitleLine.textContent).not.toContain("·");
  });

  it.each([
    ["2026-11-25T10:59:40Z", "fr", "il y a moins d’1 min"],
    ["2026-11-25T10:45:00Z", "fr", "il y a 15 min"],
    ["2026-11-25T08:00:00Z", "fr", "il y a 3 h"],
    ["2026-11-22T09:00:00Z", "fr", "il y a 3 j"],
    ["2026-10-10T09:00:00Z", "fr", "il y a 1 mois"],
    ["2025-11-20T09:00:00Z", "fr", "il y a 1 an"],
    ["2024-11-20T09:00:00Z", "fr", "il y a 2 ans"],
    ["2026-11-22T09:00:00Z", "en", "3 d ago"],
  ] as const)("formats %s in %s as %s", async (seenAt, language, expected) => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-11-25T11:00:00Z").getTime());
    if (language === "en") localStorage.setItem("applai.language", "en");
    await openScreen(reviewRoutes({ to_review: [aCard({ seen_at: seenAt }), bob] }), false);

    expect(within(rowOf(/Ingénieur logiciel/)).getByText(expected)).toBeTruthy();
  });

  it("opens the full card from a row, with the place and the work mode in its header", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard(), bob] }), false);

    fireEvent.click(rowOf(/Ingénieur logiciel/));

    const card = within(screen.getByRole("article", { name: /Ingénieur logiciel/ }));
    expect(card.getByText("Paris 11e")).toBeTruthy();
    expect(card.getByText("Hybride")).toBeTruthy();
  });

  it("opens the card of a row by a click, and by Enter, in place of the row", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard(), bob] }), false);

    fireEvent.click(rowOf(/Développeuse web/));
    expect(within(screen.getByRole("article")).getByRole("button", { name: fr["card.interested"] })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Développeuse web" })).toBeTruthy();
    expect(rows()).toHaveLength(1);

    fireEvent.keyDown(rowOf(/Ingénieur logiciel/), { key: "Enter" });
    expect(screen.getByRole("heading", { name: "Ingénieur logiciel" })).toBeTruthy();
  });

  it("keeps one card open: opening another closes the first, Escape closes it", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard(), bob] }), false);

    fireEvent.click(rowOf(/Ingénieur logiciel/));
    fireEvent.click(rowOf(/Développeuse web/));

    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Développeuse web" })).toBeTruthy();
    expect(rows()).toHaveLength(1);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("article")).toBeNull();
    expect(rows()).toHaveLength(2);
    expect(document.activeElement).toBe(rowOf(/Développeuse web/));
  });

  it("collapses an open card when clicking its framed header or collapse button, without intercepting the original link", async () => {
    await openScreen(reviewRoutes({ to_review: [aCard(), bob] }), false);

    fireEvent.click(rowOf(/Ingénieur logiciel/));
    const article = screen.getByRole("article", { name: /Ingénieur logiciel/ });
    const link = within(article).getByRole("link", { name: fr["card.original"] });
    fireEvent.click(link);
    expect(screen.getByRole("article")).toBeTruthy();

    fireEvent.click(within(article).getByText("Acme Robotics"));
    expect(screen.queryByRole("article")).toBeNull();
    expect(rows()).toHaveLength(2);
    expect(document.activeElement).toBe(rowOf(/Ingénieur logiciel/));

    fireEvent.click(rowOf(/Développeuse web/));
    fireEvent.click(screen.getByRole("button", { name: fr["card.collapse"] }));
    expect(screen.queryByRole("article")).toBeNull();
    expect(document.activeElement).toBe(rowOf(/Développeuse web/));
  });

  it("does not show a collapse button or collapse on header click in the Later view", async () => {
    const parked = [aCard({ id: 3, title: "Plombier", state: "later" })];
    await openScreen(reviewRoutes({ to_review: [aCard()], later: parked }), false);
    fireEvent.click(screen.getByRole("tab", { name: fill(fr["review.tab.later"], { n: 1 }) }));

    expect(screen.queryByRole("button", { name: fr["card.collapse"] })).toBeNull();
    fireEvent.click(screen.getByRole("heading", { name: "Plombier" }));
    expect(screen.getByRole("article", { name: /Plombier/ })).toBeTruthy();
  });

  it("removes the row of a decision and opens the next one", async () => {
    const calls = await openScreen(reviewRoutes({ to_review: manyOffers(3) }), false);
    expect(rows()).toHaveLength(3);
    fireEvent.click(rowOf(/Offre 2/));

    fireEvent.click(within(screen.getByRole("article")).getByRole("button", { name: fr["card.later"] }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Offre 3" })).toBeTruthy());
    expect(decisions(calls)).toEqual([["/api/offers/2/decision", { decision: "later" }]]);
    expect(screen.queryByRole("button", { name: /Offre 2/ })).toBeNull();
    expect(rows()).toHaveLength(1); // Offre 1 only: Offre 3 is the open card
  });

  it("opens the previous row when the last card is decided, and none when the list is empty", async () => {
    await openScreen(reviewRoutes({ to_review: manyOffers(2) }), false);
    fireEvent.click(rowOf(/Offre 2/));

    fireEvent.keyDown(document, { key: "ArrowRight" });
    await waitFor(() => expect(screen.getByRole("heading", { name: "Offre 1" })).toBeTruthy());
    fireEvent.keyDown(document, { key: "ArrowRight" });

    await waitFor(() => expect(screen.getByText(fr["review.empty"])).toBeTruthy());
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("moves the focus between rows with the up and down arrows, and does not decide", async () => {
    const calls = await openScreen(reviewRoutes({ to_review: manyOffers(3) }), false);

    fireEvent.keyDown(document, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rowOf(/Offre 1/));
    fireEvent.keyDown(document, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rowOf(/Offre 2/));
    fireEvent.keyDown(document, { key: "ArrowDown" });
    fireEvent.keyDown(document, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rowOf(/Offre 3/));
    fireEvent.keyDown(document, { key: "ArrowUp" });
    expect(document.activeElement).toBe(rowOf(/Offre 2/));
    fireEvent.keyDown(document, { key: "ArrowUp" });
    fireEvent.keyDown(document, { key: "ArrowUp" });
    expect(document.activeElement).toBe(rowOf(/Offre 1/));

    expect(decisions(calls)).toEqual([]);
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("decides only on an open card: the arrows do nothing on closed rows", async () => {
    const calls = await openScreen(reviewRoutes({ to_review: manyOffers(2) }), false);

    fireEvent.keyDown(document, { key: "ArrowRight" });
    fireEvent.keyDown(document, { key: "ArrowLeft" });

    expect(decisions(calls)).toEqual([]);
    expect(screen.queryByText(fr["decline.title"])).toBeNull();
    expect(rows()).toHaveLength(2);
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

  it("in the Later view, goes to the card the candidate scrolled to, not the first", async () => {
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
    const parked = [aCard({ id: 3, title: "Plombier", state: "later" }), { ...bob, state: "later" }];
    const calls = await openScreen(reviewRoutes({ to_review: [aCard()], later: parked }), false);
    fireEvent.click(screen.getByRole("tab", { name: fill(fr["review.tab.later"], { n: 2 }) }));

    seen([screen.getByRole("article", { name: /Développeuse web/ }).id]);
    fireEvent.keyDown(document, { key: "ArrowRight" });

    await waitFor(() => expect(decisions(calls)).toHaveLength(1));
    expect(decisions(calls)[0]?.[0]).toBe("/api/offers/2/decision");
    expect(screen.getByRole("article", { name: /Plombier/ })).toBeTruthy();
  });

  it("keeps the card and says so when the API refuses", async () => {
    fakeApi({ ...reviewRoutes({ to_review: [aCard()] }), [DECISION(1)]: new Response("no", { status: 409 }) });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Ingénieur logiciel/ }));

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

  it("gives enabled buttons cursor: pointer and a hover class, and disabled Save cursor: not-allowed", async () => {
    await openScreen();
    fireEvent.keyDown(document, { key: "ArrowLeft" });

    const cancel = screen.getByRole("button", { name: fr["decline.cancel"] });
    const reasonPill = screen.getByRole("button", { name: new RegExp(fr["reason.company"]) });
    for (const button of [cancel, reasonPill]) {
      expect(button.className).toMatch(/\bcursor-pointer\b/);
      expect(button.className).toMatch(/\bhover:/);
    }

    expect(save().disabled).toBe(true);
    expect(save().className).toContain("disabled:cursor-not-allowed");
    expect(save().className).not.toMatch(/(?:^|\s)hover:/);

    fireEvent.click(reasonPill);
    expect(save().disabled).toBe(false);
    expect(save().className).toMatch(/\bcursor-pointer\b/);
    expect(save().className).toMatch(/\bhover:/);
  });

  it("shows a hint while no reason is chosen with a note typed, then hides it and saves once chosen", async () => {
    const calls = await openScreen();
    fireEvent.keyDown(document, { key: "ArrowLeft" });

    const note = screen.getByLabelText(fr["decline.note"]);
    fireEvent.change(note, { target: { value: "Salaire non indiqué" } });

    expect(fr["decline.needReason"]).toBe("Choisis au moins une raison");
    expect(screen.getByText(fr["decline.needReason"])).toBeTruthy();
    expect(save().disabled).toBe(true);
    fireEvent.click(save());
    fireEvent.keyDown(note, { key: "Enter" });
    expect(decisions(calls)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: new RegExp(fr["reason.salary"]) }));
    expect(screen.queryByText(fr["decline.needReason"])).toBeNull();
    expect(save().disabled).toBe(false);

    fireEvent.click(save());
    await waitFor(() => expect(decisions(calls)).toHaveLength(1));
    expect(decisions(calls)[0]).toEqual([
      "/api/offers/1/decision",
      { decision: "not_interested", reasons: ["salary"], note: "Salaire non indiqué" },
    ]);
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

describe("adding an offer by its URL", () => {
  const url = "https://jobs.example.org/pivoine-analytics/data-engineer?utm_source=newsletter";

  async function add(answer: unknown, language: "fr" | "en" = "fr") {
    if (language === "en") localStorage.setItem("applai.language", "en");
    const calls = await openScreen({ ...reviewRoutes({ to_review: [aCard()] }), "POST /api/offers": answer }, false);
    const words = language === "fr" ? fr : en;
    fireEvent.change(screen.getByRole("textbox", { name: words["review.add.label"] }), { target: { value: ` ${url} ` } });
    fireEvent.click(screen.getByRole("button", { name: words["review.add.submit"] }));
    return { calls, words };
  }

  it.each(["fr", "en"] as const)("calls the route and shows the offer as added, in %s", async (language) => {
    const { calls, words } = await add({ id: 9, url, state: "new", added: true }, language);

    expect(await screen.findByText(words["review.add.added"])).toBeTruthy();
    expect(calls.filter((call) => call.method === "POST" && call.path === "/api/offers")).toEqual([
      { method: "POST", path: "/api/offers", body: { url } },
    ]);
    expect((screen.getByRole("textbox", { name: words["review.add.label"] }) as HTMLInputElement).value).toBe("");
  });

  it("says when the offer was already known", async () => {
    await add({ id: 3, url, state: "triaged", added: false });

    expect(await screen.findByText(fr["review.add.known"])).toBeTruthy();
    expect(screen.queryByText(fr["review.add.added"])).toBeNull();
  });

  it.each([
    [new Response("{}", { status: 422 }), "review.add.refused"],
    [new Response("boom", { status: 500 }), "review.add.error"],
  ] as const)("says why an offer was not added", async (answer, key) => {
    await add(answer);

    expect((await screen.findByRole("alert")).textContent).toBe(fr[key]);
    expect(screen.queryByText(fr["review.add.added"])).toBeNull();
  });
});
