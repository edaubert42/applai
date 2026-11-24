/** The API's JSON, as src/applai/review.py and scan.py serve it. */

export type View = "to_review" | "later" | "screened_out";
export const VIEWS: readonly View[] = ["to_review", "later", "screened_out"];

export type Reason = "company" | "mission" | "experience" | "skills" | "salary" | "location" | "other";
export const REASONS: readonly Reason[] = [
  "company",
  "mission",
  "experience",
  "skills",
  "salary",
  "location",
  "other",
];

export type Decision =
  | { decision: "interested" | "later" }
  | { decision: "not_interested"; reasons: Reason[]; note?: string };

export interface Point {
  text: string;
  verdict: "+" | "−" | "?";
}

export interface Card {
  id: number;
  url: string;
  title: string;
  company: string;
  logo: string | null;
  source: string | null;
  state: string;
  score: number | null;
  tags: Record<string, Point> | null;
  about: string | null;
  mission: string[] | null;
  pros: string[] | null;
  cons: Point[] | null;
  rule: string | null;
  full_text_read: boolean; // false: described from its job alert only
  can_reread: boolean; // the button "Relire l'offre" shows
  rereading: boolean; // the extension was asked and has not answered
  reread_error: string | null; // one line: why the last read again failed
}

export const GROUPS = ["in_progress", "waiting", "sent", "dropped"] as const;
export type Group = (typeof GROUPS)[number];

/** One application of the dashboard, or an old-workflow offer, which has no application. */
export interface Row {
  id: number | null;
  offer_id: number;
  url: string;
  title: string;
  company: string;
  /** The application's state, or the Notion status as written for an old-workflow offer. */
  state: string | null;
  origin: "applai" | "old_workflow";
  changed_at: string;
  /** Where the offer's form lives, or why it was not found: the candidate pastes it. */
  form_url: string | null;
  form_url_error: string | null;
}

export interface FormUrl {
  form_url: string;
  form_url_error: null;
}

export type Applications = Record<Group, Row[]>;

export interface ScanStatus {
  running: boolean;
  last_scan: {
    started_at: string;
    finished_at: string | null;
    by_hand: boolean;
    new_offers: number;
    errors: string[];
  } | null;
  next_slot: string | null;
  manual_scan_in_s: number | null;
  linkedin_reads_today: number;
  linkedin_reads_per_day: number;
}

export type ServiceState = "green" | "orange" | "red";

/** One row of /api/services; the state and the hint are computed by the API (src/applai/services.py). */
export interface Service {
  id: string;
  state: ServiceState;
  last_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  /** The sentence that says what to do; null when the state is green. */
  hint: string | null;
}

export const CATEGORIES = ["sites", "agents", "mailbox", "applai"] as const;
export type Category = (typeof CATEGORIES)[number];
export type Services = Record<Category, Service[]>;

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status}`);
  return (await response.json()) as T;
}

export const api = {
  cards: (view: View) => request<Card[]>("GET", `/api/review?view=${view}`),
  decide: (id: number, decision: Decision) =>
    request<unknown>("POST", `/api/offers/${id}/decision`, decision),
  reread: (id: number) => request<unknown>("POST", `/api/offers/${id}/reread`),
  applications: () => request<Applications>("GET", "/api/applications"),
  pasteFormUrl: (id: number, url: string) =>
    request<FormUrl>("PUT", `/api/applications/${id}/form-url`, { url }),
  scanStatus: () => request<ScanStatus>("GET", "/api/scan"),
  services: () => request<Services>("GET", "/api/services"),
  startScan: () => request<unknown>("POST", "/api/scan"),
};
