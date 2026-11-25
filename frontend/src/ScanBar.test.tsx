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
const header = () => within(screen.getByRole("banner"));

const red = aService({ id: "mailbox", state: "red", hint: "Run applai mail login." });
const orange = aService({ id: "triage", state: "orange", hint: "Check Claude login, API key, or quota." });

function openReview(services: Parameters<typeof servicesRoutes>[0]) {
  fakeApi({ ...reviewRoutes(), ...servicesRoutes(services) });
  render(<App />);
}

describe("the header's services chip", () => {
  it("is red with one red service and one orange, counts and names the red one and leads to Réglages", async () => {
    openReview({ sites: [aService()], agents: [orange], mailbox: [red] });

    const name = fill(fr["services.red"], { names: fr["settings.service.mailbox"] });
    const link = await header().findByRole("link", { name });
    expect(link.textContent).toBe(fill(fr["services.chip.red"], { n: "1" }));
    expect(link.getAttribute("title")).toBe(name);
    expect(link.querySelector(".bg-minus")).not.toBeNull();
    expect(scanBar().queryByRole("link")).toBeNull();
    expect(scanBar().queryByText(/en panne/i)).toBeNull();

    fireEvent.click(link);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(fr["nav.settings"]);
    expect(screen.getByRole("button", { name: fr["nav.settings"] }).getAttribute("aria-current")).toBe("page");
  });

  it("counts and names every red service", async () => {
    const extension = aService({ id: "extension", state: "red", hint: "Install the extension." });
    openReview({ sites: [extension], mailbox: [red] });

    const names = `${fr["settings.service.extension"]}, ${fr["settings.service.mailbox"]}`;
    const link = await header().findByRole("link", { name: fill(fr["services.red"], { names }) });
    expect(link.textContent).toBe(fill(fr["services.chip.red"], { n: "2" }));
  });

  it("is orange with only an orange service, counts it in the singular and leads to Réglages", async () => {
    openReview({ sites: [aService()], agents: [orange] });

    const name = fill(fr["services.orange"], { names: fr["settings.service.triage"] });
    const link = await header().findByRole("link", { name });
    expect(link.textContent).toBe(fill(fr["services.chip.orange.one"], { n: "1" }));
    expect(link.querySelector(".bg-unknown")).not.toBeNull();
  });

  it("counts several orange services in the plural", async () => {
    openReview({ sites: [aService({ state: "orange" })], agents: [orange] });

    const link = await header().findByRole("link");
    expect(link.textContent).toBe(fill(fr["services.chip.orange.other"], { n: "2" }));
  });

  it("is a green dot alone, not a link, when no service is red or orange", async () => {
    openReview({ sites: [aService()], agents: [aService({ id: "triage" })] });

    const dot = await header().findByRole("img", { name: fr["services.green"] });
    expect(dot.className).toContain("bg-plus");
    expect(dot.textContent).toBe("");
    expect(header().queryByRole("link")).toBeNull();
  });

  it("shows no dot when the services' state is unknown", async () => {
    fakeApi({ ...reviewRoutes(), "GET /api/services": new Response("boom", { status: 500 }) });
    render(<App />);

    await scanBar().findByText(/Dernière recherche/);
    expect(header().queryByRole("img", { name: fr["services.green"] })).toBeNull();
    expect(header().queryByRole("link")).toBeNull();
  });
});

describe("the scan bar", () => {
  it("shows the last and the next scan on the action row, and no LinkedIn reads of the day", async () => {
    fakeApi({ ...reviewRoutes(), ...servicesRoutes() });
    render(<App />);

    const last = await scanBar().findByText(/^Dernière recherche /);
    expect(last.textContent).toMatch(/^Dernière recherche .+ · prochaine .+$/);
    expect(scanBar().queryByText(/Lectures LinkedIn/)).toBeNull();
    expect(scanBar().queryByText(/Nouvelles offres/)).toBeNull();
  });

  it("shows how many of the new LinkedIn offers a running scan went through", async () => {
    const scan = aScan({ running: true, linkedin_progress: { read: 12, total: 130 } });
    fakeApi({ ...reviewRoutes({}, scan), ...servicesRoutes() });
    render(<App />);

    expect(await scanBar().findByText("LinkedIn 12 / 130")).toBeTruthy();
  });

  it("shows no LinkedIn progress when no scan reads LinkedIn", async () => {
    fakeApi({ ...reviewRoutes(), ...servicesRoutes() });
    render(<App />);

    await scanBar().findByText(/^Dernière recherche /);
    expect(scanBar().queryByText(/LinkedIn \d+ \/ \d+/)).toBeNull();
  });
});

