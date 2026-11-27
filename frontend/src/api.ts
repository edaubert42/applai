/** The API's JSON, as src/applai/review.py and scan.py serve it. */

export type View = "to_review" | "later" | "screened_out";
export const VIEWS: readonly View[] = ["to_review", "later", "screened_out"];

export type Reason =
  | "company"
  | "mission"
  | "experience"
  | "skills"
  | "salary"
  | "location"
  | "expired"
  | "other"
  | "already_applied";
export const REASONS: readonly Reason[] = [
  "company",
  "mission",
  "experience",
  "skills",
  "salary",
  "location",
  "expired",
  "other",
  "already_applied", // alone: the offer goes to the sent applications
];

/** Why the candidate drops an application: an offer's decline reasons, or the offer closed. */
export type DropReason = Exclude<Reason, "already_applied"> | "offer_closed";
// An application's offer is already accepted: its closed offer is offer_closed, not expired.
// An application sent outside ApplAI is recorded by « J'ai postulé », not dropped.
export const DROP_REASONS: readonly DropReason[] = [
  ...REASONS.filter(
    (reason): reason is Exclude<Reason, "already_applied"> => reason !== "expired" && reason !== "already_applied",
  ),
  "offer_closed",
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
  contract_type: "permanent" | "fixed_term" | "internship" | "apprenticeship" | null;
  seen_at: string;
}

/** The stages whose failure the board retries: the end of their endpoint's path. */
export type RetryStage = "draft" | "fill" | "review";

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
  origin: "applai" | "manual" | "old_workflow";
  changed_at: string;
  /** Where the offer's form lives, or why it was not found: the candidate pastes it. */
  form_url: string | null;
  form_url_error: string | null;
  fill_error: string | null;
  draft_error: string | null;
  review_error: string | null;
  /** The agent running on it now (its name), or null. */
  running: string | null;
  /** Why the candidate dropped it; empty for any other application. */
  reasons: DropReason[];
  /** Whether the filled form's tab is open; null: unknown, or no tab to wait for. */
  tab_open: boolean | null;
}

export interface BrowserStatus {
  connected: boolean;
  last_asked_at: string | null;
}

export interface FormUrl {
  form_url: string;
  form_url_error: null;
}

/** The answer to adding an offer by its URL: added is false when the URL was already known. */
export interface OfferAdded {
  id: number;
  url: string;
  state: string;
  added: boolean;
}

export type Applications = Record<Group, Row[]>;

/** GET /api/applications/{id}/trace, as src/applai/trace.py serves it. */
export interface RunStep {
  kind: "run";
  at: string;
  agent: string;
  model: string;
  duration_ms: number;
  cost_usd: number | null; // a failed run has none
  outcome: string; // "ok", or the kind of the error
}

export interface GateStep {
  kind: "gate";
  at: string;
  to_state: string;
  decision: string | null;
  corrections: number;
  reasons: string[];
  rejections: Rejection[]; // the answers gate's
}

/** An answer the candidate rejected, or a letter asked shorter (reason "short"). */
export interface Rejection {
  answer_id: number | null; // none, as text, for a label older than this field
  reason: string | null;
  text: string | null; // the text turned down
}

export interface ApplicationTrace {
  steps: (RunStep | GateStep)[]; // in time order
  total_cost_usd: number;
  total_duration_ms: number; // the agents' time
}

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

/** One change CV tailoring kept on the CV: a reorder, a reword or a drop. */
export interface CvChange {
  id: number;
  op: "reorder" | "reword" | "drop";
  path: string;
  old_text: string;
  new_text: string | null; // as drafted; null for a drop
  edited_text: string | null;
  old_items: string[] | null; // a reorder's list, before and after
  new_items: string[] | null;
  facts: CitedFact[];
  status: AnswerStatus;
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
  cv_headline: string | null; // null: the CV source's
  cv_photo: "with" | "without" | "unknown" | null; // null: no CV tailoring
  cv_photo_reason: string | null;
  cv_changes: CvChange[];
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
  send_authorized: boolean; send_error: string | null; cv_warning: string | null;
  fill_error: string | null; fill_again: boolean;
}

export interface ScanStatus {
  running: boolean;
  stopping: boolean;
  active_service: string | null;
  services_used: string[];
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
  /** The LinkedIn offers the running scan went through, out of the new ones; null when no scan reads LinkedIn. */
  linkedin_progress: { read: number; total: number } | null;
  /** The offers created in the last 24 hours, by source; the ones added by hand under "manual". */
  last_24h: Record<string, number>;
}

export type ServiceState = "green" | "orange" | "red";

