import { useCallback, useState } from "react";
import ApplicationsScreen from "./ApplicationsScreen";
import { DemoProvider, useDemo } from "./demo";
import Mark from "./Mark";
import ReviewScreen from "./ReviewScreen";
import ServicesChip from "./ServicesChip";
import SettingsScreen from "./SettingsScreen";
import { SettingsProvider, useSettings } from "./settings";

const pages = ["review", "applications", "control", "settings"] as const;
type Page = (typeof pages)[number];

const pill = "min-h-11 cursor-pointer rounded-pill px-5 font-medium focus-visible:outline-2 focus-visible:outline-link";

export default function App() {
  return (
    <SettingsProvider>
      <DemoProvider>
        <Shell />
      </DemoProvider>
    </SettingsProvider>
  );
}

function Shell() {
  const { t } = useSettings();
  const demo = useDemo();
  const [chosen, setChosen] = useState<Page | null>(null);
  // The demo opens on Applications (once /api/health says so), with the trace of the application
  // waiting for the candidate, unless the visitor already chose a page.
  const page = chosen ?? (demo ? "applications" : "review");
  const [landed, setLanded] = useState(false);
  const onLanded = useCallback(() => setLanded(true), []);
  const [scanEnds, setScanEnds] = useState(0); // each scan that ends asks for the services' state again
  const scanEnded = useCallback(() => setScanEnds((count) => count + 1), []);

  return (
    <div className="min-h-screen bg-background font-sans text-text">
      {demo && (
        <p role="note" className="border-b border-unknown-border bg-unknown-bg px-6 py-2 text-center text-sm font-medium text-unknown">
          {t("demo.banner")}
        </p>
      )}
      <header className="flex flex-wrap items-center gap-4 border-b border-border bg-surface px-6 py-3">
        <div className="flex items-center gap-2">
          <Mark />
          <span className="text-lg font-semibold">{t("app.name")}</span>
        </div>
        <nav aria-label={t("nav.label")} className="mx-auto flex gap-1 rounded-pill bg-raised p-1">
          {pages.map((name) => (
            <button
              key={name}
              type="button"
              aria-current={name === page ? "page" : undefined}
              onClick={() => setChosen(name)}
              className={`${pill} ${name === page ? "bg-primary text-on-primary hover:brightness-90" : "text-muted hover:text-text"}`}
            >
              {t(`nav.${name}`)}
            </button>
          ))}
        </nav>
        <ServicesChip scanEnds={scanEnds} onOpenSettings={() => setChosen("settings")} />
      </header>
      <main className="mx-auto min-w-0 max-w-5xl overflow-x-clip p-6">
        <h1 className="mb-4 text-2xl font-semibold">{t(`nav.${page}`)}</h1>
        {page === "review" ? (
          <ReviewScreen onScanEnded={scanEnded} />
        ) : page === "applications" ? (
          <ApplicationsScreen landing={demo && !landed} onLanded={onLanded} />
        ) : page === "settings" ? (
          <SettingsScreen />
        ) : (
          <section className="rounded-card border border-border bg-surface p-8 text-muted">
            {t("page.empty")}
          </section>
        )}
      </main>
    </div>
  );
}
