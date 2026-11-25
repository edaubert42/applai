import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { aService, fakeApi, reviewRoutes, servicesRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => vi.unstubAllGlobals());

const services = {
  sites: [aService({ id: "alert_wttj" })],
  agents: [aService({ id: "triage", state: "orange", hint: "claude_login", model: "claude-haiku-5-5" })],
  mailbox: [aService({ id: "mailbox", state: "red", last_error: "Outlook refused the token.", hint: "mail_login" })],
  applai: [aService({ id: "scan" })],
};

async function openPage(language: "fr" | "en" = "fr", routes = servicesRoutes(services)) {
  const calls = fakeApi({ ...reviewRoutes(), ...routes });
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: fr["nav.settings"] }));
  if (language === "en") fireEvent.click(screen.getByRole("button", { name: fr["language.en"] }));
  const words = language === "en" ? en : fr;
  await screen.findAllByRole("region");
  return { calls, words };
}

describe("the Settings page", () => {
  it.each(["fr", "en"] as const)(
    "shows the four categories in order, each row with its state in words and, when not green, its sentence, in %s",
    async (language) => {
      const { words } = await openPage(language);

      const names = screen.getAllByRole("region").map((region) => region.querySelector("h2")?.textContent);
      expect(names).toEqual([
        words["settings.category.sites"],
        words["settings.category.agents"],
        words["settings.category.mailbox"],
        words["settings.category.applai"],
      ]);
      const rowOf = (category: string) => within(within(screen.getByRole("region", { name: category })).getByRole("listitem"));

      const site = rowOf(words["settings.category.sites"]);
      expect(site.getByText(words["settings.service.alert_wttj"])).toBeTruthy();
      expect(site.getByText(words["settings.state.green"])).toBeTruthy();
      expect(site.queryByText(/\./)).toBeNull();

      const agent = rowOf(words["settings.category.agents"]);
      expect(agent.getByText(words["settings.service.triage"])).toBeTruthy();
      expect(agent.getByText(words["settings.state.orange"])).toBeTruthy();
      expect(agent.getByText(words["settings.hint.claude_login"])).toBeTruthy();
      expect(agent.getByText("Haiku 5.5")).toBeTruthy();

      const mail = rowOf(words["settings.category.mailbox"]);
      expect(mail.getByText(words["settings.service.mailbox"])).toBeTruthy();
      expect(mail.getByText(words["settings.state.red"])).toBeTruthy();
      expect(mail.getByText(words["settings.hint.mail_login"])).toBeTruthy();

      const applai = rowOf(words["settings.category.applai"]);
      expect(applai.getByText(words["settings.service.scan"])).toBeTruthy();
      expect(applai.getByText(words["settings.state.green"])).toBeTruthy();
    },
  );

  it("shows every row of a category in the API's order, and an id with no name as written", async () => {
    const sites = [aService({ id: "extension" }), aService({ id: "alert_custom", state: "red", hint: "Wait for the next scan." })];
    await openPage("fr", servicesRoutes({ ...services, sites }));

    const rows = within(screen.getByRole("region", { name: fr["settings.category.sites"] })).getAllByRole("listitem");

    expect(rows.map((row) => row.querySelector("h3")?.textContent)).toEqual([fr["settings.service.extension"], "alert_custom"]);
  });

  it("puts a logo before every service's name, its own for each service", async () => {
    const sites = [aService({ id: "extension" }), aService({ id: "alert_wttj" }), aService({ id: "alert_custom" })];
    const agents = [aService({ id: "triage" }), aService({ id: "drafting" })];
    const applai = [aService({ id: "scan" }), aService({ id: "backup" })];
    await openPage("fr", servicesRoutes({ ...services, sites, agents, applai }));

    const logoOf = (name: string) => {
      const row = screen.getByRole("heading", { name, level: 3 }).closest("li") as HTMLElement;
      return row.querySelector("[data-logo]")?.getAttribute("data-logo");
    };
    const logos = [
      fr["settings.service.extension"],
      fr["settings.service.alert_wttj"],
      fr["settings.service.mailbox"],
      fr["settings.service.scan"],
      fr["settings.service.backup"],
    ].map(logoOf);

    expect(logos.every(Boolean)).toBe(true);
    expect(new Set(logos).size).toBe(logos.length);
    expect(logoOf(fr["settings.service.triage"])).toBe(logoOf(fr["settings.service.drafting"]));
    expect(logoOf("alert_custom")).toBeTruthy();
  });

  it("opens from Réglages, the last item of the navigation", async () => {
    fakeApi({ ...reviewRoutes(), ...servicesRoutes(services) });
    render(<App />);
    const items = within(screen.getByRole("navigation")).getAllByRole("button");
    const last = items[items.length - 1] as HTMLElement;

    expect(last.textContent).toBe(fr["nav.settings"]);
    fireEvent.click(last);

    expect(last.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(fr["nav.settings"]);
    expect(await screen.findAllByRole("region")).toHaveLength(4);
  });

  it.each(["fr", "en"] as const)("names every agent of the pipeline in %s", async (language) => {
    const ids = ["triage", "form_capture", "drafting", "cv_tailoring", "filling", "review"] as const;
    const { words } = await openPage(language, servicesRoutes({ ...services, agents: ids.map((id) => aService({ id })) }));

    const rows = within(screen.getByRole("region", { name: words["settings.category.agents"] })).getAllByRole("listitem");

    expect(rows.map((row) => row.querySelector("h3")?.textContent)).toEqual(ids.map((id) => words[`settings.service.${id}`]));
  });

  it("has a sun and a moon for the theme, the language apart, and nothing that signs in or sends", async () => {
    const hint = "extension_id must be 32 letters.";
    const sites = [aService({ id: "extension", state: "red", hint })];
    const { calls } = await openPage("fr", servicesRoutes({ ...services, sites }));
    const main = within(screen.getByRole("main"));
    const group = (name: string) => within(main.getByRole("group", { name })).getAllByRole("button");
    const pressed = (button: HTMLElement) => button.getAttribute("aria-pressed");

    expect(main.getByText(hint)).toBeTruthy(); // a sentence that is no code is shown as sent
    const theme = group(fr["settings.appearance"]);
    expect(theme.map((button) => button.textContent)).toEqual([fr["theme.light"], fr["theme.dark"]]);
    expect(theme.map((button) => button.querySelector("svg")?.getAttribute("data-icon"))).toEqual(["sun", "moon"]);
    expect(theme.map(pressed)).toEqual(["false", "true"]);
    const language = group(fr["settings.language"]);
    expect(language.map((button) => button.textContent)).toEqual([fr["language.fr"], fr["language.en"]]);
    expect(language.map(pressed)).toEqual(["true", "false"]);
    expect(main.getAllByRole("button")).toHaveLength(4);
    expect(main.queryAllByRole("link")).toEqual([]);
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("tells the candidate when the services could not be loaded", async () => {
    fakeApi(reviewRoutes());
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: fr["nav.settings"] }));

    expect((await screen.findByRole("alert")).textContent).toBe(fr["settings.error"]);
  });
});
