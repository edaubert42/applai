/**
 * The design tokens of docs/design.md. Each token becomes a CSS variable `--ui-<name>`
 * on <html>, which src/index.css hands to Tailwind (`bg-surface`, `text-muted`…).
 */

const dark = {
  background: "#1C1D22",
  surface: "#26282F",
  raised: "#2C2F37",
  border: "#3D414B",
  outline: "#4A4E58",
  text: "#E8EAED",
  muted: "#9AA0A6",
  primary: "#1A73E8",
  "on-primary": "#FFFFFF",
  link: "#7DB3FF",
  plus: "#7EE2A0",
  "plus-bg": "#1D2E24",
  "plus-border": "#2E7D46",
  minus: "#FF9A92",
  "minus-bg": "#33201F",
  "minus-border": "#A3403B",
  unknown: "#FFC857",
  "unknown-bg": "#1C1D22",
  "unknown-border": "#9A6B12",
};

export type Token = keyof typeof dark;
export type ThemeName = "dark" | "light";

/** The light theme keeps the dark theme's hues. */
const light: Record<Token, string> = {
  background: "#F6F7F9",
  surface: "#FFFFFF",
  raised: "#EEF0F3",
  border: "#D5D8DE",
  outline: "#B4B9C2",
  text: "#1C1D22",
  muted: "#5A5F66",
  primary: "#1A73E8",
  "on-primary": "#FFFFFF",
  link: "#1557B0",
  plus: "#1E6B3A",
  "plus-bg": "#E3F5EA",
  "plus-border": "#7EC99A",
  minus: "#A8322C",
  "minus-bg": "#FCE9E7",
  "minus-border": "#E8A39D",
  unknown: "#7A5200",
  "unknown-bg": "#FFF6E0",
  "unknown-border": "#E0B24F",
};

export const themes: Record<ThemeName, Record<Token, string>> = { dark, light };

/** Every text token, on every background it is drawn on: each pair needs 4.5:1. */
export const textPairs: [Token, Token][] = [
  ["text", "background"],
  ["text", "surface"],
  ["text", "raised"],
  ["muted", "background"],
  ["muted", "surface"],
  ["muted", "raised"],
  ["link", "background"],
  ["link", "surface"],
  ["link", "raised"],
  ["on-primary", "primary"],
  ["plus", "plus-bg"],
  ["minus", "minus-bg"],
  ["unknown", "unknown-bg"],
];

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const channel = parseInt(hex.slice(i, i + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG 2 contrast ratio of two `#RRGGBB` colors, from 1 to 21. */
export function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

export function applyTheme(name: ThemeName, root: HTMLElement): void {
  root.dataset.theme = name;
  root.style.colorScheme = name;
  for (const [token, value] of Object.entries(themes[name])) {
    root.style.setProperty(`--ui-${token}`, value);
  }
}
