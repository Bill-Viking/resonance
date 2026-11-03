"use strict";

// The only place a provider permission is ever requested or removed, and only
// inside a trusted click on this extension's own options page.
const ORIGINS = {
  x: ["https://x.com/*"],
  youtube: ["https://www.youtube.com/*", "https://myactivity.google.com/*"],
};

async function show(provider) {
  const label = document.getElementById(`state-${provider}`);
  try {
    const granted = await chrome.permissions.contains({ origins: ORIGINS[provider] });
    label.textContent = granted ? "Allowed in this browser." : "Not allowed. Checks for this site will be held.";
  } catch (_) {
    label.textContent = "Could not read the permission state.";
  }
}

for (const provider of Object.keys(ORIGINS)) {
  document.getElementById(`allow-${provider}`).addEventListener("click", async event => {
    if (!event.isTrusted) return;
    try { await chrome.permissions.request({ origins: ORIGINS[provider] }); } catch (_) {}
    await show(provider);
  });
  document.getElementById(`remove-${provider}`).addEventListener("click", async event => {
    if (!event.isTrusted) return;
    try { await chrome.permissions.remove({ origins: ORIGINS[provider] }); } catch (_) {}
    await show(provider);
  });
  void show(provider);
}
