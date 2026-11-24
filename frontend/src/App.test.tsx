import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { fakeApi, reviewRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";
import { themes } from "./theme";

/** Every text and accessible name in the page: none may be missing from the locale file. */
function shownStrings(): string[] {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const strings: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim();
    if (text) strings.push(text);
  }
  for (const element of document.body.querySelectorAll("[aria-label], [title]")) {
    for (const name of ["aria-label", "title"]) {
      const value = element.getAttribute(name);
      if (value) strings.push(value);
    }
  }
  return strings;
}

/** Whether a shown string is a message of the file, or one with its {placeholders} filled. */
function isMessage(messages: string[], shown: string): boolean {
  return messages.some((message) => {
    const pattern = message.split(/\{\w+\}/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return new RegExp(`^${pattern.join(".+")}$`).test(shown);
  });
}

beforeEach(() => fakeApi(reviewRoutes()));
afterEach(() => vi.unstubAllGlobals());

function rootToken(name: string): string {
  return document.documentElement.style.getPropertyValue(`--ui-${name}`);
}

const openSettings = () => fireEvent.click(screen.getByRole("button", { name: fr["nav.settings"] }));

describe("the shell", () => {
  it("renders in French, then in English, only from the translation files", async () => {
    render(<App />);
    await screen.findByText(fr["review.empty"]);
    expect(document.documentElement.lang).toBe("fr");
    for (const label of [fr["nav.review"], fr["nav.applications"], fr["nav.control"], fr["nav.settings"]]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    const french = Object.values(fr);
    expect(shownStrings().filter((s) => !isMessage(french, s))).toEqual([]);

    openSettings();
    fireEvent.click(screen.getByRole("button", { name: fr["language.switch"] }));

    expect(document.documentElement.lang).toBe("en");
    for (const label of [en["nav.review"], en["nav.applications"], en["nav.control"], en["nav.settings"]]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    const english = Object.values(en);
    expect(shownStrings().filter((s) => !isMessage(english, s))).toEqual([]);
  });

  it("has the same keys in every translation file", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(fr).sort());
  });

  it("sets the theme and the language on Réglages, not in the header", () => {
    render(<App />);
    const header = within(screen.getByRole("banner"));

    for (const name of [fr["theme.toLight"], fr["language.switch"]]) {
      expect(header.queryByRole("button", { name })).toBeNull();
    }
    openSettings();
    const main = within(screen.getByRole("main"));
    expect(main.getByRole("button", { name: fr["theme.toLight"] })).toBeTruthy();
    expect(main.getByRole("button", { name: fr["language.switch"] })).toBeTruthy();
  });

  it("switches from the dark tokens to the light ones", () => {
    render(<App />);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(rootToken("background")).toBe(themes.dark.background);

    openSettings();
    fireEvent.click(screen.getByRole("button", { name: fr["theme.toLight"] }));

    expect(document.documentElement.dataset.theme).toBe("light");
    for (const [name, value] of Object.entries(themes.light)) {
      expect(rootToken(name)).toBe(value);
    }
    expect(themes.light.background).not.toBe(themes.dark.background);
  });

  it("keeps the chosen theme and language after a reload", () => {
    const { unmount } = render(<App />);
    openSettings();
    fireEvent.click(screen.getByRole("button", { name: fr["theme.toLight"] }));
    fireEvent.click(screen.getByRole("button", { name: fr["language.switch"] }));
    unmount();

    render(<App />);

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: en["nav.review"] })).toBeTruthy();
  });

  it("opens the page of the pill it is on", () => {
    render(<App />);
    const applications = screen.getByRole("button", { name: fr["nav.applications"] });

    fireEvent.click(applications);

    expect(applications.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(fr["nav.applications"]);
  });
});
