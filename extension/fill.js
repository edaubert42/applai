// The form filler the extension runs in a form's tab (chrome.scripting.executeScript): applies
// ApplAI's fill plan, attaches files, blocks every submission in code (ADR 0003), and reads back
// each field's actual state. Chrome injects this function alone, so every helper lives inside it.
export function fillForm(actions, files = {}, root = document) {
  const TYPES = { email: "email", tel: "phone", file: "file", radio: "radio", checkbox: "checkbox" };
  const SKIPPED = ["hidden", "submit", "button", "reset", "image"];
  const MARK = /\s*[*✱]\s*$/, MAX_LABEL = 1000, MAX_TEXT = 20000;
  const win = root.defaultView ?? globalThis;
  const words = (n) => (n?.textContent ?? "").replace(/\s+/g, " ").trim();
  const ownWords = (label) => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll("input, select, textarea").forEach((c) => c.remove());
    return words(copy);
  };
  const named = (n) => {
    const ids = n?.getAttribute("aria-labelledby")?.split(/\s+/) ?? [];
    return n?.getAttribute("aria-label")?.trim() || ids.map((id) => words(root.getElementById(id))).join(" ").trim();
  };
  const labelOf = (c) =>
    named(c) || [...(c.labels ?? [])].map(ownWords).join(" ").trim() ||
    c.getAttribute("placeholder")?.trim() || c.name || c.type;
  const groupLabelOf = (c) =>
    words(c.closest("fieldset")?.querySelector("legend")) ||
    named(c.closest('[role="radiogroup"], [role="group"]')) || c.name;
  const isButton = (c) =>
    !c || c.tagName === "BUTTON" || c.getAttribute?.("role") === "button" || SKIPPED.includes(c.type);
  const kindOf = (c) => (c.tagName === "INPUT" ? (TYPES[c.type] ?? "text") : c.tagName.toLowerCase());
  const keyOf = (c) => `${kindOf(c)} ${c.name}`;
  const clean = (label, c) => (label.replace(MARK, "").trim() || c.name || c.type).slice(0, MAX_LABEL);

  const controls = [...root.querySelectorAll("input, select, textarea")].filter((c) => !c.disabled && !isButton(c));
  const sharing = new Map();
  for (const c of controls) sharing.set(keyOf(c), (sharing.get(keyOf(c)) ?? 0) + 1);
  const entries = [], groups = new Map();
  for (const c of controls) {
    const type = kindOf(c), grouped = ["radio", "checkbox"].includes(type) && c.name && sharing.get(keyOf(c)) > 1;
    const label = clean(grouped ? groupLabelOf(c) : labelOf(c), c);
    if (!grouped) { entries.push({ label, type, grouped: false, node: c, options: [] }); continue; }
    if (!groups.has(keyOf(c))) {
      const g = { label, type, grouped: true, node: null, options: [] };
      groups.set(keyOf(c), g);
      entries.push(g);
    }
    groups.get(keyOf(c)).options.push({ label: clean(labelOf(c), c), node: c });
  }

  const notify = (el) => {
    const Ev = win.Event ?? Event;
    el.dispatchEvent(new Ev("input", { bubbles: true }));
    el.dispatchEvent(new Ev("change", { bubbles: true }));
  };
  const setProp = (el, prop, value) => {
    const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), prop)?.set;
    if (set) set.call(el, value); else el[prop] = value;
    notify(el);
  };
  const attach = (el, payload) => {
    const raw = atob(payload.base64), bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const file = new (win.File ?? File)([bytes], payload.name, { type: payload.type || "application/pdf" });
    const DT = win.DataTransfer ?? globalThis.DataTransfer;
    if (typeof DT === "function") { const dt = new DT(); dt.items.add(file); el.files = dt.files; }
    else Object.defineProperty(el, "files", { value: [file], configurable: true });
    notify(el);
  };

  // Hard submit blocking enforced in code while filling runs (ADR 0003).
  const block = (event) => { event.preventDefault(); event.stopImmediatePropagation(); };
  const FormProto = (win.HTMLFormElement ?? HTMLFormElement).prototype;
  const origSubmit = FormProto.submit, origRequest = FormProto.requestSubmit;
  win.addEventListener("submit", block, true);
  root.addEventListener("submit", block, true);
  FormProto.submit = () => {};
  FormProto.requestSubmit = () => {};

  const unfilled = new Map(), extraUnfilled = [];
  try {
    for (const action of actions ?? []) {
      const entry = entries[action.field];
      if (!entry || entry.label !== action.label) {
        if (entry) unfilled.set(action.field, "changed");
        else extraUnfilled.push({ field: action.field, unfilled: "changed" });
        continue;
      }
      if (action.kind === "fill" && ["text", "email", "phone", "textarea"].includes(entry.type)) {
        if (!isButton(entry.node)) setProp(entry.node, "value", String(action.value ?? ""));
      } else if (action.kind === "choose") {
        const wanted = Array.isArray(action.value) ? action.value : [String(action.value ?? "")];
        if (entry.type === "select") {
          const opt = [...entry.node.options].find((o) => o.value && words(o) === wanted[0]);
          if (opt) setProp(entry.node, "value", opt.value); else unfilled.set(action.field, "option_missing");
        } else if (entry.grouped) {
          if (wanted.length && !entry.options.some((o) => wanted.includes(o.label))) unfilled.set(action.field, "option_missing");
          else for (const o of entry.options) if (!isButton(o.node)) setProp(o.node, "checked", wanted.includes(o.label));
        } else if (entry.type === "checkbox") {
          if (!isButton(entry.node)) setProp(entry.node, "checked", wanted.length > 0);
        } else unfilled.set(action.field, "wrong_type");
      } else if (action.kind === "attach" && entry.type === "file") {
        const payload = files?.[String(action.value)];
        if (!payload?.base64) unfilled.set(action.field, "no_file");
        else if (!isButton(entry.node)) attach(entry.node, payload);
      } else unfilled.set(action.field, "wrong_type");
    }
  } finally {
    win.removeEventListener("submit", block, true);
    root.removeEventListener("submit", block, true);
    FormProto.submit = origSubmit;
    FormProto.requestSubmit = origRequest;
  }

  return [
    ...entries.map((entry, index) => {
      if (unfilled.has(index)) return { field: index, unfilled: unfilled.get(index) };
      if (entry.type === "file") return { field: index, file: entry.node.files?.[0]?.name?.slice(0, MAX_LABEL) ?? null };
      if (entry.type === "select") {
        const opt = entry.node.selectedOptions?.[0] ?? entry.node.options?.[entry.node.selectedIndex];
        return { field: index, value: opt?.value ? words(opt).slice(0, MAX_TEXT) : null };
      }
      if (entry.grouped) {
        const checked = entry.options.filter((o) => o.node.checked).map((o) => o.label.slice(0, MAX_TEXT));
        return { field: index, value: entry.type === "radio" ? (checked[0] ?? null) : checked };
      }
      if (entry.type === "checkbox") return { field: index, value: entry.node.checked ? entry.label.slice(0, MAX_TEXT) : null };
      return { field: index, value: String(entry.node.value ?? "").slice(0, MAX_TEXT) };
    }),
    ...extraUnfilled,
  ];
}
