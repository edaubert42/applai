// Submits an already-open form tab after per-application send authorization (ADR 0003 & ADR 0005).
// Chrome injects each function alone via executeScript, so every helper stays inside it.
export function submitForm(root = document) {
  const CAPTCHA = 'iframe[src*="captcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha';
  if (root.querySelector(CAPTCHA)) return { ok: false, reason: "captcha" };
  const SUBMIT = 'button[type="submit"], input[type="submit"], form button:not([type="button"]):not([type="reset"])';
  const visible = (el) =>
    !el.disabled && !el.hidden && el.getAttribute("aria-hidden") !== "true" && el.checkVisibility?.() !== false;
  const button = [...root.querySelectorAll(SUBMIT)].find(visible);
  if (!button) return { ok: false, reason: "no_button" };
  const beforeText = (root.body?.textContent ?? root.textContent ?? "").replace(/\s+/g, " ");
  button.click();
  return { ok: true, beforeText };
}

export function isConfirmed(initialUrl = "", beforeText = "", root = document) {
  const norm = (u) => {
    try { const p = new URL(u); return `${p.origin}${p.pathname.replace(/\/$/, "")}${p.search}`; }
    catch { return u; }
  };
  const href = String((root.defaultView ?? globalThis).location?.href ?? "");
  if (initialUrl && href && norm(href) !== norm(initialUrl)) return true;
  const CONFIRMED = /\b(merci|thank\s+you|candidature\s+(?:a\s+(?:bien\s+)?[ée]t[ée]\s+)?envoy[ée]e|application\s+(?:has\s+been\s+)?(?:submitted|received))\b/i;
  const afterText = (root.body?.textContent ?? root.textContent ?? "").replace(/\s+/g, " ");
  return CONFIRMED.test(afterText) && !CONFIRMED.test(beforeText);
}
