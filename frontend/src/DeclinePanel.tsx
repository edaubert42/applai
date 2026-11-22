import { useEffect, useId, useState } from "react";
import { REASONS, type Reason } from "./api";
import { useSettings } from "./settings";

const REASON_LABELS = {
  company: "reason.company",
  mission: "reason.mission",
  experience: "reason.experience",
  skills: "reason.skills",
  salary: "reason.salary",
  location: "reason.location",
  other: "reason.other",
} as const;

const action = "min-h-11 rounded-pill px-5 font-semibold focus-visible:outline-2 focus-visible:outline-link";

/**
 * Why the candidate declines an offer: the reasons as pills (keys 1 to 7) and a note.
 * Enter saves, once a reason is chosen; Escape cancels.
 */
export default function DeclinePanel({
  onSave,
  onCancel,
}: {
  onSave: (reasons: Reason[], note: string) => void;
  onCancel: () => void;
}) {
  const { t } = useSettings();
  const noteId = useId();
  const [chosen, setChosen] = useState<readonly Reason[]>([]);
  const [note, setNote] = useState("");
  // The reasons stay in the order of the list, whatever the order they were chosen in.
  const ordered = REASONS.filter((reason) => chosen.includes(reason));

  function toggle(reason: Reason) {
    setChosen((now) => (now.includes(reason) ? now.filter((r) => r !== reason) : [...now, reason]));
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const typing = target?.tagName === "TEXTAREA";
      const reason = REASONS[Number(event.key) - 1];
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
      <h3 className="text-xl font-semibold">{t("decline.title")}</h3>
      <p className="text-muted">{t("decline.hint")}</p>
      <div className="flex flex-wrap gap-2">
        {REASONS.map((reason, index) => (
          <button
            key={reason}
            type="button"
            aria-pressed={chosen.includes(reason)}
            onClick={() => toggle(reason)}
            className={`${action} flex items-center gap-2 border ${
              chosen.includes(reason) ? "border-primary bg-primary text-on-primary" : "border-outline"
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
        <button type="button" onClick={onCancel} className={`${action} text-link`}>
          {t("decline.cancel")}
        </button>
        <button
          type="button"
          disabled={ordered.length === 0}
          onClick={() => onSave(ordered, note.trim())}
          className={`${action} bg-primary text-on-primary disabled:bg-raised disabled:text-muted`}
        >
          {t("decline.save")}
        </button>
      </div>
      <p className="text-center text-sm text-muted">{t("decline.keys")}</p>
    </div>
  );
}
