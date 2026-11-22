import { vi } from "vitest";
import type { Applications, Card, Row, ScanStatus } from "./api";

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Answer = unknown;

/**
 * Fakes the network, and only it: fetch answers by "METHOD /path?query", as the API would.
 * A route that is not listed answers 404. Returns every call made, in order.
 */
export function fakeApi(routes: Record<string, Answer | ((call: Call) => Answer)>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const call: Call = {
      method: init?.method ?? "GET",
      path: url.pathname,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const route = routes[`${call.method} ${url.pathname}${url.search}`];
    if (route === undefined) return new Response("not found", { status: 404 });
    const answer = typeof route === "function" ? route(call) : route;
    return answer instanceof Response ? answer : Response.json(answer);
  });
  return calls;
}

export const REVIEW = (view: string) => `GET /api/review?view=${view}`;
export const DECISION = (id: number) => `POST /api/offers/${id}/decision`;

/** A fictional offer's card, a Triage result included. */
export function aCard(overrides: Partial<Card> = {}): Card {
  return {
    id: 1,
    url: "https://jobs.example.org/acme/ingenieur-logiciel",
    title: "Ingénieur logiciel",
    company: "Acme Robotics",
    logo: "acme-robotics.png",
    source: "alert_wttj",
    state: "triaged",
    score: 87,
    tags: {
      location: { text: "Paris 11e", verdict: "+" },
      work_mode: { text: "Hybride", verdict: "+" },
      contract: { text: "CDI", verdict: "+" },
      experience: { text: "3 ans et plus", verdict: "?" },
      salary: { text: "Non indiqué", verdict: "−" },
    },
    about: "Acme Robotics fabrique des robots de cuisine.",
    mission: ["Écrire le service de commandes", "Relire le code de l’équipe"],
    pros: ["Le poste est en Python", "Télétravail deux jours par semaine"],
    cons: [
      { text: "Le salaire n’est pas indiqué", verdict: "−" },
      { text: "L’équipe compte trois personnes", verdict: "?" },
    ],
    rule: null,
    ...overrides,
  };
}

/** A fictional application's row of the Applications dashboard. */
export function aRow(overrides: Partial<Row> = {}): Row {
  return {
    id: 1,
    offer_id: 1,
    url: "https://jobs.example.org/brindille-logistique/chef-de-quai",
    title: "Chef de quai",
    company: "Brindille Logistique",
    state: "drafted",
    origin: "applai",
    changed_at: "2026-11-20T09:30:00Z",
    ...overrides,
  };
}

/** The routes of the Applications dashboard with these rows in each group. */
export function applicationsRoutes(groups: Partial<Applications> = {}) {
  const board: Applications = { in_progress: [], waiting: [], sent: [], dropped: [], ...groups };
  return { "GET /api/applications": board };
}

/** The scan status after a clean scan that ended long ago. */
export function aScan(overrides: Partial<ScanStatus> = {}): ScanStatus {
  return {
    running: false,
    last_scan: {
      started_at: "2026-11-22T07:00:00+01:00",
      finished_at: "2026-11-22T07:02:00+01:00",
      by_hand: false,
      new_offers: 4,
      errors: [],
    },
    next_slot: "2026-11-22T18:00:00+01:00",
    manual_scan_in_s: null,
    linkedin_reads_today: 3,
    linkedin_reads_per_day: 20,
    ...overrides,
  };
}

/** The routes of a screen with these cards in each view and this scan status. */
export function reviewRoutes(
  views: { to_review?: Card[]; later?: Card[]; screened_out?: Card[] } = {},
  scan: ScanStatus = aScan(),
) {
  return {
    [REVIEW("to_review")]: views.to_review ?? [],
    [REVIEW("later")]: views.later ?? [],
    [REVIEW("screened_out")]: views.screened_out ?? [],
    "GET /api/scan": scan,
  };
}
