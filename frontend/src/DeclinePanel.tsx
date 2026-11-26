import { useEffect, useId, useState } from "react";
import type { DropReason } from "./api";
import { useSettings } from "./settings";

const REASON_LABELS = {
  company: "reason.company",
  mission: "reason.mission",
  experience: "reason.experience",
  skills: "reason.skills",
  salary: "reason.salary",
  location: "reason.location",
  expired: "reason.expired",
  other: "reason.other",
  offer_closed: "reason.offer_closed",
} as const;

// The words of the panel's title, hint and keys line: declining an offer, or dropping an application.
const WORDS = {
  decline: { title: "decline.title", hint: "decline.hint", keys: "decline.keys" },
  drop: { title: "drop.title", hint: "drop.hint", keys: "drop.keys" },
} as const;

const action = "min-h-11 rounded-pill px-5 font-semibold focus-visible:outline-2 focus-visible:outline-link";

/**
 * Why the candidate declines an offer, or drops an application: the reasons as pills (keys 1
 * to the last) and a note. Enter saves, once a reason is chosen; Escape cancels. Keys typed in
 * another input stay there.
 */
export default function DeclinePanel<R extends DropReason>({
  reasons,
  kind = "decline",
  onSave,
  onCancel,
}: {
  reasons: readonly R[];
  kind?: keyof typeof WORDS;
  onSave: (reasons: R[], note: string) => void;
  onCancel: () => void;
}) {
  const { t } = useSettings();
  const words = WORDS[kind];
  const noteId = useId();
  const hintId = useId();
  const [chosen, setChosen] = useState<readonly R[]>([]);
  const [note, setNote] = useState("");
  // The reasons stay in the order of the list, whatever the order they were chosen in.
  const ordered = reasons.filter((reason) => chosen.includes(reason));

  function toggle(reason: R) {
    setChosen((now) => (now.includes(reason) ? now.filter((r) => r !== reason) : [...now, reason]));
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (target?.tagName === "INPUT") return;
      const typing = target?.tagName === "TEXTAREA";
      const reason = reasons[Number(event.key) - 1];
      if (event.key === "Escape") {
        onCancel();
      } else if (reason && !typing) {
        toggle(reason);
      } else if (event.key === "Enter" && !event.shiftKey && target?.tagName !== "BUTTON") {
        event.preventDefault();
        if (ordered.length > 0) onSave(ordered, note.trim());
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  return (
    <div className="space-y-4">
      <h3 className="text-xl font-semibold">{t(words.title)}</h3>
      <p className="text-muted">{t(words.hint)}</p>
      <div className="flex flex-wrap gap-2">
        {reasons.map((reason, index) => (
          <button
            key={reason}
            type="button"
            aria-pressed={chosen.includes(reason)}
            onClick={() => toggle(reason)}
            className={`${action} flex cursor-pointer items-center gap-2 border ${
              chosen.includes(reason)
                ? "border-primary bg-primary text-on-primary hover:brightness-90"
                : "border-outline hover:bg-raised"
            }`}
          >
            <kbd className="font-mono text-sm opacity-80">{index + 1}</kbd>
            {t(REASON_LABELS[reason])}
          </button>
        ))}
      </div>
      <div>
        <label htmlFor={noteId} className="mb-1 block text-sm text-muted">
          {t("decline.note")}
        </label>
        <textarea
          id={noteId}
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="w-full rounded-block border border-outline bg-background p-3 focus-visible:outline-2 focus-visible:outline-link"
        />
      </div>
      <div className="flex flex-wrap items-center justify-end gap-3">
        {ordered.length === 0 && (
          <p id={hintId} className="text-sm text-muted">
            {t("decline.needReason")}
          </p>
        )}
        <button type="button" onClick={onCancel} className={`${action} cursor-pointer text-link hover:bg-raised`}>
          {t("decline.cancel")}
        </button>
        <button
          type="button"
          disabled={ordered.length === 0}
          aria-describedby={ordered.length === 0 ? hintId : undefined}
          onClick={() => onSave(ordered, note.trim())}
          className={`${action} enabled:cursor-pointer disabled:cursor-not-allowed bg-primary text-on-primary enabled:hover:brightness-90 disabled:bg-raised disabled:text-muted`}
        >
          {t("decline.save")}
        </button>
      </div>
      <p className="text-center text-sm text-muted">{t(words.keys)}</p>
    </div>
  );
}
