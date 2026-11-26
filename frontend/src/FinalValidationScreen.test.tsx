import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { FormReview } from "./api";
import { aFormReview, aReviewedField, aRow, applicationsRoutes, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const wordsOf = (language: "fr" | "en") => (language === "en" ? en : fr);

async function openValidation(routes: Record<string, unknown> = {}, language: "fr" | "en" = "fr", review: FormReview = aFormReview()) {
  const row = aRow({ id: review.application_id, state: review.state, form_url: review.form_url });
  const calls = fakeApi({
    ...reviewRoutes(),
    ...applicationsRoutes({ waiting: [row] }),
    [`GET /api/applications/${review.application_id}/review`]: review,
    ...routes,
  });
  if (language === "en") localStorage.setItem("applai.language", "en");
  const words = wordsOf(language);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: words["nav.applications"] }));
  fireEvent.click(await screen.findByRole("button", { name: words["validation.open"] }));
  await screen.findByRole("heading", { name: review.title });
  return { calls, words };
}

describe("FinalValidationScreen", () => {
  it("shows each paragraph of a filled cover letter as its own block", async () => {
    const letter = "Madame, Monsieur,\n\nVotre quai m’intéresse.\n\nCordialement,\nJeanne Dupont";
    const field = aReviewedField({ label: "Lettre de motivation", type: "textarea", value: letter });
    await openValidation({}, "fr", aFormReview({ fields: [field] }));

    for (const paragraph of ["Madame, Monsieur,", "Votre quai m’intéresse.", "Cordialement, Jeanne Dupont"]) {
      expect(screen.getByText(paragraph).tagName).toBe("P");
    }
  });

  it("shows Check only for reviewed, form_checked and validated rows, and Back reloads the board", async () => {
    const calls = fakeApi({
      ...reviewRoutes(),
      ...applicationsRoutes({
        in_progress: [aRow({ id: 5, offer_id: 5, state: "filled" })],
        waiting: [
          aRow({ id: 1, offer_id: 1, state: "reviewed" }), aRow({ id: 2, offer_id: 2, state: "form_checked" }),
          aRow({ id: 3, offer_id: 3, state: "validated" }), aRow({ id: 4, offer_id: 4, state: "drafted" }),
        ],
        sent: [aRow({ id: 6, offer_id: 6, state: "sent" })],
      }),
      "GET /api/applications/1/review": aFormReview(),
    });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));
    const buttons = await screen.findAllByRole("button", { name: fr["validation.open"] });
    expect(buttons).toHaveLength(3);

    fireEvent.click(buttons[0] as HTMLElement);
    await screen.findByRole("heading", { name: "Chef de quai" });
    fireEvent.click(screen.getByRole("button", { name: fr["correct.back"] }));
    await screen.findAllByRole("region");
    expect(calls.filter((c) => c.method === "GET" && c.path === "/api/applications")).toHaveLength(2);
  });

  it.each(["fr", "en"] as const)("shows offer links, problems, fields, PDFs, missing file and screenshot in %s", async (language) => {
    const review = aFormReview({
      fields: [
        aReviewedField(),
        aReviewedField({ field: 1, label: "Compétences", type: "checkbox", value: ["Python", "SQL"] }),
        aReviewedField({ field: 2, label: "CV", type: "file", value: null, file: "cv-camille.pdf", slot: "cv" }),
        aReviewedField({ field: 3, label: "Lettre", type: "file", value: null, file: null, slot: "cover_letter" }),
      ],
      problems: [
        { severity: "blocking", field: "Lettre", message: "Fichier obligatoire absent." },
        { severity: "warning", field: "Compétences", message: "Vérifier l’ordre." },
      ],
    });
    const { words } = await openValidation({}, language, review);

    expect(screen.getByRole("link", { name: words["applications.offer"] }).getAttribute("href")).toBe(review.url);
    expect(screen.getByRole("link", { name: words["applications.form"] }).getAttribute("href")).toBe(review.form_url);
    expect(screen.getByText(words["validation.severity.blocking"])).toBeTruthy();
    expect(screen.getByText("Fichier obligatoire absent.")).toBeTruthy();
    expect(screen.getByText(words["validation.severity.warning"])).toBeTruthy();
    expect(screen.getByText("Vérifier l’ordre.")).toBeTruthy();
    expect(screen.getByText("camille@example.org")).toBeTruthy();
    expect(screen.getByText("Python, SQL")).toBeTruthy();
    expect(screen.getByText("cv-camille.pdf")).toBeTruthy();
    expect(screen.getByText(words["validation.field.missing_file"])).toBeTruthy();
    const cv = screen.getByRole("link", { name: words["validation.field.see_cv"] });
    const letter = screen.getByRole("link", { name: words["validation.field.see_cover_letter"] });
    expect(cv.getAttribute("href")).toBe("/api/applications/1/files/cv");
    expect(letter.getAttribute("href")).toBe("/api/applications/1/files/cover_letter");
    for (const eye of [cv, letter]) {
      expect(eye.getAttribute("target")).toBe("_blank");
      expect(eye.getAttribute("rel")).toBe("noreferrer");
      expect(eye.querySelector("svg[data-icon='eye']")).not.toBeNull();
    }
    expect(screen.getByAltText(words["validation.screenshot.alt"]).getAttribute("src")).toBe("/api/applications/1/screenshot");
  });

  it("shows no eye button for a file field with no slot", async () => {
    const review = aFormReview({
      fields: [aReviewedField({ field: 0, label: "Portfolio", type: "file", value: null, file: null, slot: null })],
    });
    const { words } = await openValidation({}, "fr", review);

    expect(screen.getByText(words["validation.field.missing_file"])).toBeTruthy();
    expect(screen.queryByRole("link", { name: words["validation.field.see_cv"] })).toBeNull();
    expect(screen.queryByRole("link", { name: words["validation.field.see_cover_letter"] })).toBeNull();
  });

  it("shows clean message when no problem, omits screenshot when absent, and alerts on load error", async () => {
    const { words } = await openValidation({}, "fr", aFormReview({ problems: [], has_screenshot: false, form_url: null }));
    expect(screen.getByText(words["validation.problems.none"])).toBeTruthy();
    expect(screen.queryByAltText(words["validation.screenshot.alt"])).toBeNull();
    expect(screen.queryByRole("link", { name: words["applications.form"] })).toBeNull();

    vi.unstubAllGlobals();
    fakeApi({ ...reviewRoutes(), ...applicationsRoutes({ waiting: [aRow({ state: "reviewed" })] }) });
    fireEvent.click(screen.getByRole("button", { name: words["correct.back"] }));
    fireEvent.click(await screen.findByRole("button", { name: fr["validation.open"] }));
    expect((await screen.findByRole("alert")).textContent).toBe(fr["validation.error"]);
  });

  it.each(["fr", "en"] as const)("walks reviewed -> form_checked -> validated -> sent and reloads dashboard in %s", async (language) => {
    let board = applicationsRoutes({ waiting: [aRow({ state: "reviewed" })] });
    const review = aFormReview({ form_url: null });
    const { calls, words } = await openValidation({
      "GET /api/applications": () => board["GET /api/applications"],
      "POST /api/applications/1/form-checked": new Response(null, { status: 204 }),
      "POST /api/applications/1/validate": new Response(null, { status: 204 }),
      "POST /api/applications/1/sent": () => {
        board = applicationsRoutes({ sent: [aRow({ state: "sent" })] });
        return new Response(null, { status: 204 });
      },
    }, language, review);

    fireEvent.click(screen.getByRole("checkbox", { name: /Téléphone/ }));
    fireEvent.click(screen.getByRole("button", { name: words["validation.form_checked"] }));
    const validateBtn = await screen.findByRole("button", { name: words["validation.validate"] });
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/form-checked", body: { corrected_fields: ["Téléphone"] } },
    ]);

    fireEvent.click(validateBtn);
    const sentBtn = await screen.findByRole("button", { name: words["validation.mark_sent"] });
    expect(screen.getByRole("link", { name: words["validation.open_form"] }).getAttribute("href")).toBe(review.url);
    expect(screen.getByRole("button", { name: words["validation.authorize_send"] })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /submit|soumett/i })).toBeNull();

    fireEvent.click(sentBtn);
    expect(await screen.findByText(words["applications.state.sent"])).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      "/api/applications/1/form-checked", "/api/applications/1/validate", "/api/applications/1/sent",
    ]);
  });

  it.each(["fr", "en"] as const)("asks why in the drop panel, returns to dashboard, and handles action failure in %s", async (language) => {
    let board = applicationsRoutes({ waiting: [aRow({ state: "validated" })] });
    let failOnce = true;
    const { calls, words } = await openValidation({
      "GET /api/applications": () => board["GET /api/applications"],
      "POST /api/applications/1/drop": () => {
        if (failOnce) { failOnce = false; return new Response("conflict", { status: 409 }); }
        board = applicationsRoutes({ dropped: [aRow({ state: "dropped" })] });
        return new Response(null, { status: 204 });
      },
    }, language, aFormReview({ state: "validated" }));

    fireEvent.click(screen.getByRole("button", { name: words["validation.drop"] }));
    fireEvent.click(screen.getByRole("button", { name: words["decline.cancel"] }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: words["validation.drop"] }));
    expect(screen.getByText(words["drop.title"])).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(words["reason.offer_closed"]) }));
    fireEvent.click(screen.getByRole("button", { name: words["decline.save"] }));
    expect((await screen.findByRole("alert")).textContent).toBe(words["validation.action_failed"]);

    fireEvent.click(screen.getByRole("button", { name: words["decline.save"] }));
    expect(await screen.findByText(words["applications.state.dropped"])).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
      { reasons: ["offer_closed"] },
      { reasons: ["offer_closed"] },
    ]);
  });

  it.each(["fr", "en"] as const)("shows the CV warning of the application in %s", async (language) => {
    const review = aFormReview({ cv_warning: "cv_no_photo_file:cv/photo.jpg" });
    const { words } = await openValidation({}, language, review);

    const warning = words["message.cv_no_photo_file"].replace("{detail}", "cv/photo.jpg");
    expect(screen.getByRole("alert").textContent).toBe(words["validation.cv_warning"].replace("{warning}", warning));
  });

  it.each(["fr", "en"] as const)("words the code's own problem and shows free text as it is, in %s", async (language) => {
    const review = aFormReview({
      review_error: "agent review failed: timeout",
      problems: [{ severity: "blocking", field: "CV", message: "required_empty" }],
    });
    const { words } = await openValidation({}, language, review);

    expect(screen.getByText(words["message.required_empty"])).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe(
      words["validation.review_error"].replace("{error}", "agent review failed: timeout"),
    );
  });

  it.each(["fr", "en"] as const)("fills a form again in a new tab after a failed fill again, in %s", async (language) => {
    const review = aFormReview({ state: "validated", send_authorized: true, fill_error: "fill_again_not_loaded" });
    const { calls, words } = await openValidation({
      "POST /api/applications/1/fill-again": new Response(null, { status: 204 }),
    }, language, review);

    expect(screen.getByRole("alert").textContent).toBe(
      words["validation.fill_error"].replace("{error}", words["message.fill_again_not_loaded"]),
    );
    fireEvent.click(screen.getByRole("button", { name: words["validation.fill_again"] }));

    const asked = await screen.findByRole("button", { name: words["validation.fill_again_asked"] }) as HTMLButtonElement;
    expect(asked.disabled).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
    const authorize = screen.getByRole("button", { name: words["validation.authorize_send"] }) as HTMLButtonElement;
    expect(authorize.disabled).toBe(true);
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/fill-again", body: undefined },
    ]);
  });

  it("loads the review again while a fill again waits and shows its failure", async () => {
    const asked = aFormReview({ state: "validated" });
    const failed = aFormReview({ state: "validated", fill_error: "fill_again_not_loaded" });
    let reloads = 0;
    const { words } = await openValidation({
      "POST /api/applications/1/fill-again": new Response(null, { status: 204 }),
      "GET /api/applications/1/review": () => (reloads++ === 0 ? asked : failed),
    }, "en", asked);

    fireEvent.click(screen.getByRole("button", { name: words["validation.fill_again"] }));
    await screen.findByRole("button", { name: words["validation.fill_again_asked"] });

    const alert = await screen.findByRole("alert", {}, { timeout: 5000 });
    expect(alert.textContent).toBe(
      words["validation.fill_error"].replace("{error}", words["message.fill_again_not_loaded"]),
    );
    const again = screen.getByRole("button", { name: words["validation.fill_again"] }) as HTMLButtonElement;
    expect(again.disabled).toBe(false);
  }, 10000);

  it("shows the new tab's fields and screenshot after a fill again", async () => {
    const asked = aFormReview({ state: "validated" });
    const filled = aFormReview({ state: "validated", fields: [aReviewedField({ label: "First name", value: "Camille Refilled" })] });
    let reloads = 0;
    const { words } = await openValidation({
      "POST /api/applications/1/fill-again": new Response(null, { status: 204 }),
      "GET /api/applications/1/review": () => (reloads++ === 0 ? asked : filled),
    }, "en", asked);
    const image = () => screen.getByAltText(words["validation.screenshot.alt"]).getAttribute("src");
    const before = image();

    fireEvent.click(screen.getByRole("button", { name: words["validation.fill_again"] }));
    await screen.findByRole("button", { name: words["validation.fill_again_asked"] });

    await screen.findByText("Camille Refilled", {}, { timeout: 5000 });
    expect(screen.getByRole("button", { name: words["validation.fill_again"] })).toBeTruthy();
    expect(image()).not.toBe(before);
    expect(image()?.startsWith("/api/applications/1/screenshot?")).toBe(true);
  }, 10000);

  it("offers to fill again in every state the screen shows", async () => {
    for (const state of ["reviewed", "form_checked", "validated"]) {
      const { words } = await openValidation({}, "fr", aFormReview({ state }));
      expect(screen.getByRole("button", { name: words["validation.fill_again"] })).toBeTruthy();
      cleanup(); vi.unstubAllGlobals();
    }
  });

  it.each(["fr", "en"] as const)("shows send_error alert and authorizes ApplAI to send in %s", async (language) => {
    const review = aFormReview({ state: "validated", send_error: "send_no_tab" });
    const { calls, words } = await openValidation({
      "POST /api/applications/1/authorize-send": new Response(null, { status: 204 }),
    }, language, review);

    expect(screen.getByRole("alert").textContent).toContain(words["message.send_no_tab"]);
    const authorizeBtn = screen.getByRole("button", { name: words["validation.authorize_send"] }) as HTMLButtonElement;
    expect(authorizeBtn.disabled).toBe(false);

    fireEvent.click(authorizeBtn);
    const authorizedBtn = await screen.findByRole("button", { name: words["validation.send_authorized"] }) as HTMLButtonElement;
    expect(authorizedBtn.disabled).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      { method: "POST", path: "/api/applications/1/authorize-send", body: undefined },
    ]);
  });
});
