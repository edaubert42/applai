import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import en from "./locales/en.json";
import fr from "./locales/fr.json";
import { applyTheme, type ThemeName } from "./theme";

export type Language = "fr" | "en";
export type MessageKey = keyof typeof fr;

// Typed against the French file: a key missing from English fails the typecheck.
const messages: Record<Language, Record<MessageKey, string>> = { fr, en };

interface Settings {
  language: Language;
  setLanguage: (language: Language) => void;
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
  /** A message, its {name} placeholders filled from values. */
  t: (key: MessageKey, values?: Record<string, string | number>) => string;
  /** The message of a key built at run time (a source's name), or undefined if there is none. */
  lookup: (key: string) => string | undefined;
  /** A date and time in the candidate's language, like "22 nov., 18:00". */
  formatTime: (iso: string) => string;
}

const SettingsContext = createContext<Settings | null>(null);

/** A choice kept in localStorage, so it survives a reload. */
function useStored<T extends string>(key: string, allowed: readonly T[], fallback: T) {
  const [value, setValue] = useState<T>(() => {
    const saved = localStorage.getItem(key);
    return allowed.find((option) => option === saved) ?? fallback;
  });
  useEffect(() => localStorage.setItem(key, value), [key, value]);
  return [value, setValue] as const;
}

/** The candidate's language and theme, French and dark by default. */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useStored<Language>("applai.language", ["fr", "en"], "fr");
  const [theme, setTheme] = useStored<ThemeName>("applai.theme", ["dark", "light"], "dark");

  useEffect(() => {
    document.documentElement.lang = language;
    document.title = messages[language]["app.name"];
  }, [language]);
  useEffect(() => applyTheme(theme, document.documentElement), [theme]);

  const t: Settings["t"] = (key, values = {}) =>
    messages[language][key].replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? ""));
  const lookup: Settings["lookup"] = (key) =>
    Object.hasOwn(messages[language], key) ? messages[language][key as MessageKey] : undefined;
  const formatTime = (iso: string) =>
    new Date(iso).toLocaleString(language, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  return (
    <SettingsContext.Provider
      value={{ language, setLanguage, theme, setTheme, t, lookup, formatTime }}
    >
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings(): Settings {
  const settings = useContext(SettingsContext);
  if (!settings) throw new Error("useSettings needs a SettingsProvider above it");
  return settings;
}
