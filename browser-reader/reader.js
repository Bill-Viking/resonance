"use strict";

// Injected by the worker, on demand, into one owned tab in the extension's
// ISOLATED world (never the page's world). It answers one fixed command at a
// time and returns plain data. The only page interactions are: opening and
// closing the account menu, expanding one comment's own "read more" control,
// pausing a playing video through its local media control, and ONE bounded
// read-only scroll to the single rendered comments section (per document) so a
// deferred comments section can render.
// Injection is idempotent: a second injection into the same document keeps
// the first reader and its per-document state.
// Once bound to a document it also notes trusted (isTrusted) keyboard, mouse,
// pointer, touch or wheel input there; the reader's own clicks are synthetic and
// never count. After any such input every command answers user_input without
// touching the page. Input that happened before injection is NOT detected.
(() => {
  if (globalThis.ResonanceReaderPage) return;
  const S = globalThis.ResonanceReader;
  const COMMANDS = {
    x_profile: ["op", "mode", "url", "handle"],
    x_identity: ["op", "mode", "url", "handle"],
    x_status: ["op", "mode", "url", "handle", "id"],
    yt_channel: ["op", "mode", "url", "handle"],
    yt_menu_open: ["op", "mode", "url", "handle"],
    yt_menu_read: ["op", "mode", "url", "handle"],
    yt_menu_close: ["op", "mode", "url", "handle"],
    yt_activity: ["op", "mode", "url"],
    // Own-account discovery: identity and account-menu reads only, on a provider home page.
    x_whoami: ["op", "mode", "url"],
    yt_home_menu_open: ["op", "mode", "url"],
    yt_home_menu_read: ["op", "mode", "url"],
    yt_home_menu_close: ["op", "mode", "url"],
    yt_comment: ["op", "mode", "url", "v", "lc"],
  };
  const expanded = new WeakSet();
  let scrolled = false;
  let interrupted = false;
  if (typeof document.addEventListener === "function") {
    for (const type of ["keydown", "mousedown", "pointerdown", "touchstart", "wheel"]) {
      document.addEventListener(type, event => { if (event && event.isTrusted === true) interrupted = true; },
        { capture: true, passive: true });
    }
  }

  function run(cmd) {
    if (interrupted) return { state: "user_input" };
    const result = answer(cmd);
    return interrupted ? { state: "user_input" } : result;
  }
  function answer(cmd) {
    if (!cmd || typeof cmd !== "object" || !Object.hasOwn(COMMANDS, cmd.op) ||
        !S.exactShape(cmd, COMMANDS[cmd.op]) || !Object.hasOwn(S.SITES, cmd.mode)) {
      return { state: "bad_command" };
    }
    if (window.top !== window || !S.urlsMatch(location.href, cmd.url, true)) return { state: "wrong_page" };
    const ctx = { sites: S.SITES[cmd.mode], base: location.href,
      expect: { handle: cmd.handle, id: cmd.id, v: cmd.v, lc: cmd.lc } };
    switch (cmd.op) {
      case "x_profile": return S.xProfile(document, ctx);
      case "x_identity": {
        const found = S.readXIdentity(document, { sites: ctx.sites, base: ctx.base });
        if (found.state !== "ready") return found;
        return S.lower(found.handle) === S.lower(cmd.handle)
          ? { state: "ready", identity: { handle: found.handle } } : { state: "identity_mismatch" };
      }
      case "x_status": return S.xStatus(document, ctx);
      case "x_whoami": {
        if (cmd.url !== S.xHomeUrl(ctx.sites.x)) return { state: "bad_command" };
        const found = S.readXIdentity(document, { sites: ctx.sites, base: ctx.base });
        return found.state === "ready" ? { state: "ready", identity: { handle: found.handle } } : found;
      }
      case "yt_home_menu_open":
        return cmd.url === S.ytHomeUrl(ctx.sites.youtube) ? homeMenuOpen(ctx) : { state: "bad_command" };
      case "yt_home_menu_read":
        return cmd.url === S.ytHomeUrl(ctx.sites.youtube) ? S.ytMenuRead(document, ctx) : { state: "bad_command" };
      case "yt_home_menu_close":
        return cmd.url === S.ytHomeUrl(ctx.sites.youtube) ? menuClose(ctx) : { state: "bad_command" };
      case "yt_channel": return S.ytChannel(document, ctx);
      case "yt_menu_open": return menuOpen(ctx);
      case "yt_menu_read": return S.ytMenuRead(document, ctx);
      case "yt_menu_close": return menuClose(ctx);
      case "yt_activity": return S.ytActivity(document, ctx);
      case "yt_comment": {
        const video = document.querySelector("video");
        if (video && typeof video.pause === "function" && video.paused === false) video.pause();
        const result = S.ytComment(document, ctx, { expand });
        if (result.state === "pending" && result.waiting === "renderer") revealComments();
        return result;
      }
      default: return { state: "bad_command" };
    }
  }

  // Native YouTube may defer the comments section until it is in view. At most
  // one scroll per document, only to the single RENDERED comments section. An
  // unrendered duplicate (hidden, detached or zero-size) is ignored; a rendered
  // section below the viewport still qualifies, since scrolling to it is the point.
  // Zero or several rendered candidates scroll nothing and keep the allowance.
  function revealComments() {
    if (scrolled) return;
    const rendered = [];
    for (const section of document.querySelectorAll("ytd-comments")) {
      if (isRendered(section)) rendered.push(section);
    }
    if (rendered.length !== 1) return;
    scrolled = true;
    rendered[0].scrollIntoView({ block: "start" });
  }
  // Fails closed: any missing API, odd value or thrown error means "not rendered".
  function isRendered(element) {
    try {
      if (element.isConnected !== true || typeof window.getComputedStyle !== "function" ||
          typeof element.getBoundingClientRect !== "function") return false;
      let depth = 0;
      for (let node = element; node; node = node.parentNode) {
        if (node.nodeType !== 1) break;
        if ((depth += 1) > 1000) return false;
        if (typeof node.hasAttribute !== "function" || node.hasAttribute("hidden")) return false;
        if (String(node.getAttribute("aria-hidden")).trim().toLowerCase() === "true") return false;
        const style = window.getComputedStyle(node);
        if (!style || typeof style.display !== "string" || style.display.trim().toLowerCase() === "none") return false;
        if (node === element) {
          const visibility = typeof style.visibility === "string" ? style.visibility.trim().toLowerCase() : "";
          if (visibility === "" || visibility === "hidden" || visibility === "collapse") return false;
        }
      }
      const box = element.getBoundingClientRect();
      if (!box || typeof box !== "object") return false;
      return [box.left, box.top, box.width, box.height].every(n => typeof n === "number" && Number.isFinite(n)) &&
        box.width > 0 && box.height > 0;
    } catch (_) {
      return false;
    }
  }
  function expand(control) {
    if (expanded.has(control)) return false;
    expanded.add(control);
    control.click(); // The comment's own read-only "more" control only.
    return true;
  }
  function menuButton() {
    const buttons = document.querySelectorAll("ytd-masthead #avatar-btn");
    return buttons.length === 1 ? buttons[0] : null;
  }
  function menuOpen(ctx) {
    const button = menuButton();
    if (!button) return { state: "pending" };
    if (S.ytMenuRead(document, ctx).state !== "pending") {
      return { state: "ready" }; // Already open.
    }
    button.click(); // Opens the menu. Nothing in it is ever clicked.
    return { state: "ready" };
  }
  // Discovery on the YouTube home page: no avatar button plus a sign-in link is
  // signed out; no button otherwise is a page still loading.
  function homeMenuOpen(ctx) {
    if (!menuButton() && S.ytSignedOut(document)) return { state: "signed_out" };
    return menuOpen(ctx);
  }
  function menuClose(ctx) {
    const button = menuButton();
    if (!button) return { state: "pending" };
    if (S.ytMenuRead(document, ctx).state === "pending") return { state: "ready", closed: true };
    button.click();
    return { state: "pending" };
  }
  globalThis.ResonanceReaderPage = Object.freeze({ run });
})();
