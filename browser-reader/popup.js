"use strict";

// Two buttons, each inside the user's own click: the exact local app root, and
// this extension's own options page. Nothing else is ever opened from here.
const APP_URL = "http://127.0.0.1:4317/";

document.getElementById("open-app").addEventListener("click", event => {
  if (!event.isTrusted) return;
  void chrome.tabs.create({ url: APP_URL });
});
document.getElementById("open-options").addEventListener("click", event => {
  if (!event.isTrusted) return;
  void chrome.runtime.openOptionsPage();
});
