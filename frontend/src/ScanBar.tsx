import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Category, type ScanStatus } from "./api";
import AddOffer from "./AddOffer";
import { ServiceLogo } from "./ServiceLogo";
import { serviceName } from "./service-state";
import { useSettings } from "./settings";

const POLL_MS = 3000;

function categoryOf(serviceId: string): Category {
  if (serviceId === "mailbox") return "mailbox";
  if (serviceId === "triage") return "agents";
  return "sites";
}

/**
 * The button that starts or stops a scan, the one that adds an offer, where the last scan stands, and
 * where the offers of the last 24 hours come from. onScanned runs when a scan ends, onAdded when an
 * offer was added.
 */
export default function ScanBar({ onScanned, onAdded }: { onScanned: () => void; onAdded: () => void }) {
  const { t, lookup, formatTime } = useSettings();
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [stopping, setStopping] = useState(false);
  const wasRunning = useRef(false);
  const [asked, setAsked] = useState(0); // each change asks the API for the status again
  const refresh = useCallback(() => setAsked((count) => count + 1), []);

  useEffect(() => {
    let current = true;
    api.scanStatus().then(
      (next) => {
        if (!current) return;
        setStatus(next);
        setUnavailable(false);
        if (!next.running) setStopping(false);
        if (wasRunning.current && !next.running) onScanned();
        wasRunning.current = next.running;
      },
      () => current && setUnavailable(true),
    );
    return () => {
      current = false;
    };
  }, [asked, onScanned]);
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

  async function stop() {
    setStopping(true);
    try {
      await api.stopScan();
      refresh();
    } catch {
      setStopping(false);
    }
  }

  const last = status?.last_scan ?? null;
  const wait = status?.manual_scan_in_s ?? null;
  const isStopping = stopping || Boolean(status?.stopping);
  const disabled = unavailable || !status || status.running || wait !== null;
  const label = status?.running
    ? t("scan.running")
    : wait !== null
      ? t("scan.wait", { n: Math.ceil(wait / 60) })
      : t("scan.button");

  return (
    <section aria-label={t("scan.label")} className="space-y-3 rounded-block bg-surface p-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
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
        {status?.running && (
          <button
            type="button"
            disabled={isStopping}
            onClick={() => void stop()}
            className={`min-h-11 rounded-pill border px-5 font-semibold focus-visible:outline-2 focus-visible:outline-link ${
              isStopping
                ? "cursor-not-allowed border-outline text-muted"
                : "cursor-pointer border-minus text-minus hover:bg-minus/10"
            }`}
          >
            {isStopping ? t("scan.stopping") : t("scan.stop")}
          </button>
        )}
        <AddOffer
          onAdded={() => {
            refresh(); // the offer shows under "Ajoutée"
            onAdded();
          }}
        />
        <p className="min-w-0 text-sm text-muted">
          {unavailable ? (
            t("scan.unavailable")
          ) : !status ? null : !last ? (
            t("scan.never")
          ) : (
            t(status.next_slot ? "scan.last_next" : "scan.last", {
              time: formatTime(last.finished_at ?? last.started_at),
              next: status.next_slot ? formatTime(status.next_slot) : "",
            })
          )}
        </p>
        {status?.running && status.linkedin_progress && (
          <p className="text-sm font-medium">
            {t("scan.linkedin_progress", { n: status.linkedin_progress.read, total: status.linkedin_progress.total })}
          </p>
        )}
      </div>
      {status && !unavailable && <Last24h status={status} lookup={lookup} />}
      {last && last.errors.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-minus [overflow-wrap:anywhere]">
          {last.errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The offers of the last 24 hours, one pill per service with its count. While a scan runs the services
 * it uses are outlined (the active one thickly) and shown even without a count.
 */
function Last24h({ status, lookup }: { status: ScanStatus; lookup: (key: string) => string | undefined }) {
  const { t } = useSettings();
  const counts = Object.entries(status.last_24h).filter(([, count]) => count > 0);
  const total = counts.reduce((sum, [, count]) => sum + count, 0);
  const used = status.running ? status.services_used : [];
  const ids = [...new Set([...used, ...counts.map(([id]) => id)])];

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
      <span>{t(total <= 1 ? "scan.last_24h.one" : "scan.last_24h.other", { n: total })}</span>
      <ul aria-label={t("scan.active_services")} className="flex flex-wrap items-center gap-2">
        {ids.map((id) => {
          const count = status.last_24h[id] ?? 0;
          const name = id === "manual" ? t("scan.manual") : serviceName(id, lookup);
          const use = !used.includes(id) ? undefined : status.active_service === id ? "active" : "used";
          const outline = use === "active" ? "ring-2 ring-link animate-pulse" : use === "used" ? "ring-1 ring-link" : "";
          return (
            <li
              key={id}
              data-use={use}
              className={`inline-flex items-center gap-1.5 rounded-pill bg-raised py-1 pr-3 pl-1 ${outline}`}
            >
              <ServiceLogo id={id} category={categoryOf(id)} name={name} size="sm" />
              {count > 0 ? `${name} ${count}` : name}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
