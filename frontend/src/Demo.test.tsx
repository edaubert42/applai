import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ApplicationTrace } from "./api";
import { aFormReview, aRow, applicationsRoutes, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const words = { fr, en };
const health = (demo: boolean) => ({ status: "ok", database_revision: "0001", demo });
const TRACE: ApplicationTrace = { steps: [], total_cost_usd: 0, total_duration_ms: 0 };
const waiting = [
  aRow({ id: 3, offer_id: 3, title: "Analyste logistique", state: "validated" }),
  aRow({ id: 7, offer_id: 7, title: "Chef de quai", state: "drafted" }),
];

function renderApp(demo: boolean, language: "fr" | "en" = "fr", routes: Record<string, unknown> = {}) {
  const calls = fakeApi({
    ...reviewRoutes(),
    ...applicationsRoutes({ waiting }),
    "GET /api/health": health(demo),
    "GET /api/applications/7/trace": TRACE,
    "GET /api/applications/3/review": aFormReview({ application_id: 3, offer_id: 3, title: "Analyste logistique", state: "validated" }),
    ...routes,
  });
  if (language === "en") localStorage.setItem("applai.language", "en");
  render(<App />);
  return calls;
}

const heading1 = (name: string) => screen.findByRole("heading", { level: 1, name });

describe("the demo", () => {
  it.each(["fr", "en"] as const)("shows a banner on every page, in %s", async (language) => {
    const w = words[language];
    renderApp(true, language);

    for (const page of ["review", "applications", "control", "settings"] as const) {
      fireEvent.click(screen.getByRole("button", { name: w[`nav.${page}`] }));
      await heading1(w[`nav.${page}`]);
      expect((await screen.findByRole("note")).textContent).toBe(w["demo.banner"]);
    }
    expect(w["demo.banner"]).toBe(
      language === "fr"
        ? "Démo : candidate fictive, agents rejoués, rien n’est envoyé"
        : "Demo: fictional candidate, replayed agents, nothing is sent",
    );
  });

  it("shows no banner outside the demo", async () => {
    const calls = renderApp(false);
    await heading1(fr["nav.review"]);

    expect(calls.map((call) => call.path)).toContain("/api/health");
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("shows no banner when the health check fails, and the app still works", async () => {
    renderApp(false, "fr", { "GET /api/health": new Response("down", { status: 500 }) });

    await heading1(fr["nav.review"]);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("opens on Applications with the trace of the application waiting at the texts' gate", async () => {
    renderApp(true);

    await screen.findByRole("heading", { name: fr["trace.title"] });
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(fr["nav.applications"]);
    expect(screen.getByRole("button", { name: fr["nav.applications"] }).getAttribute("aria-current")).toBe("page");
  });

  it("opens the trace once: coming back to Applications shows the board", async () => {
    renderApp(true);
    await screen.findByRole("heading", { name: fr["trace.title"] });

    fireEvent.click(screen.getByRole("button", { name: fr["correct.back"] }));
    await screen.findAllByRole("region");
    fireEvent.click(screen.getByRole("button", { name: fr["nav.settings"] }));
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));

    await screen.findAllByRole("region");
    expect(screen.queryByRole("heading", { name: fr["trace.title"] })).toBeNull();
  });

  it("opens on À revoir outside the demo", async () => {
    renderApp(false);

    await heading1(fr["nav.review"]);
    expect(screen.queryByRole("heading", { name: fr["trace.title"] })).toBeNull();
  });

  it("opens the board when no application is waiting", async () => {
    renderApp(true, "fr", applicationsRoutes());

    await screen.findAllByRole("region");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(fr["nav.applications"]);
  });

  async function openValidation(w: typeof fr | typeof en) {
    await screen.findByRole("heading", { name: w["trace.title"] });
    fireEvent.click(screen.getByRole("button", { name: w["correct.back"] }));
    fireEvent.click(await screen.findByRole("button", { name: w["validation.open"] }));
    await screen.findByRole("heading", { name: "Analyste logistique" });
  }

  it.each(["fr", "en"] as const)("hides the send and send-authorization buttons, in %s", async (language) => {
    const w = words[language];
    renderApp(true, language);
    await openValidation(w);

    expect(screen.getByRole("link", { name: w["validation.open_form"] })).toBeTruthy();
    for (const name of ["validation.authorize_send", "validation.send_authorized", "validation.mark_sent"] as const) {
      expect(screen.queryByRole("button", { name: w[name] })).toBeNull();
    }
  });

  it("still shows the send buttons outside the demo", async () => {
    renderApp(false);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));
    fireEvent.click(await screen.findByRole("button", { name: fr["validation.open"] }));
    await screen.findByRole("heading", { name: "Analyste logistique" });

    expect(screen.getByRole("button", { name: fr["validation.authorize_send"] })).toBeTruthy();
    expect(screen.getByRole("button", { name: fr["validation.mark_sent"] })).toBeTruthy();
  });
});
