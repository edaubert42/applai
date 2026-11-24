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
  const win = root.defaultView ?? globalThis;
  const before = String(win.location?.href ?? "");
  const CONFIRMED = /\b(merci|thank\s+you|confirmation|confirmed|confirm[ée]e?|candidature\s+(?:a\s+(?:bien\s+)?[ée]t[ée]\s+)?envoy[ée]e|application\s+(?:has\s+been\s+)?(?:submitted|received))\b/i;
  button.click();
  const after = String(win.location?.href ?? "");
  const text = (root.body?.textContent ?? root.textContent ?? "").replace(/\s+/g, " ");
  return { ok: true, confirmed: Boolean((before && after && after !== before) || CONFIRMED.test(text)) };
}

export function isConfirmed(initialUrl = "", root = document) {
  const win = root.defaultView ?? globalThis;
  const href = String(win.location?.href ?? "");
  const CONFIRMED = /\b(merci|thank\s+you|confirmation|confirmed|confirm[ée]e?|candidature\s+(?:a\s+(?:bien\s+)?[ée]t[ée]\s+)?envoy[ée]e|application\s+(?:has\s+been\s+)?(?:submitted|received))\b/i;
  const text = (root.body?.textContent ?? root.textContent ?? "").replace(/\s+/g, " ");
  return Boolean((initialUrl && href && href !== initialUrl) || CONFIRMED.test(text));
}
