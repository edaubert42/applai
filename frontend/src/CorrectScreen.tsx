import { useEffect, useState } from "react";
import { api, type Answer, type CitedFact, type Draft, type Row } from "./api";
import { useSettings } from "./settings";
import { button } from "./styles";

const RESOLVED = ["accepted", "edited"];

/**
 * The Correct screen: the drafted answers of one application, to accept, edit or reject one
 * by one, then the answers gate. Nothing here fills or sends the form.
 */
export default function CorrectScreen({
  row,
  onBack,
  onDone,
}: {
  row: Row;
  onBack: () => void;
  /** The answers gate is closed: back to the dashboard, which loads the new state. */
  onDone: () => void;
}) {
  const { t } = useSettings();
  const id = row.id;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [failed, setFailed] = useState(id === null);
  const [refused, setRefused] = useState(false);

  useEffect(() => {
    if (id === null) return;
    let current = true;
    api.draft(id).then(
      (loaded) => current && setDraft(loaded),
      () => current && setFailed(true),
    );
    return () => {
      current = false;
    };
  }, [id]);

  /** An answer, as the API returned it after a decision: the others are left as they are. */
  function replace(answer: Answer) {
    setRefused(false);
    setDraft((old) => old && { ...old, answers: old.answers.map((a) => (a.id === answer.id ? answer : a)) });
  }

  function validate() {
    if (id === null) return;
    setRefused(false);
    api.answersChecked(id).then(onDone, () => setRefused(true));
  }

  const ready = draft !== null && draft.answers.every((a) => RESOLVED.includes(a.status));

  return (
    <div className="mx-auto max-w-215 space-y-6">
      <button type="button" onClick={onBack} className={button}>
        {t("correct.back")}
      </button>
      {failed && <p role="alert" className="text-minus">{t("correct.error")}</p>}
      {draft && id !== null && (
        <>
          <header className="space-y-1">
            <h2 className="text-xl font-semibold [overflow-wrap:anywhere]">{draft.title}</h2>
            <p className="text-muted [overflow-wrap:anywhere]">{draft.company}</p>
            <a href={row.url} target="_blank" rel="noreferrer" className={`${button} mt-2`}>
              {t("applications.offer")}
            </a>
          </header>
          {draft.draft_error && (
            <p className="text-sm text-minus">{t("correct.draft_error", { error: draft.draft_error })}</p>
          )}
          {draft.answers.length === 0 ? (
            <p className="text-muted">{t("correct.none")}</p>
          ) : (
            <ul className="space-y-4">
              {draft.answers.map((answer) => (
                <li key={answer.id}>
                  <AnswerCard applicationId={id} answer={answer} onChange={replace} />
                </li>
              ))}
            </ul>
          )}
          {refused && <p role="alert" className="text-minus">{t("correct.validate_failed")}</p>}
          <button type="button" disabled={!ready} onClick={validate} className={button}>
            {t("correct.validate")}
          </button>
        </>
      )}
    </div>
  );
}

type Mode = "view" | "edit" | "reject";

