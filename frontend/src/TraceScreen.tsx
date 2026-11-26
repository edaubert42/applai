import { useEffect, useState } from "react";
import { api, type ApplicationTrace, type GateStep, type RunStep, type Row } from "./api";
import { modelName } from "./service-state";
import { useSettings } from "./settings";
import { button } from "./styles";

/** "1.5 s", "1 min 15 s". */
function duration(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

/** The timeline of one application: its agents' runs and its candidate's gates, in order. */
export default function TraceScreen({ row, onBack }: { row: Row; onBack: () => void }) {
  const { t, language, lookup, formatTime } = useSettings();
  const id = row.id;
  const [trace, setTrace] = useState<ApplicationTrace | null>(null);
  const [failed, setFailed] = useState(id === null);

  useEffect(() => {
    if (id === null) return;
    let current = true;
    api.trace(id).then(
      (loaded) => current && setTrace(loaded),
      () => current && setFailed(true),
    );
    return () => {
      current = false;
    };
  }, [id]);

  const cost = (usd: number) =>
    usd.toLocaleString(language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 3 });
  const mark = "rounded-pill px-3 py-1 text-sm font-semibold";

  function run(step: RunStep) {
    return (
      <>
        <span className={`${mark} bg-raised`}>{t("trace.mark.agent")}</span>
        <div className="min-w-0 flex-1 basis-60">
          <p className="font-semibold">{lookup(`trace.agent.${step.agent}`) ?? step.agent}</p>
          <p className="text-sm text-muted">
            {[modelName(step.model), duration(step.duration_ms), step.cost_usd !== null && cost(step.cost_usd)]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {step.outcome === "running" ? (
            <p className="text-sm text-muted">{t("trace.running")}</p>
          ) : (
            step.outcome !== "ok" && <p className="text-sm text-minus">{t("trace.failed", { outcome: step.outcome })}</p>
          )}
        </div>
      </>
    );
  }

  function gate(step: GateStep) {
    const counted = step.to_state === "answers_checked" || step.to_state === "form_checked";
    return (
      <>
        <span className={`${mark} border border-link text-link`}>{t("trace.mark.you")}</span>
        <div className="min-w-0 flex-1 basis-60">
          <p className="font-semibold">{lookup(`trace.gate.${step.to_state}`) ?? step.to_state}</p>
          {counted && <p className="text-sm text-muted">{t("trace.corrections", { n: step.corrections })}</p>}
          {step.reasons.length > 0 && (
            <p className="text-sm text-muted [overflow-wrap:anywhere]">
              {step.reasons.map((reason) => lookup(`reason.${reason}`) ?? reason).join(", ")}
            </p>
          )}
        </div>
      </>
    );
  }

  return (
    <div className="mx-auto max-w-215 space-y-6">
      <button type="button" onClick={onBack} className={button}>
        {t("correct.back")}
      </button>
      <h2 className="text-xl font-semibold">{t("trace.title")}</h2>
      <p className="text-muted [overflow-wrap:anywhere]">
        {[row.title, row.company].join(" · ")}
      </p>
      {failed && (
        <p role="alert" className="text-minus">
          {t("trace.error")}
        </p>
      )}
      {trace && (
        <>
          <ol className="space-y-3">
            {trace.steps.map((step, index) => (
              <li key={index} className="flex flex-wrap items-center gap-4 rounded-block bg-surface p-4">
                {step.kind === "run" ? run(step) : gate(step)}
                <time dateTime={step.at} className="text-sm text-muted">
                  {formatTime(step.at)}
                </time>
              </li>
            ))}
          </ol>
          <p data-testid="trace-total" className="font-semibold">
            {t("trace.total", { time: duration(trace.total_duration_ms), cost: cost(trace.total_cost_usd) })}
          </p>
        </>
      )}
    </div>
  );
}
