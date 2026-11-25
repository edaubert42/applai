import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Answer, Draft } from "./api";
import { aCvChange, aDraft, aRow, anAnswer, applicationsRoutes, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const DRAFT = "GET /api/applications/1/draft";
const decision = (answer: number) => `POST /api/applications/1/draft/${answer}`;
const CHECKED = "POST /api/applications/1/answers-checked";

const second = anAnswer({
  id: 2,
  kind: "cover_letter",
  field_index: 1,
  label: "Lettre de motivation",
  sentences: [{ text: "Votre quai m’intéresse.", facts: [{ id: "#F9", text: "Quai de Brindille visité en 2025." }] }],
  text: "Votre quai m’intéresse.",
});

type Language = "fr" | "en";
const wordsOf = (language: Language) => (language === "en" ? en : fr);

/** Opens the Applications dashboard with one drafted row, then its Correct screen. */
async function openCorrect(
  routes: Record<string, unknown> = {},
  language: Language = "fr",
  draft: Draft = aDraft([anAnswer(), second]),
) {
  const calls = fakeApi({
    ...reviewRoutes(),
    ...applicationsRoutes({ waiting: [aRow()] }),
    [DRAFT]: draft,
    ...routes,
  });
  if (language === "en") localStorage.setItem("applai.language", "en");
  render(<App />);
  const words = wordsOf(language);
  fireEvent.click(screen.getByRole("button", { name: words["nav.applications"] }));
  fireEvent.click(await screen.findByRole("button", { name: words["correct.open"] }));
  await screen.findByRole("heading", { name: "Chef de quai" });
  return { calls, words };
}

const answerNamed = (name: string) => within(screen.getByRole("article", { name }));

describe("the entry point on the Applications dashboard", () => {
  it("gives only a drafted row a Correct button", async () => {
    fakeApi({
      ...reviewRoutes(),
      ...applicationsRoutes({
        waiting: [aRow(), aRow({ id: 2, offer_id: 2, title: "Analyste", state: "form_checked" })],
      }),
    });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));
    await screen.findAllByRole("region");

    expect(screen.getAllByRole("button", { name: fr["correct.open"] })).toHaveLength(1);
  });

  it("goes back to the dashboard with the Back button", async () => {
    const { words } = await openCorrect();

    fireEvent.click(screen.getByRole("button", { name: words["correct.back"] }));

    expect(await screen.findAllByRole("region")).not.toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "Chef de quai", level: 2 })).toBeNull();
  });
});

describe("the Correct screen", () => {
  it.each(["fr", "en"] as const)(
    "shows the offer, each question, its text, its cited facts and its judge warning, in %s",
    async (language) => {
      const warned = anAnswer({ warnings: ["La phrase « 20 % » n’est pas soutenue par le fait cité."] });
      const { words } = await openCorrect({}, language, aDraft([warned, second]));

      expect(screen.getByRole("heading", { name: "Chef de quai" })).toBeTruthy();
      expect(screen.getByText("Brindille Logistique")).toBeTruthy();
      expect(screen.getByRole("link", { name: words["applications.offer"] }).getAttribute("href")).toBe(
        "https://jobs.example.org/brindille-logistique/chef-de-quai",
      );
      const question = answerNamed("Pourquoi ce poste ?");
      expect(question.getByText(/J’ai organisé les quais/)).toBeTruthy();
      expect(question.getByText("#F1")).toBeTruthy();
      expect(question.getByText("Responsable de quai chez Silo Nord, équipe de 40 personnes.")).toBeTruthy();
      expect(question.getByText("#F2")).toBeTruthy();
      expect(question.getByText(words["correct.status.pending"])).toBeTruthy();
      expect(question.getByRole("note").textContent).toContain("n’est pas soutenue par le fait cité");
      const letter = answerNamed("Lettre de motivation");
      expect(letter.getByText("#F9")).toBeTruthy();
      expect(letter.queryByRole("note")).toBeNull();
    },
  );

  it("shows every warning of an answer in its own card, and none on an answer without any", async () => {
    const flagged = anAnswer({
      warnings: ["Sentence 1: F1 says a team of 40, not a warehouse of 40.", "Sentence 2: F2 gives no year."],
    });
    const unchecked = { ...second, warnings: ["Not checked by Judge."] };
    const clean = anAnswer({ id: 3, field_index: 2, label: "Disponibilité ?", warnings: [] });
    await openCorrect({}, "fr", aDraft([flagged, unchecked, clean]));

    const notes = (name: string) => answerNamed(name).queryAllByRole("note").map((note) => note.textContent);
    expect(notes("Pourquoi ce poste ?")).toEqual([
      "À vérifier : Sentence 1: F1 says a team of 40, not a warehouse of 40.",
      "À vérifier : Sentence 2: F2 gives no year.",
    ]);
    expect(notes("Lettre de motivation")).toEqual(["À vérifier : Not checked by Judge."]);
    expect(notes("Disponibilité ?")).toEqual([]);
  });

  it("keeps the same keys in French and English", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(fr).sort());
  });

  it("tells the candidate when the draft could not be loaded", async () => {
    fakeApi({ ...reviewRoutes(), ...applicationsRoutes({ waiting: [aRow()] }) });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));
    fireEvent.click(await screen.findByRole("button", { name: fr["correct.open"] }));

    expect((await screen.findByRole("alert")).textContent).toBe(fr["correct.error"]);
  });
});

