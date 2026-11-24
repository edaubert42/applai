import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ScanStatus, type Services } from "./api";
import { STATE_DOT, serviceName, worstState } from "./service-state";
import { useSettings } from "./settings";

const POLL_MS = 3000;

/** Where the last scan stands, the worst state of the services, and the button that starts a scan by hand. */
export default function ScanBar({ onScanned, onOpenSettings }: { onScanned: () => void; onOpenSettings: () => void }) {
  const { t, formatTime } = useSettings();
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [services, setServices] = useState<Services | null>(null);
  const wasRunning = useRef(false);
  const [asked, setAsked] = useState(0); // each change asks the API for the status again
  const refresh = useCallback(() => setAsked((count) => count + 1), []);
  const [scans, setScans] = useState(0); // each scan that ends asks for the services' state again

  useEffect(() => {
    let current = true;
    api.scanStatus().then(
      (next) => {
        if (!current) return;
        setStatus(next);
        setUnavailable(false);
        if (wasRunning.current && !next.running) {
          onScanned();
          setScans((count) => count + 1);
        }
        wasRunning.current = next.running;
      },
      () => current && setUnavailable(true),
    );
    return () => {
      current = false;
    };
  }, [asked, onScanned]);
  useEffect(() => {
    let current = true;
    api.services().then(
      (next) => current && setServices(next),
      () => current && setServices(null), // no state known: no dot
    );
    return () => {
      current = false;
    };
  }, [scans]);
  // A scan in progress is asked about again, until it ends.
  useEffect(() => {
    if (!status?.running) return;
    const timer = setTimeout(refresh, POLL_MS);
    return () => clearTimeout(timer);
  }, [status, refresh]);

  async function start() {
    try {
      await api.startScan();
    } catch {
      // Refused (one is running, or too soon): the status says why.
    }
    refresh();
  }

  const last = status?.last_scan ?? null;
  const wait = status?.manual_scan_in_s ?? null;
  const disabled = unavailable || !status || status.running || wait !== null;
  const label = status?.running
    ? t("scan.running")
    : wait !== null
      ? t("scan.wait", { n: Math.ceil(wait / 60) })
      : t("scan.button");

  return (
    <section
      aria-label={t("scan.label")}
      className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-block bg-surface p-5"
    >
      {services && <ServicesDot services={services} onOpenSettings={onOpenSettings} />}
      <div className="min-w-0 flex-1 basis-80 text-sm">
        {unavailable ? (
          <p className="text-muted">{t("scan.unavailable")}</p>
        ) : !status ? null : !last ? (
          <p className="text-muted">{t("scan.never")}</p>
        ) : (
          <>
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>{t("scan.last", { time: formatTime(last.finished_at ?? last.started_at) })}</span>
              <span>{t("scan.new", { n: last.new_offers })}</span>
              {status.next_slot && (
                <span className="text-muted">
                  {t("scan.next", { time: formatTime(status.next_slot) })}
                </span>
              )}
              <span className="text-muted">
                {t("scan.linkedin", {
                  n: status.linkedin_reads_today,
                  max: status.linkedin_reads_per_day,
                })}
              </span>
            </p>
            {last.errors.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-minus [overflow-wrap:anywhere]">
                {last.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => void start()}
        className={`min-h-11 rounded-pill px-5 font-semibold focus-visible:outline-2 focus-visible:outline-link ${
          disabled
            ? "cursor-not-allowed border border-outline text-muted"
            : "cursor-pointer bg-primary text-on-primary hover:brightness-90"
        }`}
      >
        {label}
      </button>
    </section>
  );
}

/** The worst state of /api/services; when not green, a link to Réglages that names the services at fault. */
function ServicesDot({ services, onOpenSettings }: { services: Services; onOpenSettings: () => void }) {
  const { t, lookup } = useSettings();
  const { state, at_fault } = worstState(services);
  const dot = `size-3 shrink-0 rounded-pill ${STATE_DOT[state]}`;

  if (state === "green") return <span role="img" aria-label={t("scan.services.green")} className={dot} />;
  const names = at_fault.map((service) => serviceName(service.id, lookup)).join(", ");
  return (
    <a
      href="#settings"
      onClick={(event) => {
        event.preventDefault();
        onOpenSettings();
      }}
      className="inline-flex min-h-11 items-center gap-2 text-sm font-medium underline focus-visible:outline-2 focus-visible:outline-link [overflow-wrap:anywhere]"
    >
      <span aria-hidden="true" className={dot} />
      {t(`scan.services.${state}`, { names })}
    </a>
  );
}
