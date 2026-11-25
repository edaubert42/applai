// The ApplAI extension's form reader (extension/forms.js) on a fictional form. The extension has
// no DOM library, so the frontend's jsdom runs this test.
import { afterEach, expect, test, vi } from "vitest";

import { readForm, readText } from "../../extension/forms.js";
import { hideOverlay, showOverlay } from "../../extension/overlay.js";

const FORM = `
  <form action="/apply" method="post">
    <input type="hidden" name="token" value="x">
    <label for="name">Nom complet *</label><input id="name" name="name" required>
    <label>E-mail <input type="email" name="email" aria-required="true"></label>
    <input type="tel" name="phone" aria-label="Téléphone">
    <label for="cv">CV</label><input id="cv" type="file" name="cv" required>
    <label>Disponibilité
      <select name="start"><option value="">Choisir</option><option>Immédiate</option><option>Sous 3 mois</option></select>
    </label>
    <fieldset><legend>Contrat souhaité</legend>
      <label><input type="radio" name="contract" value="cdi" required> CDI</label>
      <label><input type="radio" name="contract" value="freelance"> Freelance</label>
    </fieldset>
    <label for="why">Pourquoi Pivoine Analytics ?</label><textarea id="why" required></textarea>
    <label><input type="checkbox" name="consent"> J'accepte la politique de confidentialité</label>
    <button type="submit">Envoyer</button>
  </form>`;

afterEach(() => vi.restoreAllMocks());

test("it reads each control's label, type, whether it is required, and its options", () => {
  document.body.innerHTML = FORM;

  expect(readForm(document)).toEqual([
    { label: "Nom complet", type: "text", required: true, options: [] },
    { label: "E-mail", type: "email", required: true, options: [] },
    { label: "Téléphone", type: "phone", required: false, options: [] },
    { label: "CV", type: "file", required: true, options: [] },
    { label: "Disponibilité", type: "select", required: false, options: ["Immédiate", "Sous 3 mois"] },
    { label: "Contrat souhaité", type: "radio", required: true, options: ["CDI", "Freelance"] },
    { label: "Pourquoi Pivoine Analytics ?", type: "textarea", required: true, options: [] },
    { label: "J'accepte la politique de confidentialité", type: "checkbox", required: false, options: [] },
  ]);
});

test("it lists only what ApplAI accepts: a label of 1000 characters at most, never empty", () => {
  document.body.innerHTML = `<label for="a">*</label><input id="a" name="code">
    <label for="b">${"x".repeat(1200)}</label><input id="b" name="long">`;

  expect(readForm(document).map((field) => field.label.length)).toEqual(["code".length, 1000]);
});

test("it never dispatches an input, click or submit event, and runs alone as Chrome sends it", () => {
  document.body.innerHTML = FORM;
  const events: string[] = [];
  for (const type of ["input", "change", "click", "submit", "keydown", "focus", "focusin"]) {
    document.addEventListener(type, () => events.push(type), true);
  }
  const acts = [
    vi.spyOn(HTMLElement.prototype, "click"),
    vi.spyOn(HTMLElement.prototype, "focus"),
    vi.spyOn(HTMLFormElement.prototype, "submit"),
    vi.spyOn(HTMLFormElement.prototype, "requestSubmit"),
    vi.spyOn(EventTarget.prototype, "dispatchEvent"),
  ];
  // Chrome injects the function's source alone, without its module: no helper outside it.
  const injected = new Function(`return (${readForm.toString()})`)() as typeof readForm;

  expect(injected()).toHaveLength(8); // the page's own document, as in Chrome
  expect(events).toEqual([]);
  for (const act of acts) expect(act).not.toHaveBeenCalled();
  expect((document.querySelector("#name") as HTMLInputElement).value).toBe("");
});

// A form drawn by a custom widget: no standard control, part of it in a shadow DOM.
const WIDGET = `
  <h1>Data engineer chez Pivoine Analytics</h1>
  <script>window.tracking = "x";</script>
  <div role="form">
    <div class="question">Nom complet *</div><div role="textbox" contenteditable aria-label="Votre nom"></div>
    <div hidden>Texte masqué</div><div aria-hidden="true">Décor</div>
    <pivoine-upload></pivoine-upload>
    <div role="button">Envoyer</div>
  </div>`;

