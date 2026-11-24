// The ApplAI extension's form reader (extension/forms.js) on a fictional form. The extension has
// no DOM library, so the frontend's jsdom runs this test.
import { afterEach, expect, test, vi } from "vitest";

import { readForm, readText } from "../../extension/forms.js";

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
