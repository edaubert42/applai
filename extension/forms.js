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

// The visible text of a form's page, sent when readForm finds no control (a custom widget, a
// shadow DOM) so ApplAI's form_capture agent reads the fields in it. It only reads, as readForm,
// and Chrome sends it alone too. Tested on a fictional page: frontend/src/extension-forms.test.ts.
export function readText(root = document) {
  const MAX_TEXT = 20000; // what ApplAI accepts (applai.browser.MAX_TEXT)
  const SKIPPED = ["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"];
  const parts = [];
  let size = 0;
  const add = (words) => {
    const text = (words ?? "").replace(/\s+/g, " ").trim();
    if (text) parts.push(text);
    size += text.length + 1;
  };
  const read = (node) => {
    for (const child of node.childNodes) {
      if (size > MAX_TEXT) return;
      if (child.nodeType === 3) add(child.data); // text
      if (child.nodeType !== 1) continue; // not an element
      const hidden = child.hidden || child.getAttribute("aria-hidden") === "true" ||
        child.checkVisibility?.() === false; // jsdom has no checkVisibility
      if (hidden || SKIPPED.includes(child.tagName.toUpperCase())) continue;
      add(child.getAttribute("aria-label")); // a custom widget's name is often only there
      add(child.getAttribute("placeholder"));
      if (child.shadowRoot) read(child.shadowRoot); // an open one; a closed one is not readable
      read(child);
    }
  };
  read(root.body ?? root);
  const text = parts.join(" ").slice(0, MAX_TEXT);
  return /[\ud800-\udbff]$/.test(text) ? text.slice(0, -1) : text; // no half of a character
}

// A page's HTML as its scripts drew it: Google draws its jobs with JavaScript, so a fetch of its
// page never shows them (background.js reads Google's pages in a tab with this).
export function readHtml(root = document) {
  return root.documentElement.outerHTML;
}
