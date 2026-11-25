import { useEffect, useState } from "react";
import { api, type FormReview, type Row } from "./api";
import { useSettings } from "./settings";
import { button } from "./styles";

/** Final validation: review problems, filled fields, attached PDFs, screenshot, and gates. */
export default function FinalValidationScreen({
  row, onBack, onDone,
}: { row: Row; onBack: () => void; onDone: () => void }) {
  const { t, lookup } = useSettings();
  const id = row.id;
  const [review, setReview] = useState<FormReview | null>(null);
  const [failed, setFailed] = useState(id === null);
  const [corrected, setCorrected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [actionFailed, setActionFailed] = useState(false);
  const [confirmDrop, setConfirmDrop] = useState(false);

  useEffect(() => {
    if (id === null) return;
    let current = true;
    api.formReview(id).then(
      (loaded) => current && setReview(loaded),
      () => current && setFailed(true),
    );
    return () => { current = false; };
  }, [id]);

  const toggle = (field: number) =>
    setCorrected((old) => (old.includes(field) ? old.filter((i) => i !== field) : [...old, field]));

  function run(work: () => Promise<void>, after: () => void) {
    if (busy) return;
    setBusy(true);
    setActionFailed(false);
    work().then(
      () => { setBusy(false); after(); },
      () => { setBusy(false); setActionFailed(true); },
    );
  }

  const stateLabel = review && (lookup(`applications.state.${review.state}`) ?? review.state);
  const canDrop = review !== null && review.state !== "sent" && review.state !== "dropped";

  return (
    <div className="mx-auto max-w-215 space-y-6">
      <button type="button" onClick={onBack} className={button}>{t("correct.back")}</button>
      {failed && <p role="alert" className="text-minus">{t("validation.error")}</p>}
      {review && id !== null && (
        <>
          <header className="space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-semibold [overflow-wrap:anywhere]">{review.title}</h2>
              {stateLabel && <span className="rounded-pill bg-raised px-3 py-1 text-sm">{stateLabel}</span>}
            </div>
            <p className="text-muted [overflow-wrap:anywhere]">{review.company}</p>
            <div className="flex flex-wrap gap-3 pt-1">
              <a href={review.url} target="_blank" rel="noreferrer" className={button}>{t("applications.offer")}</a>
              {review.form_url && (
                <a href={review.form_url} target="_blank" rel="noreferrer" className={button}>{t("applications.form")}</a>
              )}
            </div>
          </header>

          {review.review_error && (
            <p role="alert" className="text-sm text-minus">{t("validation.review_error", { error: review.review_error })}</p>
          )}
          {review.send_error && (
            <p role="alert" className="text-sm text-minus">{t("validation.send_error", { error: review.send_error })}</p>
          )}
          {review.cv_warning && (
            <p role="alert" className="text-sm text-minus">{t("validation.cv_warning", { warning: review.cv_warning })}</p>
          )}

          <section aria-labelledby="validation-problems" className="space-y-3">
            <h3 id="validation-problems" className="text-lg font-semibold">{t("validation.problems.title")}</h3>
            {review.problems && review.problems.length > 0 ? (
              <ul className="space-y-2">
                {review.problems.map((p, index) => (
                  <li
                    key={index}
                    className={`flex flex-wrap items-baseline gap-2 rounded-block border px-4 py-3 text-sm ${
                      p.severity === "blocking"
                        ? "border-minus-border bg-minus-bg text-minus"
                        : "border-unknown-border bg-unknown-bg text-unknown"
                    }`}
                  >
                    <span className="font-semibold">{t(`validation.severity.${p.severity}`)}</span>
                    <strong>{p.field}</strong>
                    <span className="[overflow-wrap:anywhere]">{p.message}</span>
                  </li>
                ))}
              </ul>
            ) : review.problems !== null && (
              <p className="rounded-block border border-plus-border bg-plus-bg px-4 py-3 text-sm text-plus">
                {t("validation.problems.none")}
              </p>
            )}
          </section>

          <section aria-labelledby="validation-fields" className="space-y-3">
            <h3 id="validation-fields" className="text-lg font-semibold">{t("validation.fields.title")}</h3>
            <ul className="space-y-3">
              {review.fields.map((field) => {
                const value = Array.isArray(field.value) ? field.value.join(", ") : field.value;
                return (
                  <li key={field.field} className="space-y-2 rounded-block bg-surface p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <strong className="[overflow-wrap:anywhere]">{field.label}</strong>
                      {review.state === "reviewed" && (
                        <label className="flex cursor-pointer items-center gap-2 text-sm text-muted">
                          <input type="checkbox" checked={corrected.includes(field.field)} onChange={() => toggle(field.field)} />
                          <span>{t("validation.field.corrected")}</span>
                          <span className="sr-only">{field.label}</span>
                        </label>
                      )}
                    </div>
                    {field.type === "file" ? (
                      <div className="flex flex-wrap items-center gap-3 text-sm">
                        {field.file ? <span className="[overflow-wrap:anywhere]">{field.file}</span> : <span className="text-minus">{t("validation.field.missing_file")}</span>}
                        {field.slot && (
                          <a href={`/api/applications/${id}/files/${field.slot}`} target="_blank" rel="noreferrer" className={button}>
                            {t("validation.field.see_pdf")}
                          </a>
                        )}
                      </div>
                    ) : value ? (
                      <p className="whitespace-pre-line text-sm [overflow-wrap:anywhere]">{value}</p>
                    ) : (
                      <p className="text-sm text-muted">{field.unfilled ?? t("validation.field.empty")}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>

          {review.has_screenshot && (
            <section aria-labelledby="validation-screenshot" className="space-y-3">
              <h3 id="validation-screenshot" className="text-lg font-semibold">{t("validation.screenshot.title")}</h3>
              <img src={`/api/applications/${id}/screenshot`} alt={t("validation.screenshot.alt")} className="w-full rounded-block border border-border" />
            </section>
          )}

          {actionFailed && <p role="alert" className="text-minus">{t("validation.action_failed")}</p>}

          <div className="flex flex-wrap items-center gap-3">
            {review.state === "reviewed" && (
              <button
                type="button" disabled={busy} className={button}
                onClick={() => {
                  const labels = review.fields.filter((f) => corrected.includes(f.field)).map((f) => f.label);
                  run(() => api.formChecked(id, labels), () => setReview((old) => old && { ...old, state: "form_checked" }));
                }}
              >
                {t("validation.form_checked")}
              </button>
            )}
            {review.state === "form_checked" && (
              <button type="button" disabled={busy} className={button} onClick={() => run(() => api.validateApplication(id), () => setReview((old) => old && { ...old, state: "validated" }))}>
                {t("validation.validate")}
              </button>
            )}
            {review.state === "validated" && (
              <>
                <a href={review.form_url ?? review.url} target="_blank" rel="noreferrer" className={button}>{t("validation.open_form")}</a>
                <button
                  type="button" disabled={busy || review.send_authorized} className={button}
                  onClick={() => run(() => api.authorizeSend(id), () => setReview((old) => old && { ...old, send_authorized: true, send_error: null }))}
                >
                  {t(review.send_authorized ? "validation.send_authorized" : "validation.authorize_send")}
                </button>
                <button type="button" disabled={busy} onClick={() => run(() => api.markSent(id), onDone)} className={button}>{t("validation.mark_sent")}</button>
              </>
            )}
            {canDrop && !confirmDrop && (
              <button type="button" disabled={busy} onClick={() => setConfirmDrop(true)} className={button}>{t("validation.drop")}</button>
            )}
          </div>

          {canDrop && confirmDrop && (
            <div className="flex flex-wrap items-center gap-3 rounded-block border border-minus-border bg-minus-bg p-4">
              <p className="text-sm font-medium text-minus">{t("validation.drop_prompt")}</p>
              <button type="button" disabled={busy} onClick={() => run(() => api.dropApplication(id), onDone)} className={button}>{t("validation.drop_confirm")}</button>
              <button type="button" disabled={busy} onClick={() => setConfirmDrop(false)} className={button}>{t("correct.cancel")}</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
