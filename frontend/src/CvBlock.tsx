import { useState } from "react";
import { api, type CvChange, type Draft } from "./api";
import { useSettings } from "./settings";
import { button } from "./styles";

const field = "min-h-11 w-full rounded-block border border-outline bg-raised px-4 py-2";
const pill = "rounded-pill bg-raised px-3 py-1 text-sm";

/**
 * The CV block of the Correct screen: the title line, one row per CV change (accepted, edited or
 * rejected like an answer) and the photo choice. Each call returns what changed; the screen's
 * draft is updated with it.
 */
export default function CvBlock({ draft, onDraft }: { draft: Draft; onDraft: (draft: Draft) => void }) {
  const { t } = useSettings();
  const id = draft.application_id;
  const [editing, setEditing] = useState(false);
  const [headline, setHeadline] = useState("");
  const [failed, setFailed] = useState(false);
  const photo = draft.cv_photo === "with" ? "without" : "with";

  /** Run a call; its failure shows the block's one error, its success is shown as returned. */
  function run<T>(call: Promise<T>, done: (result: T) => void) {
    setFailed(false);
    call.then(done, () => setFailed(true));
  }

  function replace(changed: CvChange) {
    onDraft({ ...draft, cv_changes: draft.cv_changes.map((c) => (c.id === changed.id ? changed : c)) });
  }

  return (
    <section aria-labelledby="cv-title" className="space-y-4 rounded-block bg-surface p-4">
      <h3 id="cv-title" className="font-semibold">{t("cv.title")}</h3>
      {editing ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            run(api.editCvHeadline(id, headline.trim()), (next) => {
              setEditing(false);
              onDraft(next);
            });
          }}
          className="space-y-3"
        >
          <label className="block space-y-1">
            <span className="text-sm text-muted">{t("cv.headline")}</span>
            <input value={headline} onChange={(event) => setHeadline(event.target.value)} className={field} />
          </label>
          <div className="flex flex-wrap gap-3">
            <button type="submit" disabled={!headline.trim()} className={button}>{t("correct.save")}</button>
            <button type="button" onClick={() => setEditing(false)} className={button}>{t("correct.cancel")}</button>
          </div>
        </form>
      ) : (
        <p className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">{t("cv.headline")}</span>
          <strong className="[overflow-wrap:anywhere]">{draft.cv_headline ?? t("cv.headline_source")}</strong>
          <button
            type="button"
            onClick={() => {
              setHeadline(draft.cv_headline ?? "");
              setEditing(true);
            }}
            className={button}
          >
            {t("cv.headline_edit")}
          </button>
        </p>
      )}

      <ul className="space-y-3">
        {draft.cv_changes.map((change) => (
          <li key={change.id}>
            <ChangeRow applicationId={id} change={change} onChange={replace} />
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-3">
        <span className={pill}>{t(`cv.photo.${draft.cv_photo ?? "unknown"}`)}</span>
        {draft.cv_photo_reason && <span className="text-sm text-muted [overflow-wrap:anywhere]">{draft.cv_photo_reason}</span>}
        <button
          type="button"
          onClick={() => run(api.switchCvPhoto(id, photo), onDraft)}
          className={button}
        >
          {t(`cv.photo_${photo}`)}
        </button>
      </div>
      {failed && <p role="alert" className="text-sm text-minus">{t("correct.decision_failed")}</p>}
    </section>
  );
}

type Mode = "view" | "edit" | "reject";

function ChangeRow({
  applicationId,
  change,
  onChange,
}: {
  applicationId: number;
  change: CvChange;
  onChange: (change: CvChange) => void;
}) {
  const { t } = useSettings();
  const [mode, setMode] = useState<Mode>("view");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const shown = change.edited_text ?? change.new_text ?? "";
  const note = reason.trim();

  function open(next: Mode) {
    setMode(next);
    setFailed(false);
    setReason("");
    setText(shown);
  }

  function send(decision: Parameters<typeof api.decideCvChange>[2]) {
    setBusy(true);
    setFailed(false);
    api.decideCvChange(applicationId, change.id, decision).then(
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

  return (
    <div className="space-y-2 rounded-block border border-outline p-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1 basis-60 text-sm text-muted [overflow-wrap:anywhere]">
          {t(`cv.op.${change.op}`)}
        </span>
        <span className={pill}>{t(`correct.status.${change.status}`)}</span>
      </div>
      {change.op === "reorder" && (
        <ol className="list-inside list-decimal">
          {(change.new_items ?? []).map((item) => (
            <li key={item} className="[overflow-wrap:anywhere]">{item}</li>
          ))}
        </ol>
      )}
      {change.op === "drop" && <p className="line-through [overflow-wrap:anywhere]">{change.old_text}</p>}
      {change.op === "reword" && (
        <>
          <p className="text-muted line-through [overflow-wrap:anywhere]">{change.old_text}</p>
          <p className="[overflow-wrap:anywhere]">{shown}</p>
          <ul className="text-sm text-muted">
            {change.facts.map((fact) => (
              <li key={fact.id}>
                <strong>{fact.id}</strong> {fact.text ?? t("correct.fact_missing")}
              </li>
            ))}
          </ul>
        </>
      )}

      {mode === "view" && (
        <div className="flex flex-wrap gap-3">
          <button type="button" disabled={busy} onClick={() => send({ decision: "accept" })} className={button}>
            {t("correct.accept")}
          </button>
          {change.op === "reword" && (
            <button type="button" disabled={busy} onClick={() => open("edit")} className={button}>
              {t("correct.edit")}
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => open("reject")} className={button}>
            {t("cv.reject")}
          </button>
        </div>
      )}

      {mode !== "view" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (mode === "reject") send({ decision: "reject", reason: note });
            else send(note ? { decision: "edit", text, reason: note } : { decision: "edit", text });
          }}
          className="space-y-3"
        >
          {mode === "edit" && (
            <label className="block space-y-1">
              <span className="text-sm text-muted">{t("correct.edit_text")}</span>
              <textarea value={text} rows={3} onChange={(event) => setText(event.target.value)} className={field} />
            </label>
          )}
          <label className="block space-y-1">
            <span className="text-sm text-muted">{t(mode === "reject" ? "correct.reject_reason" : "correct.reason")}</span>
            <input value={reason} onChange={(event) => setReason(event.target.value)} className={field} />
          </label>
          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              disabled={busy || (mode === "reject" ? !note : !text.trim())}
              className={button}
            >
              {mode === "reject" ? t("cv.reject_confirm") : t("correct.save")}
            </button>
            <button type="button" disabled={busy} onClick={() => setMode("view")} className={button}>
              {t("correct.cancel")}
            </button>
          </div>
        </form>
      )}
      {failed && <p role="alert" className="text-sm text-minus">{t("correct.decision_failed")}</p>}
    </div>
  );
}