/** One row of /api/services; the state and the hint are computed by the API (src/applai/services.py). */
export interface Service {
  id: string;
  state: ServiceState;
  last_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  /** The code of what to do (a settings file error comes as its own sentence); null when the state is green. */
  hint: string | null;
  /** The model of an agent row. */
  model?: string;
}

export const CATEGORIES = ["sites", "agents", "mailbox", "applai"] as const;
export type Category = (typeof CATEGORIES)[number];
export type Services = Record<Category, Service[]>;

/** A request the API answered with an error status; a request that never got an answer throws what fetch throws. */
export class ApiError extends Error {
  constructor(
    method: string,
    path: string,
    readonly status: number,
  ) {
    super(`${method} ${path}: ${status}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(method, path, response.status);
  return (await response.json()) as T;
}

/** A request whose answer has no body (204). */
async function requestEmpty(method: string, path: string, body?: unknown): Promise<void> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(method, path, response.status);
}

/** GET /api/health: demo is true when ApplAI runs as the public demo (applai demo). */
export interface Health {
  status: string;
  database_revision: string | null;
  demo: boolean;
}

export const api = {
  health: () => request<Health>("GET", "/api/health"),
  cards: (view: View) => request<Card[]>("GET", `/api/review?view=${view}`),
  decide: (id: number, decision: Decision) =>
    request<unknown>("POST", `/api/offers/${id}/decision`, decision),
  addOffer: (url: string) => request<OfferAdded>("POST", "/api/offers", { url }),
  reread: (id: number) => request<unknown>("POST", `/api/offers/${id}/reread`),
  applications: () => request<Applications>("GET", "/api/applications"),
  browserStatus: () => request<BrowserStatus>("GET", "/api/browser/status"),
  pasteFormUrl: (id: number, url: string) =>
    request<FormUrl>("PUT", `/api/applications/${id}/form-url`, { url }),
  trace: (id: number) => request<ApplicationTrace>("GET", `/api/applications/${id}/trace`),
  draft: (id: number) => request<Draft>("GET", `/api/applications/${id}/draft`),
  decideAnswer: (id: number, answer: number, decision: AnswerDecision) =>
    request<Answer>("POST", `/api/applications/${id}/draft/${answer}`, decision),
  shortCoverLetter: (id: number, answer: number) =>
    request<Answer>("POST", `/api/applications/${id}/draft/${answer}/short`),
  decideCvChange: (id: number, change: number, decision: AnswerDecision) =>
    request<CvChange>("POST", `/api/applications/${id}/cv/changes/${change}`, decision),
  editCvHeadline: (id: number, text: string) =>
    request<Draft>("POST", `/api/applications/${id}/cv/headline`, { text }),
  switchCvPhoto: (id: number, photo: "with" | "without") =>
    request<Draft>("POST", `/api/applications/${id}/cv/photo`, { photo }),
  answersChecked: (id: number) => requestEmpty("POST", `/api/applications/${id}/answers-checked`),
  formReview: (id: number) => request<FormReview>("GET", `/api/applications/${id}/review`),
  formChecked: (id: number, corrected_fields: string[], note?: string) =>
    requestEmpty("POST", `/api/applications/${id}/form-checked`, note ? { corrected_fields, note } : { corrected_fields }),
  validateApplication: (id: number) => requestEmpty("POST", `/api/applications/${id}/validate`),
  authorizeSend: (id: number) => requestEmpty("POST", `/api/applications/${id}/authorize-send`),
  fillAgain: (id: number) => requestEmpty("POST", `/api/applications/${id}/fill-again`),
  markSent: (id: number) => requestEmpty("POST", `/api/applications/${id}/sent`),
  /** Record a send the candidate already made outside ApplAI: ApplAI sends nothing. */
  markSentOutside: (id: number) => requestEmpty("POST", `/api/applications/${id}/sent-outside`),
  dropApplication: (id: number, reasons: DropReason[], note?: string) =>
    requestEmpty("POST", `/api/applications/${id}/drop`, note ? { reasons, note } : { reasons }),
  /** Start a failed stage again: Drafting, Filling or Review runs in the background (202). */
  retryStage: (id: number, stage: RetryStage) =>
    stage === "draft"
      ? requestEmpty("POST", `/api/applications/${id}/draft`)
      : stage === "fill"
        ? requestEmpty("POST", `/api/applications/${id}/fill`)
        : requestEmpty("POST", `/api/applications/${id}/review`),
  scanStatus: () => request<ScanStatus>("GET", "/api/scan"),
  services: () => request<Services>("GET", "/api/services"),
  startScan: () => request<unknown>("POST", "/api/scan"),
  stopScan: () => requestEmpty("POST", "/api/scan/stop"),
};
