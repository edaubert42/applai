// The page turner the extension runs in a form's tab (chrome.scripting.executeScript) on a form
// spread over pages at one URL. Code alone decides what it clicks, never an agent (ADR 0003):
// a page with a button whose label holds a send word, or a review page, is the final page and
// nothing is clicked there ("final"). Else the first enabled button whose every label is on
// the allowlist is the next page's ("next"), clicked when click is true, with every submit it
// starts cancelled; none is a page that cannot be turned ("none"). Chrome injects this function
// alone, so everything it uses is inside it. Tested on fictional pages: frontend/src/extension-pages.test.ts.
export function turnPage(click = false, root = document) {
  const SEND = /\b(envoyer|soumettre|postuler|submit|apply)\b/;
  const REVIEW = /\b(relecture|review)\b/;
  const ALLOWED = ["suivant", "continuer", "enregistrer et continuer", "next", "continue", "save and continue"];
  const fold = (text) =>
    (text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z]+/g, " ").trim();
  const shown = (el) => !el.hidden && el.getAttribute("aria-hidden") !== "true" && el.checkVisibility?.() !== false;
  const labels = (el) =>
    [el.tagName === "INPUT" ? el.value : el.textContent, el.getAttribute("aria-label"), el.getAttribute("title")]
      .map(fold).filter(Boolean);
  const buttons = [...root.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]')].filter(shown);
  const headings = [...root.querySelectorAll('h1, h2, h3, [role="heading"]')].filter(shown);
  if (buttons.some((b) => labels(b).some((label) => SEND.test(label))) || headings.some((h) => REVIEW.test(fold(h.textContent)))) {
    return "final";
  }
  const next = buttons.find((b) => !b.disabled && b.getAttribute("aria-disabled") !== "true" &&
    labels(b).length && labels(b).every((label) => ALLOWED.includes(label)));
  if (!next) return "none";
  if (!click) return "next";
  // The site's own handlers still run; a submit the click starts never leaves the page (ADR 0003).
  const win = root.defaultView ?? globalThis;
  const FormProto = (win.HTMLFormElement ?? HTMLFormElement).prototype;
  const cancel = (event) => event.preventDefault();
  const submit = FormProto.submit;
  win.addEventListener("submit", cancel, true);
  FormProto.submit = () => {};
  try {
    next.click();
  } finally {
    win.removeEventListener("submit", cancel, true);
    FormProto.submit = submit;
  }
  return "next";
}
