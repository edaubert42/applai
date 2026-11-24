// The form reader the extension runs in a form's tab (chrome.scripting.executeScript): it lists
// the form's controls, each with its label, type, whether it is required, and its options. It
// only reads: no typing, no click, no event, no submit. Chrome sends its source alone into the
// page, so everything it uses is inside it. Tested on a fictional form: frontend/src/extension-forms.test.ts.
export function readForm(root = document) {
  const TYPES = { email: "email", tel: "phone", file: "file", radio: "radio", checkbox: "checkbox" };
  const SKIPPED = ["hidden", "submit", "button", "reset", "image"];
  const MARK = /\s*[*✱]\s*$/; // a required field's mark at the end of its label
  const MAX_LABEL = 1000, MAX_OPTIONS = 500; // what ApplAI accepts (applai.browser.FormField)
  const words = (node) => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
  const ownWords = (label) => {
    const copy = label.cloneNode(true); // detached: the page sees nothing
    copy.querySelectorAll("input, select, textarea").forEach((control) => control.remove());
    return words(copy);
  };
  const named = (node) => {
    const ids = node?.getAttribute("aria-labelledby")?.split(/\s+/) ?? [];
    const by = ids.map((id) => words(root.getElementById(id))).join(" ").trim();
    return node?.getAttribute("aria-label")?.trim() || by;
  };
  const labelOf = (control) =>
    named(control) || [...(control.labels ?? [])].map(ownWords).join(" ").trim() ||
    control.getAttribute("placeholder")?.trim() || control.name || control.type;
  const groupLabelOf = (control) =>
    words(control.closest("fieldset")?.querySelector("legend")) ||
    named(control.closest('[role="radiogroup"], [role="group"]')) || control.name;

  const controls = [...root.querySelectorAll("input, select, textarea")].filter(
    (control) => !control.disabled && !SKIPPED.includes(control.type),
  );
  const kindOf = (control) =>
    control.tagName === "INPUT" ? (TYPES[control.type] ?? "text") : control.tagName.toLowerCase();
  const keyOf = (control) => `${kindOf(control)} ${control.name}`;
  const sharing = new Map(); // how many radios or checkboxes share each name
  for (const control of controls) sharing.set(keyOf(control), (sharing.get(keyOf(control)) ?? 0) + 1);

  const clean = (label, control) =>
    (label.replace(MARK, "").trim() || control.name || control.type).slice(0, MAX_LABEL);
  const fields = [];
  const groups = new Map();
  for (const control of controls) {
    const type = kindOf(control);
    const grouped = ["radio", "checkbox"].includes(type) && control.name && sharing.get(keyOf(control)) > 1;
    const label = grouped ? groupLabelOf(control) : labelOf(control);
    const required =
      control.required || control.getAttribute("aria-required") === "true" || MARK.test(label);
    if (!grouped) {
      const options = type === "select" ? [...control.options].filter((o) => o.value).map(words) : [];
      fields.push({ label: clean(label, control), type, required, options: options.slice(0, MAX_OPTIONS) });
      continue;
    }
    if (!groups.has(keyOf(control))) {
      groups.set(keyOf(control), { label: clean(label, control), type, required: false, options: [] });
      fields.push(groups.get(keyOf(control)));
    }
    const group = groups.get(keyOf(control));
    if (group.options.length < MAX_OPTIONS) group.options.push(clean(labelOf(control), control));
    group.required ||= required;
  }
  return fields;
}
