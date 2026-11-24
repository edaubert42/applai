// The ApplAI extension's form filler (extension/fill.js) on a fictional form.
import { afterEach, expect, test, vi } from "vitest";

import { fillForm } from "../../extension/fill.js";
import { readForm } from "../../extension/forms.js";

const FORM = `
  <form action="/apply" method="post">
    <input type="hidden" name="token" value="x">
    <label for="name">Nom complet *</label><input id="name" name="name" required>
    <label>E-mail <input type="email" name="email" aria-required="true"></label>
    <input type="tel" name="phone" aria-label="Téléphone">
    <label for="cv">CV</label><input id="cv" type="file" name="cv" required>
    <label>Disponibilité
      <select name="start"><option value="">Choisir</option><option value="now">Immédiate</option><option value="3m">Sous 3 mois</option></select>
    </label>
    <fieldset><legend>Contrat souhaité</legend>
      <label><input type="radio" name="contract" value="cdi" required> CDI</label>
      <label><input type="radio" name="contract" value="freelance"> Freelance</label>
    </fieldset>
    <fieldset><legend>Langues</legend>
      <label><input type="checkbox" name="lang" value="fr"> Français</label>
      <label><input type="checkbox" name="lang" value="en"> Anglais</label>
    </fieldset>
    <label for="why">Pourquoi Pivoine Analytics ?</label><textarea id="why" required></textarea>
    <label><input type="checkbox" name="consent"> J'accepte la politique de confidentialité</label>
    <input type="submit" value="Envoyer">
    <button type="submit">Postuler maintenant</button>
    <div role="button">Envoyer le formulaire</div>
  </form>`;

afterEach(() => vi.restoreAllMocks());

test("fillForm fills every control type, lines up with readForm, reports changed labels, and runs alone", () => {
  document.body.innerHTML = FORM;
  expect(readForm(document).map((f) => f.label)).toEqual([
    "Nom complet", "E-mail", "Téléphone", "CV", "Disponibilité",
    "Contrat souhaité", "Langues", "Pourquoi Pivoine Analytics ?",
    "J'accepte la politique de confidentialité",
  ]);
  const seen: string[] = [];
  document.addEventListener("input", (e) => seen.push(`input:${(e.target as HTMLElement).tagName}`), true);
  document.addEventListener("change", (e) => seen.push(`change:${(e.target as HTMLElement).tagName}`), true);

  const actions = [
    { field: 0, label: "Nom complet", kind: "fill" as const, value: "Camille Moreau" },
    { field: 1, label: "E-mail", kind: "fill" as const, value: "camille.moreau@example.org" },
    { field: 2, label: "Libellé modifié", kind: "fill" as const, value: "+33 6 00 00 00 00" },
    { field: 3, label: "CV", kind: "attach" as const, value: "cv" },
    { field: 4, label: "Disponibilité", kind: "choose" as const, value: ["Immédiate"] },
    { field: 5, label: "Contrat souhaité", kind: "choose" as const, value: ["CDI"] },
    { field: 6, label: "Langues", kind: "choose" as const, value: ["Français", "Anglais"] },
    { field: 7, label: "Pourquoi Pivoine Analytics ?", kind: "fill" as const, value: "Mission data solide." },
    { field: 8, label: "J'accepte la politique de confidentialité", kind: "choose" as const, value: ["J'accepte la politique de confidentialité"] },
  ];
  const files = { cv: { name: "cv-camille.pdf", type: "application/pdf", base64: btoa("%PDF-1.4\n") } };
  const injected = new Function(`return (${fillForm.toString()})`)() as typeof fillForm;
  const filled = injected(actions, files);

  expect((document.querySelector('input[type="tel"]') as HTMLInputElement).value).toBe("");
  expect((document.querySelector("#cv") as HTMLInputElement).files?.[0]?.name).toBe("cv-camille.pdf");
  expect(seen).toContain("input:INPUT");
  expect(seen).toContain("change:SELECT");
  expect(filled).toEqual([
    { field: 0, value: "Camille Moreau" },
    { field: 1, value: "camille.moreau@example.org" },
    { field: 2, unfilled: "changed" },
    { field: 3, file: "cv-camille.pdf" },
    { field: 4, value: "Immédiate" },
    { field: 5, value: "CDI" },
    { field: 6, value: ["Français", "Anglais"] },
    { field: 7, value: "Mission data solide." },
    { field: 8, value: "J'accepte la politique de confidentialité" },
  ]);
});

test("fillForm never clicks submit/button controls and cancels every submit attempt during filling", () => {
  document.body.innerHTML = FORM;
  const form = document.querySelector("form") as HTMLFormElement;
  let submitted = 0;
  form.addEventListener("submit", (e) => { e.preventDefault(); submitted += 1; });
  document.querySelector("#name")?.addEventListener("input", () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.submit();
    form.requestSubmit();
  });
  const clickSpy = vi.spyOn(HTMLElement.prototype, "click");
  const submitSpy = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => { submitted += 1; });
  const requestSpy = vi.spyOn(HTMLFormElement.prototype, "requestSubmit").mockImplementation(() => { submitted += 1; });

  const filled = fillForm(
    [
      { field: 0, label: "Nom complet", kind: "fill", value: "Camille Moreau" },
      { field: 9, label: "Postuler maintenant", kind: "fill", value: "click" },
    ],
    {},
    document,
  );

  expect(submitted).toBe(0);
  expect(clickSpy).not.toHaveBeenCalled();
  expect(submitSpy).not.toHaveBeenCalled();
  expect(requestSpy).not.toHaveBeenCalled();
  expect(filled.find((f) => f.field === 9)).toEqual({ field: 9, unfilled: "changed" });
});