describe("the offers of the last 24 hours", () => {
  it("are counted, by service with its logo, and by hand as Ajoutée", async () => {
    const scan = aScan({ last_24h: { alert_linkedin: 12, manual: 1 } });
    fakeApi({ ...reviewRoutes({}, scan), ...servicesRoutes() });
    render(<App />);

    expect(await scanBar().findByText(fill(fr["scan.last_24h.other"], { n: "13" }))).toBeTruthy();
    const linkedin = scanBar().getByText("LinkedIn 12").closest("li");
    expect(linkedin?.querySelector("[data-logo='alert_linkedin']")).not.toBeNull();
    const byHand = scanBar().getByText(`${fr["scan.manual"]} 1`).closest("li");
    expect(byHand?.querySelector("[data-logo='manual']")).not.toBeNull();
    expect(scanBar().queryByText(/Lectures LinkedIn aujourd’hui/)).toBeNull();
  });

  it("show only the sources that have offers", async () => {
    const scan = aScan({ last_24h: { alert_linkedin: 1, alert_wttj: 0 } });
    fakeApi({ ...reviewRoutes({}, scan), ...servicesRoutes() });
    render(<App />);

    expect(await scanBar().findByText(fill(fr["scan.last_24h.one"], { n: "1" }))).toBeTruthy();
    expect(scanBar().getByText("LinkedIn 1")).toBeTruthy();
    expect(scanBar().queryByText(/Welcome to the Jungle/)).toBeNull();
  });
});

describe("the scan bar while a scan runs", () => {
  it("outlines the active service thickly, the others used thinly, and an Outlook that has no count", async () => {
    let running = true;
    fakeApi({
      ...reviewRoutes(),
      "GET /api/scan": () =>
        aScan(
          running
            ? {
                running: true,
                active_service: "alert_linkedin",
                services_used: ["mailbox", "alert_linkedin"],
                last_24h: { alert_linkedin: 12 },
              }
            : { last_24h: { alert_linkedin: 12 } },
        ),
      ...servicesRoutes(),
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<App />);

    const linkedin = (await scanBar().findByText("LinkedIn 12")).closest("li");
    expect(linkedin?.getAttribute("data-use")).toBe("active");
    expect(linkedin?.className).toContain("animate-pulse");
    const outlook = scanBar().getByText(fr["settings.service.mailbox"]).closest("li");
    expect(outlook?.getAttribute("data-use")).toBe("used");
    expect(outlook?.className).not.toContain("animate-pulse");

    running = false;
    await act(() => vi.advanceTimersByTimeAsync(3_000));

    expect(scanBar().queryByText(fr["settings.service.mailbox"])).toBeNull();
    const done = scanBar().getByText("LinkedIn 12").closest("li");
    expect(done?.getAttribute("data-use")).toBeNull();
  });

  it("shows a Stop button and the logos of the services used", async () => {
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

  it("hides the Stop button and the services used when no scan is running", async () => {
    fakeApi({ ...reviewRoutes(), ...servicesRoutes() });
    render(<App />);

    await scanBar().findByText(/^Dernière recherche /);
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

describe("the header's chip stays fresh", () => {
  const servicesCalls = (calls: { method: string; path: string }[]) =>
    calls.filter((call) => call.method === "GET" && call.path === "/api/services").length;

  /** The services answer green first, then red: a service that failed since the page opened. */
  async function openGreenThenRed(scan: () => ReturnType<typeof aScan> = () => aScan()) {
    let asked = 0;
    const calls = fakeApi({
      ...reviewRoutes(),
      "GET /api/scan": scan,
      "GET /api/services": () =>
        servicesRoutes(asked++ === 0 ? { sites: [aService()] } : { mailbox: [red] })["GET /api/services"],
    });
    render(<App />);
    await header().findByRole("img", { name: fr["services.green"] });
    return calls;
  }

  it("fetches the services again when the window regains focus", async () => {
    const calls = await openGreenThenRed();
    expect(servicesCalls(calls)).toBe(1);

    fireEvent.focus(window);

    const name = fill(fr["services.red"], { names: fr["settings.service.mailbox"] });
    expect(await header().findByRole("link", { name })).toBeTruthy();
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

  it("fetches the services again when a scan ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let running = true;
    const calls = await openGreenThenRed(() => aScan({ running }));
    expect(servicesCalls(calls)).toBe(1);

    running = false;
    await act(() => vi.advanceTimersByTimeAsync(3_000));

    const name = fill(fr["services.red"], { names: fr["settings.service.mailbox"] });
    expect(await header().findByRole("link", { name })).toBeTruthy();
    expect(servicesCalls(calls)).toBe(2);
  });
});
