import { useState } from "react";
import ReviewScreen from "./ReviewScreen";
import { SettingsProvider, useSettings } from "./settings";

const pages = ["review", "applications", "control"] as const;
type Page = (typeof pages)[number];

const pill = "min-h-11 rounded-pill px-5 font-medium focus-visible:outline-2 focus-visible:outline-link";

export default function App() {
  return (
    <SettingsProvider>
      <Shell />
    </SettingsProvider>
  );
}

function Shell() {
  const { t } = useSettings();
  const [page, setPage] = useState<Page>("review");

  return (
    <div className="min-h-screen bg-background font-sans text-text">
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
              onClick={() => setPage(name)}
              className={`${pill} ${name === page ? "bg-primary text-on-primary" : "text-muted hover:text-text"}`}
            >
              {t(`nav.${name}`)}
            </button>
          ))}
        </nav>
        <Switches />
      </header>
      <main className="mx-auto min-w-0 max-w-5xl overflow-x-clip p-6">
        <h1 className="mb-4 text-2xl font-semibold">{t(`nav.${page}`)}</h1>
        {page === "review" ? (
          <ReviewScreen />
        ) : (
          <section className="rounded-card border border-border bg-surface p-8 text-muted">
            {t("page.empty")}
          </section>
        )}
      </main>
    </div>
  );
}

function Switches() {
  const { t, language, setLanguage, theme, setTheme } = useSettings();
  const other = language === "fr" ? "en" : "fr";
  const button = `${pill} min-w-11 border border-outline hover:bg-raised`;

  return (
    <div className="flex gap-2">
      <button
        type="button"
        aria-label={t(theme === "dark" ? "theme.toLight" : "theme.toDark")}
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        className={button}
      >
        {t(theme === "dark" ? "theme.light" : "theme.dark")}
      </button>
      <button
        type="button"
        lang={other}
        aria-label={t("language.switch")}
        onClick={() => setLanguage(other)}
        className={button}
      >
        {t("language.other")}
      </button>
    </div>
  );
}

/** The ApplAI mark: an A in a rounded square. */
function Mark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 32 32" className="size-8">
      <rect width="32" height="32" rx="10" className="fill-primary" />
      <path
        d="M10 23 16 9l6 14M12.5 18h7"
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="stroke-on-primary"
      />
    </svg>
  );
}
