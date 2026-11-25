import { useCallback, useEffect, useState } from "react";
import { api, type Services } from "./api";
import { STATE_DOT, serviceName, worstState } from "./service-state";
import { useSettings } from "./settings";

const SERVICES_MS = 60_000;

/**
 * The worst state of /api/services, in the header: a dot, and when not green a link to Réglages that
 * counts and names the services at fault. It asks again each time scanEnds changes, each return of
 * the window's focus and each minute.
 */
export default function ServicesChip({ scanEnds, onOpenSettings }: { scanEnds: number; onOpenSettings: () => void }) {
  const { t, lookup } = useSettings();
  const [services, setServices] = useState<Services | null>(null);
  const [asks, setAsks] = useState(0);
  const ask = useCallback(() => setAsks((count) => count + 1), []);

  useEffect(() => {
    let current = true;
    api.services().then(
      (next) => current && setServices(next),
      () => current && setServices(null), // no state known: no dot
    );
    return () => {
      current = false;
    };
  }, [asks, scanEnds]);
  useEffect(() => {
    window.addEventListener("focus", ask);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") ask();
    }, SERVICES_MS);
    return () => {
      window.removeEventListener("focus", ask);
      clearInterval(timer);
    };
  }, [ask]);

  if (!services) return null;
  const { state, at_fault } = worstState(services);
  const dot = `size-3 shrink-0 rounded-pill ${STATE_DOT[state]}`;

  if (state === "green") return <span role="img" aria-label={t("services.green")} className={dot} />;
  const n = at_fault.length;
  const names = at_fault.map((service) => serviceName(service.id, lookup)).join(", ");
  const label = t(`services.${state}`, { names });
  const text = state === "red" ? t("services.chip.red", { n }) : t(`services.chip.orange.${n === 1 ? "one" : "other"}`, { n });
  return (
    <a
      href="#settings"
      aria-label={label}
      title={label}
      onClick={(event) => {
        event.preventDefault();
        onOpenSettings();
      }}
      className="inline-flex min-h-11 items-center gap-2 text-sm font-medium underline focus-visible:outline-2 focus-visible:outline-link"
    >
      <span aria-hidden="true" className={dot} />
      {text}
    </a>
  );
}
