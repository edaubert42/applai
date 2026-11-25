import { vi } from "vitest";
import type {
  Answer, Applications, BrowserStatus, Card, CvChange, Draft, FormReview, ReviewedField, Row, ScanStatus, Service, Services,
} from "./api";

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Reply = unknown;

/**
 * Fakes the network, and only it: fetch answers by "METHOD /path?query", as the API would.
 * A route that is not listed answers 404. Returns every call made, in order.
 */
export function fakeApi(routes: Record<string, Reply | ((call: Call) => Reply)>): Call[] {
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
    const answer = await (typeof route === "function" ? route(call) : route);
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
    full_text_read: true,
    can_reread: false,
    rereading: false,
    reread_error: null,
    contract_type: "permanent",
    seen_at: "2026-11-22T09:00:00Z",
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
    form_url: null,
    form_url_error: null,
    fill_error: null,
    reasons: [],
    ...overrides,
  };
}

/** The routes of the Applications dashboard with these rows in each group. */
export function applicationsRoutes(groups: Partial<Applications> = {}) {
  const board: Applications = { in_progress: [], waiting: [], sent: [], dropped: [], ...groups };
  const status: BrowserStatus = { connected: true, last_asked_at: "2026-11-22T07:02:00Z" };
  return {
    "GET /api/applications": board,
    "GET /api/browser/status": status,
  };
}

/** The scan status after a clean scan that ended long ago. */
export function aScan(overrides: Partial<ScanStatus> = {}): ScanStatus {
  return {
    running: false,
    stopping: false,
    active_service: null,
    services_used: [],
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
    linkedin_progress: null,
    last_24h: {},
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

/** A fictional service of the Settings page: green unless overridden. */
export function aService(overrides: Partial<Service> = {}): Service {
  return {
    id: "alert_wttj",
    state: "green",
    last_at: "2026-11-22T07:02:00Z",
    last_error: null,
    last_error_at: null,
    hint: null,
    ...overrides,
  };
}

/** The route of the Settings page with these services in each category. */
export function servicesRoutes(services: Partial<Services> = {}) {
  const status: Services = { sites: [], agents: [], mailbox: [], applai: [], ...services };
  return { "GET /api/services": status };
}

/** A fictional drafted answer, still pending. */
export function anAnswer(overrides: Partial<Answer> = {}): Answer {
  return {
    id: 1,
    kind: "question",
    field_index: 0,
    label: "Pourquoi ce poste ?",
    sentences: [
      {
        text: "J’ai organisé les quais d’un entrepôt de 40 personnes.",
        facts: [{ id: "#F1", text: "Responsable de quai chez Silo Nord, équipe de 40 personnes." }],
      },
      {
        text: "J’ai réduit les retards de 20 %.",
        facts: [{ id: "#F2", text: "Retards de livraison réduits de 20 % en 2024." }],
      },
    ],
    text: "J’ai organisé les quais d’un entrepôt de 40 personnes. J’ai réduit les retards de 20 %.",
    edited_text: null,
    status: "pending",
    warnings: [],
    ...overrides,
  };
}

/** A fictional application's draft. */
export function aDraft(answers: Answer[] = [anAnswer()], overrides: Partial<Draft> = {}): Draft {
  return {
    application_id: 1,
    offer_id: 1,
    title: "Chef de quai",
    company: "Brindille Logistique",
    state: "drafted",
    draft_error: null,
    answers,
    cv_headline: null,
    cv_photo: null,
    cv_photo_reason: null,
    cv_changes: [],
    resolved: answers.every((a) => a.status === "accepted" || a.status === "edited"),
    ...overrides,
  };
}

/** A fictional CV change: a reword of one bullet, still pending. */
export function aCvChange(overrides: Partial<CvChange> = {}): CvChange {
  return {
    id: 10,
    op: "reword",
    path: "experience[0].bullets[1]",
    old_text: "Suivi des retards de livraison.",
    new_text: "Réduit les retards de livraison de 20 %.",
    edited_text: null,
    old_items: null,
    new_items: null,
    facts: [{ id: "F2", text: "Retards de livraison réduits de 20 % en 2024." }],
    status: "pending",
    ...overrides,
  };
}

/** A fictional filled field reported from Chrome. */
export function aReviewedField(overrides: Partial<ReviewedField> = {}): ReviewedField {
  return {
    field: 0, label: "E-mail", type: "email", required: true,
    value: "camille@example.org", file: null, unfilled: null, slot: null, ...overrides,
  };
}

/** A fictional form review ready for final validation. */
export function aFormReview(overrides: Partial<FormReview> = {}): FormReview {
  return {
    application_id: 1, offer_id: 1, title: "Chef de quai", company: "Brindille Logistique",
    url: "https://jobs.example.org/brindille-logistique/chef-de-quai",
    form_url: "https://forms.example.org/brindille-logistique/apply",
    state: "reviewed",
    fields: [
      aReviewedField(),
      aReviewedField({ field: 1, label: "Téléphone", type: "tel", value: "06 00 00 00 00" }),
      aReviewedField({ field: 2, label: "CV", type: "file", value: null, file: "cv-camille.pdf", slot: "cv" }),
    ],
    problems: [], review_error: null, has_screenshot: true,
    send_authorized: false, send_error: null, cv_warning: null, ...overrides,
  };
}