function AnswerCard({
  applicationId,
  answer,
  onChange,
}: {
  applicationId: number;
  answer: Answer;
  onChange: (answer: Answer) => void;
}) {
  const { t } = useSettings();
  const [mode, setMode] = useState<Mode>("view");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const shown = answer.edited_text ?? answer.text;
  const titleId = `answer-${answer.id}`;

  function open(next: Mode) {
    setMode(next);
    setFailed(false);
    setReason("");
    setText(shown);
  }

  function send(decision: Parameters<typeof api.decideAnswer>[2]) {
    setBusy(true);
    setFailed(false);
    api.decideAnswer(applicationId, answer.id, decision).then(
      (decided) => {
        setBusy(false);
        setMode("view");
        onChange(decided);
      },
      () => {
        setBusy(false);
        setFailed(true);
      },
    );
  }

  const note = reason.trim();
  const field = "min-h-11 w-full rounded-block border border-outline bg-raised px-4 py-2";
  const pill = "rounded-pill bg-raised px-3 py-1 text-sm";

  return (
    <article aria-labelledby={titleId} className="space-y-3 rounded-block bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id={titleId} className="min-w-0 flex-1 basis-60 font-semibold [overflow-wrap:anywhere]">
          {answer.label}
        </h3>
        <span className={pill}>{t(`correct.status.${answer.status}`)}</span>
      </div>
      {answer.warnings.map((warning) => (
        <p key={warning} role="note" className="rounded-block border border-minus px-3 py-2 text-sm text-minus">
          {t("correct.warning", { warning })}
        </p>
      ))}
      {answer.edited_text !== null ? (
        <p className="whitespace-pre-line [overflow-wrap:anywhere]">{answer.edited_text}</p>
      ) : (
        <p className="[overflow-wrap:anywhere]">
          {answer.sentences.map((sentence, index) => (
            <span key={index}>{index > 0 && " "}{sentence.text}</span>
          ))}
        </p>
      )}
      <Facts sentences={answer.sentences} />

      {mode === "view" && (
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={busy || answer.status === "rejected"}
            onClick={() => send({ decision: "accept" })}
            className={button}
          >
            {t("correct.accept")}
          </button>
          <button type="button" disabled={busy} onClick={() => open("edit")} className={button}>
            {t("correct.edit")}
          </button>
          <button type="button" disabled={busy} onClick={() => open("reject")} className={button}>
            {t("correct.reject")}
          </button>
        </div>
      )}

      {mode === "edit" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(note ? { decision: "edit", text, reason: note } : { decision: "edit", text });
          }}
          className="space-y-3"
        >
          <label className="block space-y-1">
            <span className="text-sm text-muted">{t("correct.edit_text")}</span>
            <textarea value={text} rows={6} onChange={(event) => setText(event.target.value)} className={field} />
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-muted">{t("correct.reason")}</span>
            <input value={reason} onChange={(event) => setReason(event.target.value)} className={field} />
          </label>
          <div className="flex flex-wrap gap-3">
            <button type="submit" disabled={busy || !text.trim()} className={button}>
              {t("correct.save")}
            </button>
            <button type="button" disabled={busy} onClick={() => setMode("view")} className={button}>
              {t("correct.cancel")}
            </button>
          </div>
        </form>
      )}

      {mode === "reject" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            send({ decision: "reject", reason: note });
          }}
          className="space-y-3"
        >
          <label className="block space-y-1">
            <span className="text-sm text-muted">{t("correct.reject_reason")}</span>
            <input value={reason} onChange={(event) => setReason(event.target.value)} className={field} />
          </label>
          <div className="flex flex-wrap gap-3">
            <button type="submit" disabled={busy || !note} className={button}>
              {busy ? t("correct.regenerating") : t("correct.regenerate")}
            </button>
            <button type="button" disabled={busy} onClick={() => setMode("view")} className={button}>
              {t("correct.cancel")}
            </button>
          </div>
        </form>
      )}
      {failed && <p role="alert" className="text-sm text-minus">{t("correct.decision_failed")}</p>}
    </article>
  );
}

/** The facts the answer's sentences cite, each once, with its ID and its text. */
function Facts({ sentences }: { sentences: Answer["sentences"] }) {
  const { t } = useSettings();
  const facts = new Map<string, CitedFact>();
  for (const sentence of sentences) for (const fact of sentence.facts) facts.set(fact.id, fact);
  if (facts.size === 0) return null;
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-muted">{t("correct.facts")}</summary>
      <ul className="mt-2 space-y-1">
        {[...facts.values()].map((fact) => (
          <li key={fact.id} className="flex gap-2">
            <strong>{fact.id}</strong>
            <span className="[overflow-wrap:anywhere]">{fact.text ?? t("correct.fact_missing")}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
