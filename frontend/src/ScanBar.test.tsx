import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { aService, fakeApi, reviewRoutes, servicesRoutes } from "./fake-api";
import fr from "./locales/fr.json";

afterEach(() => vi.unstubAllGlobals());

const fill = (template: string, values: Record<string, string>) =>
  template.replace(/\{(\w+)\}/g, (_, name: string) => values[name] ?? "");

const scanBar = () => within(screen.getByRole("region", { name: fr["scan.label"] }));

const red = aService({ id: "mailbox", state: "red", hint: "Run applai mail login." });
const orange = aService({ id: "triage", state: "orange", hint: "Check Claude login, API key, or quota." });

function openReview(services: Parameters<typeof servicesRoutes>[0]) {
  fakeApi({ ...reviewRoutes(), ...servicesRoutes(services) });
  render(<App />);
}

describe("the scan bar's dot", () => {
  it("is red with one red service and one orange, names the red one and leads to Réglages", async () => {
    openReview({ sites: [aService()], agents: [orange], mailbox: [red] });

    const name = fill(fr["scan.services.red"], { names: fr["settings.service.mailbox"] });
    const link = await scanBar().findByRole("link", { name });
    expect(link.textContent).not.toContain(fr["settings.service.triage"]);
    expect(link.querySelector(".bg-minus")).not.toBeNull();

    fireEvent.click(link);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(fr["nav.settings"]);
    expect(screen.getByRole("button", { name: fr["nav.settings"] }).getAttribute("aria-current")).toBe("page");
  });

  it("names every red service", async () => {
    const extension = aService({ id: "extension", state: "red", hint: "Install the extension." });
    openReview({ sites: [extension], mailbox: [red] });

    const names = `${fr["settings.service.extension"]}, ${fr["settings.service.mailbox"]}`;
    expect(await scanBar().findByRole("link", { name: fill(fr["scan.services.red"], { names }) })).toBeTruthy();
  });

  it("is orange with only an orange service, and leads to Réglages", async () => {
    openReview({ sites: [aService()], agents: [orange] });

    const name = fill(fr["scan.services.orange"], { names: fr["settings.service.triage"] });
    const link = await scanBar().findByRole("link", { name });
    expect(link.querySelector(".bg-unknown")).not.toBeNull();
  });

  it("is green and not a link when no service is red or orange", async () => {
    openReview({ sites: [aService()], agents: [aService({ id: "triage" })] });

    const dot = await scanBar().findByRole("img", { name: fr["scan.services.green"] });
    expect(dot.className).toContain("bg-plus");
    expect(scanBar().queryByRole("link")).toBeNull();
  });
});
