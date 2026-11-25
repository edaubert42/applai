import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, GROUPS, api, type Applications, type Row } from "./api";
import CorrectScreen from "./CorrectScreen";
import FinalValidationScreen from "./FinalValidationScreen";
import { Logo } from "./ReviewCard";
import { serverMessage } from "./server-message";
import { useSettings } from "./settings";
import { button } from "./styles";

const CHECKABLE = ["reviewed", "form_checked", "validated"];

/** The Applications dashboard, or the Correct screen of the application being corrected. */
export default function ApplicationsScreen() {
  const [correcting, setCorrecting] = useState<Row | null>(null);
  const [validating, setValidating] = useState<Row | null>(null);
  // The board loads again each time it comes back, so a closed gate shows in the state.
  if (correcting) {
    const back = () => setCorrecting(null);
    return <CorrectScreen row={correcting} onBack={back} onDone={back} />;
  }
  if (validating) {
    const back = () => setValidating(null);
    return <FinalValidationScreen row={validating} onBack={back} onDone={back} />;
  }
  return <Board onCorrect={setCorrecting} onValidate={setValidating} />;
}

/** Every application in four groups, the latest change first. */
function Board({ onCorrect, onValidate }: { onCorrect: (row: Row) => void; onValidate: (row: Row) => void }) {
  const { t } = useSettings();
  const [groups, setGroups] = useState<Applications | null>(null);
  const [failed, setFailed] = useState(false);
  const [extensionConnected, setExtensionConnected] = useState(true);
  const [loads, setLoads] = useState(0); // a row found stale asks for the board again
  const reload = useCallback(() => setLoads((count) => count + 1), []);

  useEffect(() => {
    let current = true;
    api.applications().then(
      (loaded) => current && setGroups(loaded),
      () => current && setFailed(true),
    );
    api.browserStatus().then(
      (status) => current && setExtensionConnected(status.connected),
      () => current && setExtensionConnected(false),
    );
    return () => {
      current = false;
    };
  }, [loads]);

  return (
    <div className="mx-auto max-w-215 space-y-7">
      {failed && <p role="alert" className="text-minus">{t("applications.error")}</p>}
      {groups &&
        GROUPS.map((group) => (
          <section key={group} aria-labelledby={`group-${group}`} className="space-y-3">
            <h2 id={`group-${group}`} className="text-xl font-semibold">
              {t(`applications.group.${group}`, { n: groups[group].length })}
            </h2>
            {groups[group].length === 0 ? (
              <p className="text-muted">{t("applications.none")}</p>
            ) : (
              <ul className="space-y-3">
                {groups[group].map((row) => (
                  <ApplicationRow
                    key={row.offer_id}
                    row={row}
                    extensionConnected={extensionConnected}
                    onCorrect={onCorrect}
                    onValidate={onValidate}
                    onStale={reload}
                  />
                ))}
              </ul>
            )}
          </section>
        ))}
    </div>
  );
}

function ApplicationRow({
  row,
  extensionConnected,
  onCorrect,
  onValidate,
  onStale,
}: {
  row: Row;
  extensionConnected: boolean;
  onCorrect: (row: Row) => void;
  onValidate: (row: Row) => void;
  onStale: () => void;
}) {
  const { t, lookup, formatTime } = useSettings();
  const old = row.origin === "old_workflow";
  // An application's state is translated; a Notion status stays as written.
  const state = old ? row.state : row.state && (lookup(`applications.state.${row.state}`) ?? row.state);
  const pill = "rounded-pill bg-raised px-3 py-1 text-sm";
  // The form's URL the finder recorded, or the one the candidate pasted since.
  const [form, setForm] = useState({ url: row.form_url, error: row.form_url_error });
  const [pasted, setPasted] = useState("");
  // What the last paste failed on: the end of its message's key.
  const [failed, setFailed] = useState<"failed" | "past" | "unreachable" | "error" | null>(null);
  const id = row.id;
  const canPaste = id !== null && row.state === "created" && !form.url;

  function paste(event: FormEvent, id: number) {
    event.preventDefault();
    setFailed(null);
    api.pasteFormUrl(id, pasted.trim()).then(
      (saved) => setForm({ url: saved.form_url, error: null }),
      (error: unknown) => {
        const status = error instanceof ApiError ? error.status : null;
        if (status === 409) onStale(); // the row moved on: show where it stands
        setFailed(
          status === null
            ? "unreachable" // fetch rejected: no answer
            : status === 409
              ? "past"
              : status === 422
                ? "failed" // the scheme
                : "error",
        );
      },
    );
  }

  return (
    <li className="flex flex-wrap items-center gap-4 rounded-block bg-surface p-4">
      <Logo card={{ company: row.company, logo: null }} className="size-10 shrink-0 rounded-xl" />
      <div className="min-w-0 flex-1 basis-60">
        <h3 className="font-semibold [overflow-wrap:anywhere]">{row.title}</h3>
        <p className="text-sm text-muted [overflow-wrap:anywhere]">{row.company}</p>
      </div>
      {old && <span className={`${pill} text-muted`}>{t("applications.origin.old_workflow")}</span>}
      {state && <span className={pill}>{state}</span>}
      {row.tab_open !== null && (
        <span className={`${pill} ${row.tab_open ? "text-plus" : "text-muted"}`}>
          {t(row.tab_open ? "applications.tab.open" : "applications.tab.closed")}
        </span>
      )}
      <time dateTime={row.changed_at} className="text-sm text-muted">
        {formatTime(row.changed_at)}
      </time>
      <a href={row.url} target="_blank" rel="noreferrer" className={button}>
        {t("applications.offer")}
      </a>
      {form.url && (
        <a href={form.url} target="_blank" rel="noreferrer" className={button}>
          {t("applications.form")}
        </a>
      )}
      {row.id !== null && row.state === "drafted" && (
        <button type="button" onClick={() => onCorrect(row)} className={button}>
          {t("correct.open")}
        </button>
      )}
      {row.id !== null && row.state !== null && CHECKABLE.includes(row.state) && (
        <button type="button" onClick={() => onValidate(row)} className={button}>
          {t("validation.open")}
        </button>
      )}
      {row.state === "answers_checked" && (row.fill_error || !extensionConnected) && (
        <p role="alert" className="basis-full text-sm text-minus">
          {row.fill_error
            ? t("applications.fill.error", { error: serverMessage(row.fill_error, lookup) })
            : t("applications.fill.extension_disconnected")}
        </p>
      )}
      {canPaste && (
        <form onSubmit={(event) => paste(event, id)} className="flex basis-full flex-wrap items-center gap-3">
          {form.error && <p className="basis-full text-sm text-minus">{serverMessage(form.error, lookup)}</p>}
          <input
            type="url"
            required
            aria-label={t("applications.form_url.label")}
            placeholder="https://"
            value={pasted}
            onChange={(event) => setPasted(event.target.value)}
            className="min-h-11 min-w-0 flex-1 basis-60 rounded-pill border border-outline bg-raised px-4"
          />
          <button type="submit" className={button}>
            {t("applications.form_url.save")}
          </button>
        </form>
      )}
      {failed && (
        <p role="alert" className="basis-full text-sm text-minus">
          {t(`applications.form_url.${failed}`)}
        </p>
      )}
    </li>
  );
}
