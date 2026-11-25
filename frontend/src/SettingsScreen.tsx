import { useEffect, useState, type ReactNode } from "react";
import { CATEGORIES, api, type Category, type Service, type ServiceState, type Services } from "./api";
import { ServiceLogo } from "./ServiceLogo";
import { STATE_DOT, serviceName } from "./service-state";
import { useSettings } from "./settings";

/**
 * The Réglages page: the theme and the language, each in its own block, then the state of every service, by category, as /api/services computes it.
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
      <Preferences />
      {failed && <p role="alert" className="text-minus">{t("settings.error")}</p>}
      {services &&
        CATEGORIES.map((category) => (
          <section key={category} aria-labelledby={`category-${category}`} className="space-y-3">
            <h2 id={`category-${category}`} className="text-xl font-semibold">
              {t(`settings.category.${category}`)}
            </h2>
            <ul className="divide-y divide-border rounded-block border border-border bg-surface">
              {services[category].map((service) => (
                <ServiceRow key={service.id} service={service} category={category} />
              ))}
            </ul>
          </section>
        ))}
    </div>
  );
}

const STATE_TEXT: Record<ServiceState, string> = { red: "text-minus", orange: "text-unknown", green: "text-plus" };

/** "claude-haiku-5-5" as "Haiku 5.5"; a name of another shape stays as it is. */
function modelName(model: string): string {
  return model.replace(
    /^claude-([a-z])([a-z]+)-(\d+)-(\d+)$/,
    (_, first: string, rest: string, major: string, minor: string) => `${first.toUpperCase()}${rest} ${major}.${minor}`,
  );
}

function ServiceRow({ service, category }: { service: Service; category: Category }) {
  const { t, lookup } = useSettings();
  const name = serviceName(service.id, lookup);

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
      <ServiceLogo id={service.id} category={category} name={name} />
      <div className="min-w-0 flex-1 basis-32 [overflow-wrap:anywhere]">
        <h3 className="font-semibold">{name}</h3>
        {service.model && <p className="text-sm text-muted">{modelName(service.model)}</p>}
      </div>
      <span className={`flex items-center gap-2 text-sm font-semibold ${STATE_TEXT[service.state]}`}>
        <span aria-hidden="true" className={`size-2.5 shrink-0 rounded-full ${STATE_DOT[service.state]}`} />
        {t(`settings.state.${service.state}`)}
      </span>
      {service.state !== "green" && service.hint && (
        // A hint that is no code (a settings file's own error) is shown as the API sent it.
        <p className="basis-full text-sm text-muted [overflow-wrap:anywhere]">
          {lookup(`settings.hint.${service.hint}`) ?? service.hint}
        </p>
      )}
    </li>
  );
}

/** The theme and the language, kept in localStorage by the settings context. */
function Preferences() {
  const { t, language, setLanguage, theme, setTheme } = useSettings();

  return (
    <div className="space-y-3">
      <Choice
        id="appearance"
        title={t("settings.appearance")}
        sub={t("settings.appearance.sub")}
        value={theme}
        onChange={setTheme}
        options={[
          { value: "light", label: t("theme.light"), icon: <ThemeIcon name="sun" /> },
          { value: "dark", label: t("theme.dark"), icon: <ThemeIcon name="moon" /> },
        ]}
      />
      <Choice
        id="language"
        title={t("settings.language")}
        sub={t("settings.language.sub")}
        value={language}
        onChange={setLanguage}
        options={[
          { value: "fr", label: t("language.fr"), lang: "fr" },
          { value: "en", label: t("language.en"), lang: "en" },
        ]}
      />
    </div>
  );
}

/** A titled block with one pill per option, the current one pressed. */
function Choice<T extends string>({
  id,
  title,
  sub,
  value,
  onChange,
  options,
}: {
  id: string;
  title: string;
  sub: string;
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; icon?: ReactNode; lang?: string }[];
}) {
  // Not a named region: the regions of this page are the services' categories.
  return (
    <section className="flex flex-wrap items-center justify-between gap-3 rounded-block border border-border bg-surface p-4">
      <div>
        <h2 id={id} className="text-xl font-semibold">
          {title}
        </h2>
        <p className="text-sm text-muted">{sub}</p>
      </div>
      <div role="group" aria-labelledby={id} className="flex flex-wrap gap-1 rounded-pill bg-raised p-1">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            lang={option.lang}
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-pill px-5 font-medium focus-visible:outline-2 focus-visible:outline-link ${
              option.value === value ? "bg-primary text-on-primary" : "text-muted hover:text-text"
            }`}
          >
            {option.icon}
            {option.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function ThemeIcon({ name }: { name: "sun" | "moon" }) {
  return (
    <svg
      data-icon={name}
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-5 shrink-0"
    >
      {name === "sun" ? (
        <>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </>
      ) : (
        <path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z" />
      )}
    </svg>
  );
}