test("with no control, it reads the page's visible text, its shadow DOM's too, and nothing else", () => {
  document.body.innerHTML = WIDGET;
  const host = document.querySelector("pivoine-upload") as HTMLElement;
  host.attachShadow({ mode: "open" }).innerHTML = "<label>CV *</label><span>PDF, 2 Mo maximum</span>";
  const events: string[] = [];
  for (const type of ["input", "change", "click", "submit", "keydown", "focus", "focusin"]) {
    document.addEventListener(type, () => events.push(type), true);
  }
  const acts = [
    vi.spyOn(HTMLElement.prototype, "click"),
    vi.spyOn(HTMLElement.prototype, "focus"),
    vi.spyOn(EventTarget.prototype, "dispatchEvent"),
  ];
  const injected = new Function(`return (${readText.toString()})`)() as typeof readText;

  expect(readForm(document)).toEqual([]);
  expect(injected()).toBe(
    "Data engineer chez Pivoine Analytics Nom complet * Votre nom CV * PDF, 2 Mo maximum Envoyer",
  );
  expect(events).toEqual([]);
  for (const act of acts) expect(act).not.toHaveBeenCalled();
});

test("it sends at most what ApplAI accepts: 20000 characters", () => {
  document.body.innerHTML = `<p>${"Pivoine ".repeat(4000)}</p>`;

  expect(readText(document)).toHaveLength(20000);
});

// The frame the extension draws on a tab it uses (extension/overlay.js).
test("a page with the overlay gives the same fields and text as without it", () => {
  document.body.innerHTML = FORM;
  const [fields, text] = [readForm(document), readText(document)];

  showOverlay(document);

  expect(document.getElementById("applai-overlay")).not.toBeNull();
  expect(readForm(document)).toEqual(fields);
  expect(readForm(document)).toHaveLength(8);
  expect(readText(document)).toBe(text);
  expect(readText(document)).not.toContain("ApplAI");
  hideOverlay(document);
});

test("the overlay is a 4 px green frame and a label that catch no click, and one call removes it", () => {
  document.body.innerHTML = FORM;
  const attach = vi.spyOn(Element.prototype, "attachShadow");

  showOverlay(document);
  showOverlay(document); // asked again: still one frame

  const host = document.getElementById("applai-overlay") as HTMLElement;
  const shadow = attach.mock.results.at(-1)?.value as ShadowRoot;
  const frame = shadow.children[0] as HTMLElement;
  const label = shadow.children[1] as HTMLElement;
  expect(document.querySelectorAll("#applai-overlay")).toHaveLength(1);
  expect(host.style.position).toBe("fixed");
  expect(host.style.pointerEvents).toBe("none");
  expect(host.shadowRoot).toBeNull(); // closed: the page's scripts do not reach in
  expect(frame.style.pointerEvents).toBe("none");
  expect(frame.style.borderWidth).toBe("4px");
  expect(frame.style.borderColor).toBe("rgb(30, 142, 62)"); // #1e8e3e
  expect(label.textContent).toBe("ApplAI is using this tab");
  expect(label.style.pointerEvents).toBe("none");
  expect(label.style.top).toBe("4px");
  expect(label.style.right).toBe("4px");
  expect(document.body.textContent).not.toContain("ApplAI");

  hideOverlay(document);

  expect(document.getElementById("applai-overlay")).toBeNull();
});

test("the overlay runs alone as Chrome sends it", () => {
  document.body.innerHTML = FORM;
  const show = new Function(`return (${showOverlay.toString()})`)() as typeof showOverlay;
  const hide = new Function(`return (${hideOverlay.toString()})`)() as typeof hideOverlay;

  show();
  expect(document.getElementById("applai-overlay")).not.toBeNull();
  hide();
  expect(document.getElementById("applai-overlay")).toBeNull();
});
