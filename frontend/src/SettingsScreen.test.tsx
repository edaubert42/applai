import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { aService, fakeApi, reviewRoutes, servicesRoutes } from "./fake-api";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

afterEach(() => vi.unstubAllGlobals());

const services = {
  sites: [aService({ id: "alert_wttj" })],
  agents: [aService({ id: "triage", state: "orange", hint: "Check Claude login, API key, or quota." })],
  mailbox: [aService({ id: "mailbox", state: "red", last_error: "Outlook refused the token.", hint: "Run applai mail login." })],
  applai: [aService({ id: "scan" })],
};

async function openPage(language: "fr" | "en" = "fr", routes = servicesRoutes(services)) {
  const calls = fakeApi({ ...reviewRoutes(), ...routes });
  render(<App />);
  if (language === "en") fireEvent.click(screen.getByRole("button", { name: fr["language.switch"] }));
  const words = language === "en" ? en : fr;
  fireEvent.click(screen.getByRole("button", { name: words["nav.settings"] }));
  await screen.findAllByRole("region");
  return { calls, words };
}

describe("the Settings page", () => {
  it.each(["fr", "en"] as const)(
    "shows the four categories in order, each row with its state and, when not green, its sentence, in %s",
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
      expect(agent.getByText("Check Claude login, API key, or quota.")).toBeTruthy();

      const mail = rowOf(words["settings.category.mailbox"]);
      expect(mail.getByText(words["settings.service.mailbox"])).toBeTruthy();
      expect(mail.getByText(words["settings.state.red"])).toBeTruthy();
      expect(mail.getByText("Run applai mail login.")).toBeTruthy();

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

  it("has no button that signs in or sends anything", async () => {
    const hint = "Set APPLAI_LINKEDIN_CMD to connect LinkedIn.";
    const sites = [aService({ id: "alert_linkedin", state: "red", hint })];
    const { calls } = await openPage("fr", servicesRoutes({ ...services, sites }));
    const main = within(screen.getByRole("main"));

    expect(main.getByText(hint)).toBeTruthy();
    expect(main.queryAllByRole("button")).toEqual([]);
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
