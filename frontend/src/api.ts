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
  fill_error: string | null;
}

export interface BrowserStatus {
  connected: boolean;
  last_asked_at: string | null;
}

export interface FormUrl {
  form_url: string;
  form_url_error: null;
}

export type Applications = Record<Group, Row[]>;

export type AnswerStatus = "pending" | "accepted" | "edited" | "rejected";

export interface CitedFact {
  id: string;
  text: string | null; // null: no longer in the profile
}

export interface Answer {
  id: number;
  kind: "question" | "cover_letter";
  field_index: number;
  label: string;
  sentences: { text: string; facts: CitedFact[] }[];
  text: string; // as drafted
  edited_text: string | null;
  status: AnswerStatus;
  warnings: string[]; // the judge's, shown without blocking
}

/** GET /api/applications/{id}/draft, as src/applai/correct.py serves it. */
export interface Draft {
  application_id: number;
  offer_id: number;
  title: string;
  company: string;
  state: string;
  draft_error: string | null;
  answers: Answer[];
  resolved: boolean;
}

export type AnswerDecision =
  | { decision: "accept" }
  | { decision: "edit"; text: string; reason?: string }
  | { decision: "reject"; reason: string };

export type FileSlot = "cv" | "cover_letter";

export interface ReviewedField {
  field: number; label: string; type: string; required: boolean;
  value: string | string[] | null; file: string | null; unfilled: string | null; slot: FileSlot | null;
}

export interface ReviewProblem {
  severity: "blocking" | "warning"; field: string; message: string;
}

/** GET /api/applications/{id}/review, as src/applai/review_form.py serves it. */
export interface FormReview {
  application_id: number; offer_id: number; title: string; company: string;
  url: string; form_url: string | null; state: string; fields: ReviewedField[];
  problems: ReviewProblem[] | null; review_error: string | null; has_screenshot: boolean;
  send_authorized: boolean; send_error: string | null;
}

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

/** A request whose answer has no body (204). */
async function requestEmpty(method: string, path: string, body?: unknown): Promise<void> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status}`);
}

export const api = {
  cards: (view: View) => request<Card[]>("GET", `/api/review?view=${view}`),
  decide: (id: number, decision: Decision) =>
    request<unknown>("POST", `/api/offers/${id}/decision`, decision),
  reread: (id: number) => request<unknown>("POST", `/api/offers/${id}/reread`),
  applications: () => request<Applications>("GET", "/api/applications"),
  browserStatus: () => request<BrowserStatus>("GET", "/api/browser/status"),
  pasteFormUrl: (id: number, url: string) =>
    request<FormUrl>("PUT", `/api/applications/${id}/form-url`, { url }),
  draft: (id: number) => request<Draft>("GET", `/api/applications/${id}/draft`),
  decideAnswer: (id: number, answer: number, decision: AnswerDecision) =>
    request<Answer>("POST", `/api/applications/${id}/draft/${answer}`, decision),
  answersChecked: (id: number) => requestEmpty("POST", `/api/applications/${id}/answers-checked`),
  formReview: (id: number) => request<FormReview>("GET", `/api/applications/${id}/review`),
  formChecked: (id: number, corrected_fields: string[], note?: string) =>
    requestEmpty("POST", `/api/applications/${id}/form-checked`, note ? { corrected_fields, note } : { corrected_fields }),
  validateApplication: (id: number) => requestEmpty("POST", `/api/applications/${id}/validate`),
  authorizeSend: (id: number) => requestEmpty("POST", `/api/applications/${id}/authorize-send`),
  markSent: (id: number) => requestEmpty("POST", `/api/applications/${id}/sent`),
  dropApplication: (id: number, note?: string) =>
    requestEmpty("POST", `/api/applications/${id}/drop`, note ? { note } : undefined),
  scanStatus: () => request<ScanStatus>("GET", "/api/scan"),
  services: () => request<Services>("GET", "/api/services"),
  startScan: () => request<unknown>("POST", "/api/scan"),
};
