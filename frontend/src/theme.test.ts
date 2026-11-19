import { describe, expect, it } from "vitest";
import { contrast, textPairs, themes } from "./theme";

describe("the design tokens", () => {
  it("compute the WCAG contrast ratio", () => {
    expect(contrast("#000000", "#FFFFFF")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#777777")).toBeCloseTo(1, 5);
  });

  for (const [themeName, tokens] of Object.entries(themes)) {
    for (const [text, background] of textPairs) {
      it(`give ${text} on ${background} a contrast of at least 4.5:1 in the ${themeName} theme`, () => {
        expect(contrast(tokens[text], tokens[background])).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});
