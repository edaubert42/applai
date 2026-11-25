// The frame the extension draws around a tab while it reads, fills or submits there, so the
// candidate sees which tabs it uses. Chrome sends each function's source alone into the page, so
// everything a function uses is inside it. The frame is a fixed overlay with pointer-events: none
// in a closed shadow root: it catches no click, and neither form reader (forms.js), the text
// reader nor submit (send.js) can see it. Tested: frontend/src/extension-forms.test.ts.
export function showOverlay(root = document) {
  const ID = "applai-overlay";
  const GREEN = "#1e8e3e"; // the green of Chrome's tab groups
  root.getElementById(ID)?.remove(); // a second call replaces the first: never two frames
  const host = root.createElement("div");
  host.id = ID;
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position: fixed; top: 0; right: 0; bottom: 0; left: 0; " +
    "z-index: 2147483647; pointer-events: none; margin: 0; padding: 0; border: 0;";
  const frame = root.createElement("div");
  frame.style.cssText = "position: fixed; top: 0; right: 0; bottom: 0; left: 0; " +
    `box-sizing: border-box; border: 4px solid ${GREEN}; pointer-events: none;`;
  const label = root.createElement("div");
  label.textContent = "ApplAI is using this tab";
  label.style.cssText = `position: fixed; top: 4px; right: 4px; padding: 2px 8px; background: ${GREEN}; ` +
    "color: #ffffff; font: 12px/1.4 sans-serif; border-radius: 0 0 0 6px; pointer-events: none;";
  host.attachShadow({ mode: "closed" }).append(frame, label);
  root.documentElement.append(host);
}

export function hideOverlay(root = document) {
  root.getElementById("applai-overlay")?.remove();
}
