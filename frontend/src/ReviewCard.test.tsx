import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { compile } from "tailwindcss";
import { describe, expect, it } from "vitest";
import { Logo } from "./ReviewCard";

// Vitest empties CSS imports, so the stylesheets are read from disk; tests run from frontend/.
const tailwind = createRequire(resolve("package.json")).resolve("tailwindcss/index.css");

/** The CSS that the app's own stylesheet generates for these classes. */
async function cssFor(classes: string[]): Promise<string> {
  const compiler = await compile(await readFile(resolve("src/index.css"), "utf8"), {
    loadStylesheet: async () => ({ path: tailwind, base: "", content: await readFile(tailwind, "utf8") }),
  });
  return compiler.build(classes);
}

describe("a company's logo", () => {
  it("sits on a white tile, so a transparent logo reads on the dark card", async () => {
    const { container } = render(<Logo card={{ company: "Acme Robotics", logo: "acme-robotics.png" }} className="" />);
    const classes = [...(container.querySelector("img")?.classList ?? [])];

    expect(await cssFor(classes)).toMatch(/background-color:\s*#fff\b/);
  });
});
