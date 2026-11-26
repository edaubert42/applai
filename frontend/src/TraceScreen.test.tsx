import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ApplicationTrace, GateStep, RunStep } from "./api";
import { aRow, applicationsRoutes, fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

const words = { fr, en };

const triage: RunStep = { kind: "run", at: "2026-11-10T09:00:00Z", agent: "triage", model: "claude-haiku-5-5", duration_ms: 1500, cost_usd: 0.002, outcome: "ok" };
const accepted: GateStep = { kind: "gate", at: "2026-11-10T09:05:00Z", to_state: "accepted", decision: "interested", corrections: 0, reasons: [] };
const timedOut: RunStep = { kind: "run", at: "2026-11-10T09:10:00Z", agent: "drafting", model: "claude-opus-5-5", duration_ms: 60000, cost_usd: null, outcome: "timeout" };
const drafted: RunStep = { kind: "run", at: "2026-11-10T09:12:00Z", agent: "drafting", model: "claude-opus-5-5", duration_ms: 75000, cost_usd: 0.4, outcome: "ok" };
const answers: GateStep = { kind: "gate", at: "2026-11-10T09:30:00Z", to_state: "answers_checked", decision: null, corrections: 2, reasons: [] };

const TRACE: ApplicationTrace = {
  steps: [triage, accepted, timedOut, drafted, answers],
  total_cost_usd: 0.402,
  total_duration_ms: 136500,
};

const text = (node: HTMLElement | undefined) => node?.textContent ?? "";

async function openTrace(trace: ApplicationTrace | Response, language: "fr" | "en" = "fr") {
  const calls = fakeApi({
    ...reviewRoutes(),
    ...applicationsRoutes({ waiting: [aRow({ id: 7, offer_id: 7 })] }),
    "GET /api/applications/7/trace": trace,
  });
  if (language === "en") localStorage.setItem("applai.language", "en");
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: words[language]["nav.applications"] }));
  fireEvent.click(await screen.findByRole("button", { name: words[language]["trace.open"] }));
  return { calls, w: words[language] };
}

describe("the trace of an application", () => {
  it.each(["fr", "en"] as const)("lists its runs and gates in order, agents apart from you, in %s", async (language) => {
    const { calls, w } = await openTrace(TRACE, language);

    await screen.findByRole("heading", { name: w["trace.title"] });
    expect(calls.map((call) => `${call.method} ${call.path}`)).toContain("GET /api/applications/7/trace");
    const steps = screen.getAllByRole("listitem");
    expect(steps).toHaveLength(5);
    const marks = steps.map((step) => (within(step).queryByText(w["trace.mark.you"]) ? "you" : within(step).queryByText(w["trace.mark.agent"]) ? "agent" : "none"));
    expect(marks).toEqual(["agent", "you", "agent", "agent", "you"]);
    expect(text(steps[0])).toContain(w["trace.agent.triage"]);
    expect(text(steps[0])).toContain("Haiku 5.5");
    expect(text(steps[3])).toContain("Opus 5.5");
    expect(text(steps[3])).toContain("1 min 15 s");
    expect(text(steps[3])).toMatch(/0[.,]40/);
    expect(text(steps[1])).toContain(w["trace.gate.accepted"]);
    expect(text(steps[4])).toContain(w["trace.gate.answers_checked"]);
    expect(text(steps[4])).toContain(w["trace.corrections"].replace("{n}", "2"));
  });

  it("shows the outcome of a failed run, then the run of the same stage that follows it", async () => {
    await openTrace(TRACE);

    const [failed, retried] = (await screen.findAllByText(fr["trace.agent.drafting"])).map((name) => name.closest("li") ?? undefined);
    const outcome = fr["trace.failed"].replace("{outcome}", "timeout");
    expect(text(failed)).toContain(outcome);
    expect(text(retried)).not.toContain(outcome);
    expect(failed && retried && failed.compareDocumentPosition(retried) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("ends with the time and the estimated cost of the whole application", async () => {
    await openTrace(TRACE);

    const total = text(await screen.findByTestId("trace-total"));
    expect(total).toContain("2 min 17 s");
    expect(total).toMatch(/0[.,]402/);
  });

  it("shows only the offer's Triage and the acceptance for an application with no run yet", async () => {
    await openTrace({ steps: [triage, accepted], total_cost_usd: 0.002, total_duration_ms: 1500 });

    await screen.findAllByRole("listitem");
    const steps = screen.getAllByRole("listitem");
    expect(steps).toHaveLength(2);
    expect(text(steps[0])).toContain(fr["trace.agent.triage"]);
    expect(text(steps[1])).toContain(fr["trace.gate.accepted"]);
  });

  it("says the trace could not be loaded and leaves the board one click away", async () => {
    await openTrace(new Response("boom", { status: 500 }));

    expect(text(await screen.findByRole("alert"))).toBe(fr["trace.error"]);
    fireEvent.click(screen.getByRole("button", { name: fr["correct.back"] }));
    expect(await screen.findAllByRole("region")).not.toHaveLength(0);
  });

  it("offers no trace for an offer of the old workflow, which has no application", async () => {
    fakeApi({
      ...reviewRoutes(),
      ...applicationsRoutes({ in_progress: [aRow({ id: null, origin: "old_workflow", state: "À faire" })] }),
    });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.applications"] }));
    await screen.findAllByRole("region");

    expect(screen.queryByRole("button", { name: fr["trace.open"] })).toBeNull();
  });
});