describe("a cover letter in paragraphs", () => {
  const body = ["Votre quai m’intéresse.", "J’ai organisé les quais de Silo Nord.", "Parlons-en."];
  const signed = "Cordialement,\nJeanne Dupont";
  const text = ["Madame, Monsieur,", ...body, signed].join("\n\n");
  const sentences = body.map((sentence) => ({ text: sentence, facts: [{ id: "#F9", text: "Quai visité." }] }));
  const letter = { ...second, sentences, text };

  it("shows each paragraph of the letter as its own block", async () => {
    await openCorrect({}, "fr", aDraft([letter]));
    const card = answerNamed("Lettre de motivation");

    for (const paragraph of ["Madame, Monsieur,", ...body, "Cordialement, Jeanne Dupont"]) {
      expect(card.getByText(paragraph).tagName).toBe("P");
    }
  });

  it("keeps the blank lines of the letter when it is edited", async () => {
    const changed = text.replace("Parlons-en.", "Je serais heureuse d’en parler.");
    const edited = { ...letter, status: "edited" as const, edited_text: changed };
    const { calls, words } = await openCorrect({ [decision(2)]: edited }, "fr", aDraft([letter]));
    const card = answerNamed("Lettre de motivation");

    fireEvent.click(card.getByRole("button", { name: words["correct.edit"] }));
    const box = card.getByLabelText(words["correct.edit_text"]) as HTMLTextAreaElement;
    expect(box.value).toBe(text);
    fireEvent.change(box, { target: { value: changed } });
    fireEvent.click(card.getByRole("button", { name: words["correct.save"] }));

    expect((await card.findByText("Je serais heureuse d’en parler.")).tagName).toBe("P");
    expect(card.getByText("J’ai organisé les quais de Silo Nord.").tagName).toBe("P");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "edit", text: changed });
  });
});

