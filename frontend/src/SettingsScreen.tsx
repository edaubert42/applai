import { useEffect, useState } from "react";
import { CATEGORIES, api, type Service, type Services } from "./api";
import { STATE_DOT, serviceName } from "./service-state";
import { useSettings } from "./settings";

/**
 * The Réglages page: the theme and the language, then the state of every service, by category, as /api/services computes it.
 * The services are only shown: the sentence of one that is not green says what to do, and nothing here signs in or sends.
 * (settings.tsx is the theme and language context, not this page.)
 */
export default function SettingsScreen() {
  const { t } = useSettings();
  const [services, setServices] = useState<Services | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    api.services().then(
      (loaded) => current && setServices(loaded),
      () => current && setFailed(true),
    );
    return () => {
      current = false;
    };
  }, []);

  return (
    <div className="mx-auto max-w-215 space-y-7">
      <Display />
      {failed && <p role="alert" className="text-minus">{t("settings.error")}</p>}
      {services &&
        CATEGORIES.map((category) => (
          <section key={category} aria-labelledby={`category-${category}`} className="space-y-3">
            <h2 id={`category-${category}`} className="text-xl font-semibold">
              {t(`settings.category.${category}`)}
            </h2>
            <ul className="space-y-3">
              {services[category].map((service) => (
                <ServiceRow key={service.id} service={service} />
              ))}
            </ul>
          </section>
        ))}
    </div>
  );
}

function ServiceRow({ service }: { service: Service }) {
  const { t, lookup } = useSettings();
  const name = serviceName(service.id, lookup);

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-block bg-surface p-4">
      <h3 className="min-w-0 flex-1 basis-60 font-semibold [overflow-wrap:anywhere]">{name}</h3>
      <span className="inline-flex items-center gap-2 rounded-pill bg-raised px-3 py-1 text-sm">
        <span aria-hidden="true" className={`size-2.5 rounded-full ${STATE_DOT[service.state]}`} />
        {t(`settings.state.${service.state}`)}
      </span>
      {service.state !== "green" && service.hint && (
        <p className="basis-full text-sm text-muted [overflow-wrap:anywhere]">{service.hint}</p>
      )}
    </li>
  );
}

/** The theme and the language, kept in localStorage by the settings context. */
function Display() {
  const { t, language, setLanguage, theme, setTheme } = useSettings();
  const other = language === "fr" ? "en" : "fr";
  const button =
    "min-h-11 min-w-11 cursor-pointer rounded-pill border border-outline px-5 font-medium hover:bg-raised focus-visible:outline-2 focus-visible:outline-link";

  // Not a named region: the regions of this page are the services' categories.
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">{t("settings.display")}</h2>
      <div className="flex flex-wrap gap-2">
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
    </section>
  );
}
