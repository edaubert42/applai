import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { aScan, aService, fakeApi, reviewRoutes, servicesRoutes } from "./fake-api";
import fr from "./locales/fr.json";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

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

describe("the scan bar while a scan reads LinkedIn", () => {
  it("shows how many of the new LinkedIn offers it went through", async () => {
    const scan = aScan({ running: true, linkedin_progress: { read: 12, total: 130 } });
    fakeApi({ ...reviewRoutes({}, scan), ...servicesRoutes() });
    render(<App />);

    expect(await scanBar().findByText("LinkedIn : 12 / 130")).toBeTruthy();
  });

  it("shows no LinkedIn progress when no scan reads LinkedIn", async () => {
    fakeApi({ ...reviewRoutes(), ...servicesRoutes() });
    render(<App />);

    await scanBar().findByText(fill(fr["scan.new"], { n: "4" }));
    expect(scanBar().queryByText(/LinkedIn : \d+ \/ \d+/)).toBeNull();
  });

  it("shows the active services with small logos and a Stop button while running", async () => {
    let stopping = false;
    const calls = fakeApi({
      ...reviewRoutes(),
      "GET /api/scan": () =>
        aScan({
          running: true,
          stopping,
          active_service: "alert_linkedin",
          services_used: ["mailbox", "alert_hellowork", "alert_linkedin", "triage"],
        }),
      "POST /api/scan/stop": () => {
        stopping = true;
        return new Response(null, { status: 204 });
      },
      ...servicesRoutes(),
    });
    render(<App />);

    const bar = scanBar();
    const stop = (await bar.findByRole("button", { name: fr["scan.stop"] })) as HTMLButtonElement;
    const region = screen.getByRole("region", { name: fr["scan.label"] });
    const logos = [...region.querySelectorAll("[data-logo]")].map((el) => el.getAttribute("data-logo"));
    expect(logos).toEqual(["mailbox", "alert_hellowork", "alert_linkedin", "claude"]);

    fireEvent.click(stop);

    expect(await bar.findByRole("button", { name: fr["scan.stopping"] })).toBeTruthy();
    expect(calls.filter((c) => c.method === "POST" && c.path === "/api/scan/stop")).toHaveLength(1);
  });

  it("hides the Stop button and service logos when no scan is running", async () => {
    fakeApi({ ...reviewRoutes(), ...servicesRoutes() });
    render(<App />);

    await scanBar().findByText(fill(fr["scan.new"], { n: "4" }));
    expect(scanBar().queryByRole("button", { name: fr["scan.stop"] })).toBeNull();
    const region = screen.getByRole("region", { name: fr["scan.label"] });
    expect(region.querySelectorAll("[data-logo]")).toHaveLength(0);
  });

  it("keeps Stop usable if stopping fails", async () => {
    fakeApi({
      ...reviewRoutes({}, aScan({ running: true, services_used: ["mailbox"], active_service: "mailbox" })),
      "POST /api/scan/stop": new Response("error", { status: 500 }),
      ...servicesRoutes(),
    });
    render(<App />);

    const stop = (await scanBar().findByRole("button", { name: fr["scan.stop"] })) as HTMLButtonElement;
    fireEvent.click(stop);

    await act(() => Promise.resolve());
    expect((await scanBar().findByRole("button", { name: fr["scan.stop"] }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("the scan bar's dot stays fresh", () => {
  const servicesCalls = (calls: { method: string; path: string }[]) =>
    calls.filter((call) => call.method === "GET" && call.path === "/api/services").length;

  /** The services answer green first, then red: a service that failed since the page opened. */
  async function openGreenThenRed() {
    let asked = 0;
    const calls = fakeApi({
      ...reviewRoutes(),
      "GET /api/services": () =>
        servicesRoutes(asked++ === 0 ? { sites: [aService()] } : { mailbox: [red] })["GET /api/services"],
    });
    render(<App />);
    await scanBar().findByRole("img", { name: fr["scan.services.green"] });
    return calls;
  }

  it("fetches the services again when the window regains focus", async () => {
    const calls = await openGreenThenRed();
    expect(servicesCalls(calls)).toBe(1);

    fireEvent.focus(window);

    const name = fill(fr["scan.services.red"], { names: fr["settings.service.mailbox"] });
    expect(await scanBar().findByRole("link", { name })).toBeTruthy();
    expect(servicesCalls(calls)).toBe(2);
  });

  it("fetches the services again every 60 s while the page is visible", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = await openGreenThenRed();

    await act(() => vi.advanceTimersByTimeAsync(59_000));
    expect(servicesCalls(calls)).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(servicesCalls(calls)).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(servicesCalls(calls)).toBe(3);
  });
});