describe("deciding on an answer", () => {
  it("accepts an answer: posts accept and shows it accepted", async () => {
    const accepted: Answer = anAnswer({ status: "accepted" });
    const { calls, words } = await openCorrect({ [decision(1)]: accepted });

    fireEvent.click(answerNamed("Pourquoi ce poste ?").getByRole("button", { name: words["correct.accept"] }));

    expect(await answerNamed("Pourquoi ce poste ?").findByText(words["correct.status.accepted"])).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/draft/1", body: { decision: "accept" } },
    ]);
  });

  it("edits an answer: the textarea holds the current text, Save sends the new one and the reason", async () => {
    const edited = anAnswer({ status: "edited", edited_text: "J’ai dirigé 40 personnes." });
    const { calls, words } = await openCorrect({ [decision(1)]: edited });
    const question = answerNamed("Pourquoi ce poste ?");

    fireEvent.click(question.getByRole("button", { name: words["correct.edit"] }));
    const box = question.getByLabelText(words["correct.edit_text"]) as HTMLTextAreaElement;
    expect(box.value).toBe(anAnswer().text);
    fireEvent.change(box, { target: { value: "J’ai dirigé 40 personnes." } });
    fireEvent.change(question.getByLabelText(words["correct.reason"]), { target: { value: "Plus direct" } });
    fireEvent.click(question.getByRole("button", { name: words["correct.save"] }));

    expect(await question.findByText(words["correct.status.edited"])).toBeTruthy();
    expect(question.getByText("J’ai dirigé 40 personnes.")).toBeTruthy();
    expect(question.queryByLabelText(words["correct.edit_text"])).toBeNull();
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      {
        method: "POST",
        path: "/api/applications/1/draft/1",
        body: { decision: "edit", text: "J’ai dirigé 40 personnes.", reason: "Plus direct" },
      },
    ]);
  });

  it("sends an edit without a reason when none is typed", async () => {
    const edited = anAnswer({ status: "edited", edited_text: "Court." });
    const { calls, words } = await openCorrect({ [decision(1)]: edited });
    const question = answerNamed("Pourquoi ce poste ?");

    fireEvent.click(question.getByRole("button", { name: words["correct.edit"] }));
    fireEvent.change(question.getByLabelText(words["correct.edit_text"]), { target: { value: "Court." } });
    fireEvent.click(question.getByRole("button", { name: words["correct.save"] }));

    await question.findByText(words["correct.status.edited"]);
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "edit", text: "Court." });
  });

  it("rejects only with a reason, waits for the new draft, and keeps the sibling answers", async () => {
    const accepted = anAnswer({ status: "accepted" });
    const redrafted: Answer = { ...second, text: "Un nouveau quai.", sentences: [{ text: "Un nouveau quai.", facts: [] }] };
    let release: () => void = () => {};
    const { calls, words } = await openCorrect(
      { [decision(2)]: () => new Promise<Answer>((resolve) => (release = () => resolve(redrafted))) },
      "fr",
      aDraft([accepted, { ...second, status: "pending" }]),
    );
    const letter = answerNamed("Lettre de motivation");

    fireEvent.click(letter.getByRole("button", { name: words["correct.reject"] }));
    const regenerate = letter.getByRole("button", { name: words["correct.regenerate"] }) as HTMLButtonElement;
    expect(regenerate.disabled).toBe(true);
    fireEvent.change(letter.getByLabelText(words["correct.reject_reason"]), { target: { value: "  " } });
    expect(regenerate.disabled).toBe(true);
    fireEvent.change(letter.getByLabelText(words["correct.reject_reason"]), { target: { value: "Trop vague" } });
    expect(regenerate.disabled).toBe(false);
    fireEvent.click(regenerate);

    await waitFor(() =>
      expect((letter.getByRole("button", { name: words["correct.regenerating"] }) as HTMLButtonElement).disabled).toBe(true),
    );
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/draft/2", body: { decision: "reject", reason: "Trop vague" } },
    ]);
    // ... the answer text is the one from the response, once it comes
    release();
    expect(await letter.findByText("Un nouveau quai.")).toBeTruthy();
    expect(letter.queryByLabelText(words["correct.reject_reason"])).toBeNull();
    expect(answerNamed("Pourquoi ce poste ?").getByText(words["correct.status.accepted"])).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("tells the candidate when an answer could not be drafted again, and leaves the others alone", async () => {
    const { words } = await openCorrect({
      [decision(1)]: new Response("bad gateway", { status: 502 }),
    });
    const question = answerNamed("Pourquoi ce poste ?");

    fireEvent.click(question.getByRole("button", { name: words["correct.reject"] }));
    fireEvent.change(question.getByLabelText(words["correct.reject_reason"]), { target: { value: "Faux" } });
    fireEvent.click(question.getByRole("button", { name: words["correct.regenerate"] }));

    expect((await question.findByRole("alert")).textContent).toBe(words["correct.decision_failed"]);
    expect(answerNamed("Lettre de motivation").queryByRole("alert")).toBeNull();
    expect((question.getByRole("button", { name: words["correct.regenerate"] }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("cannot accept an answer that was rejected and not drafted again", async () => {
    const { words } = await openCorrect({}, "fr", aDraft([anAnswer({ status: "rejected" })]));

    const accept = answerNamed("Pourquoi ce poste ?").getByRole("button", { name: words["correct.accept"] });
    expect((accept as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the answers gate", () => {
  it("disables Validate while an answer is pending and enables it once all are accepted or edited", async () => {
    const answered = [anAnswer({ status: "accepted" }), second];
    const { words } = await openCorrect(
      { [decision(2)]: { ...second, status: "edited", edited_text: "Votre quai." } },
      "fr",
      aDraft(answered),
    );
    const validate = screen.getByRole("button", { name: words["correct.validate"] }) as HTMLButtonElement;
    expect(validate.disabled).toBe(true);

    const letter = answerNamed("Lettre de motivation");
    fireEvent.click(letter.getByRole("button", { name: words["correct.edit"] }));
    fireEvent.change(letter.getByLabelText(words["correct.edit_text"]), { target: { value: "Votre quai." } });
    fireEvent.click(letter.getByRole("button", { name: words["correct.save"] }));

    await waitFor(() => expect(validate.disabled).toBe(false));
  });

  it("stays disabled while an answer is rejected", async () => {
    const { words } = await openCorrect({}, "fr", aDraft([anAnswer({ status: "accepted" }), { ...second, status: "rejected" }]));

    expect((screen.getByRole("button", { name: words["correct.validate"] }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(["fr", "en"] as const)(
    "validates the answers, calls answers-checked and returns to the dashboard, in %s",
    async (language) => {
      let board = applicationsRoutes({ waiting: [aRow()] });
      const calls = fakeApi({
        ...reviewRoutes(),
        "GET /api/applications": () => board["GET /api/applications"],
        [DRAFT]: aDraft([anAnswer({ status: "accepted" }), { ...second, status: "edited", edited_text: "Oui." }]),
        [CHECKED]: () => {
          board = applicationsRoutes({ waiting: [aRow({ state: "answers_checked" })] });
          return new Response(null, { status: 204 });
        },
      });
      if (language === "en") localStorage.setItem("applai.language", "en");
      const words = wordsOf(language);
      render(<App />);
      fireEvent.click(screen.getByRole("button", { name: words["nav.applications"] }));
      fireEvent.click(await screen.findByRole("button", { name: words["correct.open"] }));
      const validate = (await screen.findByRole("button", { name: words["correct.validate"] })) as HTMLButtonElement;
      expect(validate.disabled).toBe(false);

      fireEvent.click(validate);

      expect(await screen.findByText(words["applications.state.answers_checked"])).toBeTruthy();
      expect(screen.queryByRole("button", { name: words["correct.open"] })).toBeNull();
      expect(calls.filter((c) => c.method === "POST")).toEqual([
        { method: "POST", path: "/api/applications/1/answers-checked", body: undefined },
      ]);
    },
  );

  it("enables Validate when the draft has no answer", async () => {
    const { words } = await openCorrect({}, "fr", aDraft([]));

    expect((screen.getByRole("button", { name: words["correct.validate"] }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("posts answers-checked once when Validate is clicked twice", async () => {
    let release: (reply: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls, words } = await openCorrect({ [CHECKED]: () => pending }, "fr", aDraft([anAnswer({ status: "accepted" })]));
    const validate = screen.getByRole("button", { name: words["correct.validate"] }) as HTMLButtonElement;

    fireEvent.click(validate);
    fireEvent.click(validate);

    expect(calls.filter((c) => c.path.endsWith("/answers-checked"))).toHaveLength(1);
    expect(validate.disabled).toBe(true);
    release(new Response("no", { status: 409 }));
    await waitFor(() => expect(validate.disabled).toBe(false));
  });

  it("stays on the screen and says so when the gate refuses", async () => {
    const { words } = await openCorrect(
      { [CHECKED]: new Response("no", { status: 409 }) },
      "fr",
      aDraft([anAnswer({ status: "accepted" })]),
    );

    fireEvent.click(screen.getByRole("button", { name: words["correct.validate"] }));

    expect((await screen.findByRole("alert")).textContent).toBe(words["correct.validate_failed"]);
    expect(screen.getByRole("heading", { name: "Chef de quai" })).toBeTruthy();
  });

  it("offers no button that fills or sends the form", async () => {
    const { words } = await openCorrect();

    const names = screen.getAllByRole("button").map((button) => button.textContent);
    const allowed = ["nav.review", "nav.applications", "nav.control", "nav.settings", "correct.back", "correct.accept", "correct.edit", "correct.reject", "correct.validate"].map(
      (key) => words[key as keyof typeof words],
    );
    expect(names.filter((name) => !allowed.includes(name ?? ""))).toEqual([]);
    expect(screen.queryByRole("button", { name: /fill|send|submit|rempli|envoy|soumett/i })).toBeNull();
  });
});

describe("the CV block", () => {
  const reword = aCvChange();
  const drop = aCvChange({ id: 11, op: "drop", path: "experience[0].bullets[2]", old_text: "Animation d’un atelier.", new_text: null, facts: [] });
  const reorder = aCvChange({
    id: 12,
    op: "reorder",
    path: "skills",
    old_text: "[]",
    new_text: "[]",
    old_items: ["SQL", "Python"],
    new_items: ["Python", "SQL"],
    facts: [],
  });
  const tailored = (changes = [reword, drop, reorder]) =>
    aDraft([anAnswer({ status: "accepted" })], {
      cv_headline: "Analytics engineer",
      cv_photo: "with",
      cv_photo_reason: "Entreprise française.",
      cv_changes: changes,
    });
  const change = (id: number) => `POST /api/applications/1/cv/changes/${id}`;
  const block = () => within(screen.getByRole("region", { name: fr["cv.title"] }));
  const rowOf = (text: string) => within(screen.getByText(text).closest("li") as HTMLElement);

  it("shows the headline, a reword before and after with its facts, the new order and the dropped line", async () => {
    await openCorrect({}, "fr", tailored());

    expect(block().getByText("Analytics engineer")).toBeTruthy();
    expect(block().getByText(reword.old_text)).toBeTruthy();
    expect(block().getByText(reword.new_text as string)).toBeTruthy();
    expect(block().getByText(/Retards de livraison réduits de 20 %/)).toBeTruthy();
    expect(block().getAllByRole("listitem").map((item) => item.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining("Animation d’un atelier.")]),
    );
    expect(block().getByText(/Python/).closest("ol")?.textContent).toBe("PythonSQL");
    expect(block().getByText("Entreprise française.")).toBeTruthy();
  });

  it("rejects a change with a reason through the reject route", async () => {
    const rejected = { ...reword, status: "rejected" };
    const { calls } = await openCorrect({ [change(10)]: rejected }, "fr", tailored());
    const row = rowOf(reword.new_text as string);

    fireEvent.click(row.getByRole("button", { name: fr["cv.reject"] }));
    fireEvent.change(row.getByLabelText(fr["correct.reject_reason"]), { target: { value: "Trop vague" } });
    fireEvent.click(row.getByRole("button", { name: fr["cv.reject_confirm"] }));

    expect(await row.findByText(fr["correct.status.rejected"])).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/cv/changes/10", body: { decision: "reject", reason: "Trop vague" } },
    ]);
  });

  it("edits a reword's text and the headline", async () => {
    const edited = { ...reword, status: "edited", edited_text: "Réduit les retards de 20 %." };
    const { calls } = await openCorrect(
      {
        [change(10)]: edited,
        "POST /api/applications/1/cv/headline": { ...tailored(), cv_headline: "Data lead" },
      },
      "fr",
      tailored(),
    );
    const row = rowOf(reword.new_text as string);

    fireEvent.click(row.getByRole("button", { name: fr["correct.edit"] }));
    fireEvent.change(row.getByLabelText(fr["correct.edit_text"]), { target: { value: "Réduit les retards de 20 %." } });
    fireEvent.click(row.getByRole("button", { name: fr["correct.save"] }));
    expect(await row.findByText("Réduit les retards de 20 %.")).toBeTruthy();

    fireEvent.click(block().getByRole("button", { name: fr["cv.headline_edit"] }));
    fireEvent.change(block().getByLabelText(fr["cv.headline"]), { target: { value: "Data lead" } });
    fireEvent.click(block().getByRole("button", { name: fr["correct.save"] }));
    expect(await block().findByText("Data lead")).toBeTruthy();

    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
      { decision: "edit", text: "Réduit les retards de 20 %." },
      { text: "Data lead" },
    ]);
  });

  it("offers a reorder and a drop no edit, and keeps Validate off until each change is decided", async () => {
    await openCorrect({}, "fr", tailored());
    const validate = () => screen.getByRole("button", { name: fr["correct.validate"] }) as HTMLButtonElement;

    expect(rowOf("Animation d’un atelier.").queryByRole("button", { name: fr["correct.edit"] })).toBeNull();
    expect(validate().disabled).toBe(true);
  });

  it("enables Validate once every change is decided", async () => {
    const decided = tailored([{ ...reword, status: "accepted" }, { ...drop, status: "rejected" }]);
    await openCorrect({}, "fr", decided);

    expect((screen.getByRole("button", { name: fr["correct.validate"] }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("switches the photo and shows its reason", async () => {
    const without = { ...tailored(), cv_photo: "without" };
    const { calls } = await openCorrect({ "POST /api/applications/1/cv/photo": without }, "fr", tailored());

    fireEvent.click(block().getByRole("button", { name: fr["cv.photo_without"] }));

    expect(await block().findByText(fr["cv.photo.without"])).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/cv/photo", body: { photo: "without" } },
    ]);
  });

  it("has no CV block when nothing was tailored", async () => {
    await openCorrect();

    expect(screen.queryByRole("region", { name: fr["cv.title"] })).toBeNull();
  });
});
