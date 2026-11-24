import { useEffect, useState } from "react";
import { CATEGORIES, api, type Service, type ServiceState, type Services } from "./api";
import { useSettings } from "./settings";

const dot: Record<ServiceState, string> = {
  red: "bg-minus",
  orange: "bg-unknown",
  green: "bg-plus",
};

/**
 * The Réglages page: the state of every service, by category, as /api/services computes it.
 * It only shows: the sentence of a service that is not green says what to do, and nothing here signs in or sends.
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
  // A service the translation files do not name (a new source) shows its id.
  const name = lookup(`settings.service.${service.id}`) ?? service.id;

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-block bg-surface p-4">
      <h3 className="min-w-0 flex-1 basis-60 font-semibold [overflow-wrap:anywhere]">{name}</h3>
      <span className="inline-flex items-center gap-2 rounded-pill bg-raised px-3 py-1 text-sm">
        <span aria-hidden="true" className={`size-2.5 rounded-full ${dot[service.state]}`} />
        {t(`settings.state.${service.state}`)}
      </span>
      {service.state !== "green" && service.hint && (
        <p className="basis-full text-sm text-muted [overflow-wrap:anywhere]">{service.hint}</p>
      )}
    </li>
  );
}
