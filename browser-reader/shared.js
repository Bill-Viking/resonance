"use strict";

// Pure helpers shared by the worker, the isolated page reader and the root
// bridge. No network, storage, eval or DOM mutation lives here. The parsers
// that read X / YouTube pages are in the same file as their validators.
// Vetted X and YouTube logic carried over from the R1-accepted reader; only
// the provider set (X and YouTube) and the single local app address changed.
(function (scope) {
  const LIMITS = Object.freeze({
    items: 3, xExamine: 6, itemBytes: 16384, totalBytes: 65536,
    jobMs: 120000, messageChars: 262144,
  });
  const LOCAL = "http://127.0.0.1:4317";
  const SITES = Object.freeze({
    live: Object.freeze({
      x: Object.freeze({ origin: "https://x.com", prefix: "" }),
      youtube: Object.freeze({ origin: "https://www.youtube.com", prefix: "" }),
      activity: Object.freeze({ origin: "https://myactivity.google.com", prefix: "" }),
    }),
  });
  const PERMISSION_ORIGINS = Object.freeze({
    x: Object.freeze(["https://x.com/*"]),
    youtube: Object.freeze(["https://www.youtube.com/*", "https://myactivity.google.com/*"]),
  });
  const X_HANDLE = /^[A-Za-z0-9_]{1,15}$/;
  const X_RESERVED = new Set(["home", "explore", "notifications", "messages", "search", "settings",
    "i", "compose", "login", "logout", "signup", "tos", "privacy", "intent", "share", "hashtag",
    "account", "jobs", "help", "about"]);
  const YT_HANDLE = /^[A-Za-z0-9._-]{3,30}$/;
  const YT_CHANNEL = /^UC[A-Za-z0-9_-]{22}$/;
  const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
  const COMMENT_ID = /^[A-Za-z0-9_-][A-Za-z0-9_.-]{8,78}[A-Za-z0-9_-]$/;
  const STATUS_ID = /^[0-9]{1,25}$/;
  const REQUEST_ID = /^[a-f0-9]{32}$/;
  const TOKEN = /^[a-z_]{1,48}$/;
  const ACTIVITY_QUERY = "?page=youtube_comments";

  function exactShape(value, keys) {
    return value !== null && typeof value === "object" && !Array.isArray(value) &&
      Object.keys(value).sort().join(",") === keys.slice().sort().join(",");
  }

  function validXHandle(value) {
    return typeof value === "string" && X_HANDLE.test(value) && !X_RESERVED.has(value.toLowerCase());
  }
  const validYtHandle = value => typeof value === "string" && YT_HANDLE.test(value);
  const validChannelId = value => typeof value === "string" && YT_CHANNEL.test(value);
  const lower = value => String(value).toLowerCase();

  // UTF-8 length without TextEncoder so VM and worker contexts behave alike.
  function utf8Length(text) {
    let total = 0;
    for (let i = 0; i < text.length; i += 1) {
      const code = text.charCodeAt(i);
      if (code < 0x80) total += 1;
      else if (code < 0x800) total += 2;
      else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length &&
          text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) { total += 4; i += 1; }
      else total += 3;
    }
    return total;
  }

  // ---- strict URL validation -------------------------------------------
  // A candidate must start with the exact configured origin (so uppercase
  // hosts, credentials, explicit ports and other schemes all fail), carry no
  // fragment, no whitespace/control characters and be reasonably short.
  // URL-standard single/double dot segments, literal or percent-encoded (any
  // case, mixed), in the path part of a raw spelling (before any "?").
  function hasDotSegment(pathAndQuery) {
    return pathAndQuery.split("?")[0].split("/").some(part => /^(?:\.|%2e){1,2}$/i.test(part));
  }
  function parseUrl(value, site) {
    if (typeof value !== "string" || value.length === 0 || value.length > 2048 ||
        /[\u0000-\u0020\u007f-\u009f\\]/.test(value) || value.includes("#") ||
        !value.startsWith(site.origin + "/") ||
        hasDotSegment(value.slice(site.origin.length))) return null;
    let url;
    try { url = new URL(value); } catch (_) { return null; }
    if (url.origin !== site.origin || url.username || url.password || url.hash) return null;
    if (!url.pathname.startsWith(site.prefix + "/")) return null;
    return url;
  }
  function pathParts(url, site) {
    return url.pathname.slice(site.prefix.length).split("/"); // ["", ...]
  }

  function xProfileUrl(site, handle) { return `${site.origin}${site.prefix}/${handle}`; }
  function validateXProfileUrl(value, site) {
    const url = parseUrl(value, site);
    if (!url || url.search) return null;
    const parts = pathParts(url, site);
    return parts.length === 2 && validXHandle(parts[1]) ? { handle: parts[1] } : null;
  }
  function xStatusUrl(site, handle, id) { return `${site.origin}${site.prefix}/${handle}/status/${id}`; }
  function validateXStatusUrl(value, site) {
    const url = parseUrl(value, site);
    if (!url || url.search) return null;
    const parts = pathParts(url, site);
    if (parts.length !== 4 || parts[2] !== "status" || !validXHandle(parts[1]) ||
        !STATUS_ID.test(parts[3])) return null;
    return { handle: parts[1], id: parts[3], url: xStatusUrl(site, parts[1], parts[3]) };
  }
  function ytChannelUrl(site, handle) { return `${site.origin}${site.prefix}/@${handle}`; }
  function activityUrl(site) { return `${site.origin}${site.prefix}/page${ACTIVITY_QUERY}`; }
  function watchUrl(site, v, lc) { return `${site.origin}${site.prefix}/watch?v=${v}&lc=${lc}`; }
  function validateWatchUrl(value, site) {
    const url = parseUrl(value, site);
    if (!url || url.pathname !== `${site.prefix}/watch`) return null;
    const entries = Array.from(url.searchParams.entries());
    if (entries.length !== 2 || new Set(entries.map(entry => entry[0])).size !== 2) return null;
    const v = url.searchParams.get("v");
    const lc = url.searchParams.get("lc");
    if (!VIDEO_ID.test(v || "") || !COMMENT_ID.test(lc || "") || lc.includes("..")) return null;
    return { v, lc, url: watchUrl(site, v, lc) };
  }
  // A DOM timestamp link on a watch page may carry one extra provider metadata
  // field, pp. It is accepted ONLY here (never for navigation targets): exactly
  // v + lc, optionally one bounded pp, no duplicate or unknown keys. The result
  // is canonical v + lc, so pp never reaches a navigation or the validator above.
  const PP_VALUE = /^[A-Za-z0-9_=-]{1,128}$/;
  function parseDomWatchLink(value, site) {
    const url = parseUrl(value, site);
    if (!url || url.pathname !== `${site.prefix}/watch` || url.search.length > 512) return null;
    const entries = Array.from(url.searchParams.entries());
    const keys = entries.map(entry => entry[0]);
    if (entries.length < 2 || entries.length > 3 || new Set(keys).size !== keys.length ||
        !keys.every(key => key === "v" || key === "lc" || key === "pp")) return null;
    const v = url.searchParams.get("v");
    const lc = url.searchParams.get("lc");
    const pp = url.searchParams.get("pp");
    if (!VIDEO_ID.test(v || "") || !COMMENT_ID.test(lc || "") || lc.includes("..")) return null;
    if (keys.includes("pp") && !PP_VALUE.test(pp || "")) return null;
    return { v, lc, url: watchUrl(site, v, lc) };
  }
  // True only for a destination this worker itself builds: nothing else is opened.
  function validDestination(value, mode, provider) {
    if (typeof value !== "string" || !Object.hasOwn(SITES, mode)) return false;
    const sites = SITES[mode];
    if (provider === "x") {
      const profile = validateXProfileUrl(value, sites.x);
      if (profile && xProfileUrl(sites.x, profile.handle) === value) return true;
      const status = validateXStatusUrl(value, sites.x);
      return !!status && status.url === value;
    }
    if (provider !== "youtube") return false;
    if (value === activityUrl(sites.activity)) return true;
    const watch = validateWatchUrl(value, sites.youtube);
    if (watch && watch.url === value) return true;
    const channel = validateYtProfileHref(value, sites.youtube);
    return !!channel && ytChannelUrl(sites.youtube, channel.handle) === value;
  }
  // ---- own-account discovery destinations ---------------------------------
  // Fixed addresses used ONLY by a discovery job: the signed-in home page of the
  // provider, and the exact own profile or channel address derived from the one
  // handle that home page showed. An ordinary read never uses the home pages.
  function xHomeUrl(site) { return `${site.origin}${site.prefix}/home`; }
  function ytHomeUrl(site) { return `${site.origin}${site.prefix}/`; }
  function ytChannelIdUrl(site, channelId) { return `${site.origin}${site.prefix}/channel/${channelId}`; }
  function validDiscoveryDestination(value, provider) {
    if (typeof value !== "string") return false;
    const sites = SITES.live;
    if (provider === "x") {
      if (value === xHomeUrl(sites.x)) return true;
      const profile = validateXProfileUrl(value, sites.x);
      return !!profile && xProfileUrl(sites.x, profile.handle) === value;
    }
    if (provider !== "youtube") return false;
    if (value === ytHomeUrl(sites.youtube)) return true;
    const channel = validateYtProfileHref(value, sites.youtube);
    return !!channel && ytChannelUrl(sites.youtube, channel.handle) === value;
  }
  function validateStudioUrl(value) {
    const match = /^https:\/\/studio\.youtube\.com\/channel\/(UC[A-Za-z0-9_-]{22})\/(editing|content)$/.exec(
      typeof value === "string" ? value : "");
    return match ? { channelId: match[1], kind: match[2] } : null;
  }
  function validateYtProfileHref(value, site) {
    const url = parseUrl(value, site);
    if (!url || url.search) return null;
    const parts = pathParts(url, site);
    if (parts.length !== 2) return null;
    if (/^@[A-Za-z0-9._-]{3,30}$/.test(parts[1])) return { handle: parts[1].slice(1) };
    return null;
  }
  function validateYtChannelPath(value, site) {
    const url = parseUrl(value, site);
    if (!url || url.search) return null;
    const parts = pathParts(url, site);
    return parts.length === 3 && parts[1] === "channel" && YT_CHANNEL.test(parts[2])
      ? { channelId: parts[2] } : null;
  }
  // Compares a tab address with the address the worker asked for. Profile
  // pages may be re-cased by the provider; query and origin must stay exact.
  function urlsMatch(actual, expected, loosePath) {
    if (typeof actual !== "string" || typeof expected !== "string") return false;
    let a;
    let b;
    try { a = new URL(actual); b = new URL(expected); } catch (_) { return false; }
    if (a.origin !== b.origin || a.search !== b.search || a.hash) return false;
    return loosePath ? a.pathname.toLowerCase() === b.pathname.toLowerCase() : a.pathname === b.pathname;
  }

  // ---- messages -------------------------------------------------------
  // The only job request: one X or YouTube account, always live.
  function validateStart(message) {
    if (!exactShape(message, ["type", "requestId", "provider", "mode", "handle", "channelId"]) ||
        message.type !== "START" || !REQUEST_ID.test(message.requestId || "") ||
        message.mode !== "live" || (message.provider !== "x" && message.provider !== "youtube")) return null;
    const channelId = message.channelId === "" ? null : message.channelId;
    if (message.provider === "x") {
      if (!validXHandle(message.handle) || channelId !== null) return null;
    } else if (!validYtHandle(message.handle) || (channelId !== null && !validChannelId(channelId))) return null;
    return { requestId: message.requestId, provider: message.provider, mode: message.mode,
      handle: message.handle, channelId };
  }

  // ---- own-account discovery messages --------------------------------------
  // Separate message types and validators from the read messages above: a
  // discovery result is never a post-reading success and is never accepted by
  // validateResult. One DISCOVER asks the worker for one provider.
  const DISCOVERY_PROVIDERS = Object.freeze(["x", "youtube"]);
  function validateDiscover(message) {
    if (!exactShape(message, ["type", "discoveryId", "provider"]) || message.type !== "DISCOVER" ||
        !REQUEST_ID.test(message.discoveryId || "") || !DISCOVERY_PROVIDERS.includes(message.provider)) return null;
    return { discoveryId: message.discoveryId, provider: message.provider };
  }
  // The page's discover event: exact keys, a 32-hex ID, one or two unique providers.
  function validateDiscoverInput(detail) {
    if (!exactShape(detail, ["discoveryId", "providers"]) || typeof detail.discoveryId !== "string" ||
        !REQUEST_ID.test(detail.discoveryId) || !Array.isArray(detail.providers) ||
        detail.providers.length < 1 || detail.providers.length > 2 ||
        !detail.providers.every(p => DISCOVERY_PROVIDERS.includes(p)) ||
        new Set(detail.providers).size !== detail.providers.length) return null;
    return { discoveryId: detail.discoveryId, providers: detail.providers.slice() };
  }
  // The identity a discovery may report: X has a handle only; YouTube has a
  // current handle AND the stable channel ID. The address is always derived from
  // those parts, never taken from the page.
  function validDiscoveryIdentity(identity, provider) {
    if (!exactShape(identity, ["handle", "channel_id", "profile_url"])) return false;
    if (provider === "x") {
      return validXHandle(identity.handle) && identity.channel_id === null &&
        identity.profile_url === xProfileUrl(SITES.live.x, identity.handle);
    }
    if (provider !== "youtube") return false;
    return validYtHandle(identity.handle) && validChannelId(identity.channel_id) &&
      identity.profile_url === ytChannelIdUrl(SITES.live.youtube, identity.channel_id);
  }
  function validateDiscoveryResult(message, request) {
    const base = ["type", "discoveryId", "provider", "status"];
    if (message === null || typeof message !== "object" || message.type !== "DISCOVERY_RESULT" ||
        message.discoveryId !== request.discoveryId || message.provider !== request.provider ||
        !DISCOVERY_PROVIDERS.includes(message.provider)) return false;
    try { if (JSON.stringify(message).length > LIMITS.messageChars) return false; } catch (_) { return false; }
    if (message.status === "ok") {
      return exactShape(message, base.concat(["identity", "observed_at"])) &&
        validDiscoveryIdentity(message.identity, message.provider) && validIso(message.observed_at);
    }
    if (message.status !== "held" && message.status !== "error") return false;
    return exactShape(message, base.concat(["reason"])) && typeof message.reason === "string" && TOKEN.test(message.reason);
  }

  const ITEM_KEYS = ["provider", "source_id", "url", "author", "channel_id", "text",
    "date_iso", "date_label", "date_unknown", "links_shortened"];
  function validIso(value) {
    return typeof value === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,3})?Z$/.test(value) &&
      !Number.isNaN(Date.parse(value));
  }
  function validItem(item, provider, mode, identity) {
    if (!exactShape(item, ITEM_KEYS) || item.provider !== provider ||
        typeof item.text !== "string" || item.text.length === 0 || item.text.trim().length === 0 ||
        utf8Length(item.text) > LIMITS.itemBytes ||
        typeof item.date_unknown !== "boolean" || typeof item.links_shortened !== "boolean" ||
        (item.date_iso !== null && !validIso(item.date_iso)) ||
        (item.date_label !== null && (typeof item.date_label !== "string" || item.date_label.length > 80)) ||
        (item.date_iso === null) !== item.date_unknown) return false;
    if (!Object.hasOwn(SITES, mode)) return false;
    const site = SITES[mode];
    if (provider === "x") {
      const parsed = validateXStatusUrl(item.url, site.x);
      return !!parsed && parsed.url === item.url && parsed.id === item.source_id &&
        lower(parsed.handle) === lower(item.author) && lower(item.author) === lower(identity.handle) &&
        item.channel_id === null && identity.channel_id === null;
    }
    if (provider !== "youtube") return false; // An unsupported provider never validates.
    const parsed = validateWatchUrl(item.url, site.youtube);
    return !!parsed && parsed.url === item.url && parsed.lc === item.source_id &&
      lower(item.author) === lower(identity.handle) && validChannelId(item.channel_id) &&
      item.channel_id === identity.channel_id;
  }
  const DIAG_REASON = "ownership_not_confirmed";
  const DIAG_KEYS = ["document_ready", "visibility", "avatar", "editing_link", "content_link", "channels"];
  function validChannelDiag(value) {
    return exactShape(value, DIAG_KEYS) &&
      ["loading", "interactive", "complete", "unknown"].includes(value.document_ready) &&
      ["visible", "hidden", "unknown"].includes(value.visibility) &&
      typeof value.avatar === "boolean" && typeof value.editing_link === "boolean" &&
      typeof value.content_link === "boolean" && (value.channels === 0 || value.channels === 1 || value.channels === 2);
  }
  // A fresh copy of a valid diagnostic, or null: anything off-shape is dropped whole.
  function cleanChannelDiag(value) {
    return validChannelDiag(value) ? { document_ready: value.document_ready, visibility: value.visibility,
      avatar: value.avatar, editing_link: value.editing_link, content_link: value.content_link,
      channels: value.channels } : null;
  }
  // Consumer-side check for a worker RESULT, used by the root bridge.
  function validateResult(message, request) {
    const base = ["type", "requestId", "provider", "mode", "status", "tabs"];
    if (message === null || typeof message !== "object" || message.type !== "RESULT" ||
        message.requestId !== request.requestId || message.provider !== request.provider ||
        message.mode !== request.mode || !Array.isArray(message.tabs) || message.tabs.length > 16 ||
        !(request.provider === "x" || request.provider === "youtube") ||
        !message.tabs.every(id => Number.isInteger(id) && id >= 0)) return false;
    try { if (JSON.stringify(message).length > LIMITS.messageChars) return false; } catch (_) { return false; }
    if (message.status === "ok") {
      if (!exactShape(message, base.concat(["items", "coverage", "identity"]))) return false;
      const { items, coverage, identity } = message;
      // The result must be for the identity that was requested, not merely
      // consistent with itself. Zero accepted items is never an "ok": no
      // supported zero-item provider evidence exists.
      if (!exactShape(identity, ["handle", "channel_id"]) || typeof identity.handle !== "string" ||
          typeof request.handle !== "string" || lower(identity.handle) !== lower(request.handle) ||
          (request.provider === "youtube"
            ? !validChannelId(identity.channel_id) ||
              (request.channelId !== null && request.channelId !== undefined && identity.channel_id !== request.channelId)
            : identity.channel_id !== null) ||
          !Array.isArray(items) || items.length < 1 || items.length > LIMITS.items) return false;
      let total = 0;
      for (const item of items) {
        if (!validItem(item, request.provider, request.mode, identity)) return false;
        total += utf8Length(item.text);
      }
      if (new Set(items.map(item => item.source_id)).size !== items.length ||
          new Set(items.map(item => item.url)).size !== items.length || total > LIMITS.totalBytes) return false;
      return exactShape(coverage, ["limit", "accepted", "examined", "skipped", "more_available", "partial", "notes"]) &&
        coverage.limit === LIMITS.items && coverage.accepted === items.length &&
        Number.isInteger(coverage.examined) && coverage.examined >= 0 && coverage.examined <= 64 &&
        typeof coverage.more_available === "boolean" && typeof coverage.partial === "boolean" &&
        Array.isArray(coverage.skipped) && coverage.skipped.length <= 16 &&
        coverage.skipped.every(row => exactShape(row, ["reason", "count"]) && TOKEN.test(row.reason) &&
          Number.isInteger(row.count) && row.count > 0 && row.count <= 64) &&
        Array.isArray(coverage.notes) && coverage.notes.length <= 16 &&
        coverage.notes.every(note => TOKEN.test(note));
    }
    if (!(message.status === "held" || message.status === "error" || message.status === "cancelled")) return false;
    // The channel diagnostic rides only on a held YouTube "ownership not
    // confirmed"; anywhere else its presence makes the whole result invalid.
    if (Object.hasOwn(message, "diagnostic")) {
      return request.provider === "youtube" && message.status === "held" && message.reason === DIAG_REASON &&
        exactShape(message, base.concat(["reason", "diagnostic"])) && validChannelDiag(message.diagnostic);
    }
    return exactShape(message, base.concat(["reason"])) && typeof message.reason === "string" &&
      TOKEN.test(message.reason);
  }

  // ---- exact text and visibility ---------------------------------------
  // Text nodes verbatim; <br> is a newline; emoji images contribute their alt
  // text. Never trimmed, collapsed or shortened.
  function exactText(node) {
    let out = "";
    (function walk(current) {
      if (current.nodeType === 3) { out += current.data; return; }
      if (current.nodeType !== 1) return;
      const tag = String(current.tagName).toLowerCase();
      if (tag === "script" || tag === "style" || tag === "template") return;
      if (tag === "br") { out += "\n"; return; }
      if (tag === "img") { out += current.getAttribute("alt") || ""; return; }
      for (const child of Array.from(current.childNodes)) walk(child);
    })(node);
    return out;
  }
  function isShown(element) {
    for (let node = element; node && node.nodeType === 1; node = node.parentNode) {
      if (node.hasAttribute("hidden")) return false;
      const style = (node.getAttribute("style") || "").replace(/\s+/g, "").toLowerCase();
      if (style.includes("display:none") || style.includes("visibility:hidden")) return false;
    }
    return typeof element.checkVisibility === "function" ? element.checkVisibility() : true;
  }
  function absoluteHref(anchor, base) {
    const raw = anchor.getAttribute("href");
    if (typeof raw !== "string") return null;
    // Absolute, protocol-relative and unclean spellings are handed to the strict
    // parsers exactly as written, so an explicit port, odd-case host, credential,
    // fragment or control character is still refused there. Only a clean path
    // starting with a single "/" is resolved against the page address.
    if (!/^\/(?![\/\\])/.test(raw) || /[\u0000-\u0020\u007f-\u009f\\#]/.test(raw) ||
        hasDotSegment(raw)) return raw;
    try { return new URL(raw, base).href; } catch (_) { return null; }
  }
  function bump(counts, reason) { counts[reason] = (counts[reason] || 0) + 1; }
  function skipped(counts) {
    return Object.keys(counts).sort().map(reason => ({ reason, count: counts[reason] }));
  }

  // ---- X parsers --------------------------------------------------------
  function readXIdentity(doc, ctx) {
    const links = Array.from(doc.querySelectorAll('a[data-testid="AppTabBar_Profile_Link"]'));
    if (links.length === 0) {
      return doc.querySelector('[data-testid="loginButton"], a[href="/login"], a[href="/i/flow/login"]')
        ? { state: "signed_out" } : { state: "pending" };
    }
    const handles = new Set();
    for (const link of links) {
      const parsed = validateXProfileUrl(absoluteHref(link, ctx.base), ctx.sites.x);
      if (!parsed) return { state: "identity_unreadable" };
      handles.add(parsed.handle);
    }
    if (handles.size !== 1) return { state: "identity_unreadable" };
    const switcher = doc.querySelector('button[data-testid="SideNav_AccountSwitcher_Button"]');
    if (!switcher) return { state: "pending" };
    const seen = new Set((exactText(switcher).match(/@[A-Za-z0-9_]{1,15}(?![A-Za-z0-9_])/g) || [])
      .map(text => lower(text.slice(1))));
    const handle = Array.from(handles)[0];
    if (seen.size > 1 || (seen.size === 1 && !seen.has(lower(handle)))) return { state: "identity_unreadable" };
    return { state: "ready", handle, switcher_handle: seen.size ? Array.from(seen)[0] : null };
  }

  function authorHandles(region, ctx) {
    const found = new Set();
    for (const link of Array.from(region.querySelectorAll("a[href]"))) {
      const parsed = validateXProfileUrl(absoluteHref(link, ctx.base), ctx.sites.x);
      if (parsed) found.add(parsed.handle);
    }
    return found;
  }

  function parseXArticle(article, ctx) {
    const skip = reason => ({ ok: false, reason });
    if (article.querySelector('[data-testid="socialContext"]')) return skip("repost");
    const names = article.querySelectorAll('[data-testid="User-Name"]');
    if (names.length === 0) return skip("no_author");
    if (names.length > 1) return skip("quote_or_embedded");
    const authors = authorHandles(names[0], ctx);
    if (authors.size !== 1) return skip("ambiguous_author");
    const author = Array.from(authors)[0];
    if (lower(author) !== lower(ctx.expect.handle)) return skip("other_author");
    const texts = article.querySelectorAll('[data-testid="tweetText"]');
    if (texts.length === 0) return skip("no_text");
    if (texts.length > 1) return skip("ambiguous_text");
    if (article.querySelector('[data-testid="tweet-text-show-more-link"]')) return skip("show_more_unexpanded");
    const permalinks = new Map();
    for (const link of Array.from(article.querySelectorAll("a[href]"))) {
      if (!link.querySelector("time")) continue;
      const parsed = validateXStatusUrl(absoluteHref(link, ctx.base), ctx.sites.x);
      if (!parsed) return skip("bad_permalink");
      permalinks.set(parsed.id, { parsed, time: link.querySelector("time") });
    }
    if (permalinks.size === 0) return skip("no_permalink");
    if (permalinks.size > 1) return skip("ambiguous_permalink");
    const { parsed, time } = Array.from(permalinks.values())[0];
    if (lower(parsed.handle) !== lower(ctx.expect.handle)) return skip("permalink_author_mismatch");
    const text = exactText(texts[0]);
    if (text.length === 0 || text.trim().length === 0) return skip("empty_text");
    if (utf8Length(text) > LIMITS.itemBytes) return skip("oversize");
    const stamp = time.getAttribute("datetime");
    const label = exactText(time).trim();
    const shortened = Array.from(texts[0].querySelectorAll("a[href]")).some(link => {
      const shown = exactText(link);
      return !/^[@#$]/.test(shown.trim()) && shown !== absoluteHref(link, ctx.base);
    });
    return { ok: true, item: {
      permalink: parsed.url, id: parsed.id, author, text,
      date_iso: validIso(stamp) ? stamp : null,
      date_label: label ? label.slice(0, 80) : null, links_shortened: shortened } };
  }

  function xProfile(doc, ctx) {
    const identity = readXIdentity(doc, ctx);
    if (identity.state !== "ready") return identity;
    if (lower(identity.handle) !== lower(ctx.expect.handle)) return { state: "identity_mismatch" };
    const articles = Array.from(doc.querySelectorAll('article[data-testid="tweet"]'));
    if (articles.length === 0) return { state: "pending" };
    const counts = {};
    const items = [];
    let examined = 0;
    for (const article of articles) {
      if (items.length >= LIMITS.items || examined >= LIMITS.xExamine) break;
      examined += 1;
      const parsed = parseXArticle(article, ctx);
      if (parsed.ok) items.push(parsed.item); else bump(counts, parsed.reason);
    }
    return { state: "ready", identity: { handle: identity.handle }, examined, items,
      skipped: skipped(counts), more_available: articles.length > examined };
  }

  function xStatus(doc, ctx) {
    const identity = readXIdentity(doc, ctx);
    if (identity.state !== "ready") return identity;
    if (lower(identity.handle) !== lower(ctx.expect.handle)) return { state: "identity_mismatch" };
    const matches = [];
    for (const article of Array.from(doc.querySelectorAll('article[data-testid="tweet"]'))) {
      const hit = Array.from(article.querySelectorAll("a[href]")).some(link => {
        if (!link.querySelector("time")) return false;
        const parsed = validateXStatusUrl(absoluteHref(link, ctx.base), ctx.sites.x);
        return parsed && parsed.id === ctx.expect.id;
      });
      if (hit) matches.push(article);
    }
    if (matches.length === 0) return { state: "pending" };
    const parsed = matches.map(article => parseXArticle(article, ctx));
    if (!parsed.every(entry => entry.ok)) return { state: "item_unreadable", reason: parsed.find(e => !e.ok).reason };
    const first = parsed[0].item;
    if (!parsed.every(entry => entry.item.id === first.id && entry.item.text === first.text &&
        lower(entry.item.author) === lower(first.author))) return { state: "conflicting_duplicates" };
    return { state: "ready", identity: { handle: identity.handle }, item: first };
  }

  // ---- YouTube parsers --------------------------------------------------
  const SIGN_IN = 'ytd-masthead a[href^="https://accounts.google.com/"]';
  function ytChannelScan(doc) {
    const ids = new Set();
    const kinds = new Set();
    for (const link of Array.from(doc.querySelectorAll('a[href^="https://studio.youtube.com/"]'))) {
      const parsed = validateStudioUrl(link.getAttribute("href"));
      if (!parsed) continue;
      ids.add(parsed.channelId);
      kinds.add(parsed.kind);
    }
    return { avatar: !!doc.querySelector("#avatar-btn"), ids, kinds };
  }
  // Non-content diagnostic for the one failure "ownership not confirmed". Only
  // these closed values can leave the page: two enumerated browser states, four
  // presence flags and a channel count capped at two. No URL, handle, channel
  // ID, text or markup is ever included.
  function ytChannelDiag(doc, scan) {
    const ready = doc.readyState;
    const visible = doc.visibilityState;
    return {
      document_ready: ready === "loading" || ready === "interactive" || ready === "complete" ? ready : "unknown",
      visibility: visible === "visible" || visible === "hidden" ? visible : "unknown",
      avatar: scan.avatar,
      editing_link: scan.kinds.has("editing"),
      content_link: scan.kinds.has("content"),
      channels: Math.min(scan.ids.size, 2),
    };
  }
  function ytChannel(doc, ctx) {
    const scan = ytChannelScan(doc);
    if (!scan.avatar) {
      return doc.querySelector(SIGN_IN) ? { state: "signed_out" } : { state: "pending", diag: ytChannelDiag(doc, scan) };
    }
    if (scan.ids.size > 1) return { state: "channel_id_conflict" };
    if (scan.ids.size === 0 || !scan.kinds.has("editing") || !scan.kinds.has("content")) {
      return { state: "pending", diag: ytChannelDiag(doc, scan) };
    }
    return { state: "ready", channel_id: Array.from(scan.ids)[0] };
  }

  // True when the masthead offers sign-in. Used only by discovery on the home page.
  function ytSignedOut(doc) { return !!doc.querySelector(SIGN_IN); }

  function ytMenuLinks(doc, ctx) {
    return Array.from(doc.querySelectorAll("ytd-popup-container a[href]")).filter(link =>
      exactText(link).trim() === "View your channel" && isShown(link));
  }
  function ytMenuRead(doc, ctx) {
    const links = ytMenuLinks(doc, ctx);
    if (links.length === 0) return { state: "pending" };
    if (links.length > 1) return { state: "menu_ambiguous" };
    const href = absoluteHref(links[0], ctx.base);
    const byHandle = validateYtProfileHref(href, ctx.sites.youtube);
    const byId = validateYtChannelPath(href, ctx.sites.youtube);
    if (!byHandle && !byId) return { state: "menu_unreadable" };
    return { state: "ready", handle: byHandle ? byHandle.handle : null, channel_id: byId ? byId.channelId : null };
  }

  function ytActivity(doc, ctx) {
    const heading = Array.from(doc.querySelectorAll('h1, h2, h3, [role="heading"]'))
      .some(node => exactText(node).trim() === "Your YouTube Comments");
    if (!heading) return { state: "pending" };
    const rows = Array.from(doc.querySelectorAll('[role="listitem"]'));
    if (rows.length === 0) return { state: "pending" };
    const counts = {};
    const candidates = [];
    const seen = new Set();
    let more = false;
    for (const row of rows) {
      const links = new Map();
      for (const link of Array.from(row.querySelectorAll("a[href]"))) {
        const parsed = validateWatchUrl(absoluteHref(link, ctx.base), ctx.sites.youtube);
        if (parsed) links.set(parsed.url, parsed);
      }
      if (links.size === 0) { bump(counts, "no_valid_source"); continue; }
      if (links.size > 1) { bump(counts, "ambiguous_source"); continue; }
      const only = Array.from(links.values())[0];
      if (seen.has(only.url)) { bump(counts, "duplicate_source"); continue; }
      seen.add(only.url);
      if (candidates.length >= LIMITS.items) { more = true; continue; }
      // History text is deliberately discarded: it can be shortened or lose
      // spacing, so it is never evidence of the authored words.
      candidates.push({ v: only.v, lc: only.lc });
    }
    return { state: "ready", listed: rows.length, candidates, skipped: skipped(counts), more_available: more };
  }

  function parseYtRenderer(renderer, ctx) {
    const times = renderer.querySelectorAll("#published-time-text a[href]");
    if (times.length !== 1) return null;
    const source = parseDomWatchLink(absoluteHref(times[0], ctx.base), ctx.sites.youtube);
    if (!source) return null;
    return { renderer, source, link: times[0] };
  }
  function ytComment(doc, ctx, helpers) {
    const found = [];
    for (const renderer of Array.from(doc.querySelectorAll("ytd-comment-view-model"))) {
      const parsed = parseYtRenderer(renderer, ctx);
      if (parsed && parsed.source.v === ctx.expect.v && parsed.source.lc === ctx.expect.lc) found.push(parsed);
    }
    if (found.length === 0) return { state: "pending", waiting: "renderer" };
    const rows = [];
    for (const entry of found) {
      const more = entry.renderer.querySelector("#more");
      if (more && isShown(more)) {
        if (helpers && helpers.expand) helpers.expand(more);
        return { state: "pending", waiting: "expand" };
      }
      const bodies = entry.renderer.querySelectorAll("#content-text");
      const authors = entry.renderer.querySelectorAll("a#author-text");
      if (bodies.length !== 1 || authors.length !== 1) return { state: "comment_unreadable" };
      const authorHref = absoluteHref(authors[0], ctx.base);
      const byHandle = validateYtProfileHref(authorHref, ctx.sites.youtube);
      const byId = validateYtChannelPath(authorHref, ctx.sites.youtube);
      if (!byHandle && !byId) return { state: "comment_unreadable" };
      const text = exactText(bodies[0]);
      const label = exactText(entry.link).trim();
      rows.push({ v: entry.source.v, lc: entry.source.lc, href: entry.source.url,
        author_handle: byHandle ? byHandle.handle : null, author_channel_id: byId ? byId.channelId : null,
        text, date_label: label ? label.slice(0, 80) : null });
    }
    const first = rows[0];
    if (!rows.every(row => row.author_handle === first.author_handle &&
        row.author_channel_id === first.author_channel_id && row.text === first.text)) {
      return { state: "conflicting_duplicates" };
    }
    if (first.text.length === 0 || first.text.trim().length === 0) return { state: "comment_unreadable" };
    if (utf8Length(first.text) > LIMITS.itemBytes) return { state: "ready", oversize: true, comment: null };
    return { state: "ready", oversize: false, comment: first };
  }

  // ---- setup diagnostics ----------------------------------------------------
  // Read-only feedback about the loaded build and provider access. A manifest
  // version is up to four dot-separated integers (0-65535, no leading zeros).
  const SETUP_KEYS = ["type", "version", "x", "youtube"];
  function validSetupVersion(value) {
    if (typeof value !== "string" || value.length > 23 ||
        !/^(0|[1-9][0-9]{0,4})(\.(0|[1-9][0-9]{0,4})){0,3}$/.test(value)) return false;
    return value.split(".").every(part => Number(part) <= 65535);
  }
  const triState = value => value === true || value === false || value === null;
  function validSetup(message) {
    return exactShape(message, SETUP_KEYS) && message.type === "SETUP" &&
      (message.version === null || validSetupVersion(message.version)) &&
      triState(message.x) && triState(message.youtube);
  }

  scope.ResonanceReader = Object.freeze({
    LIMITS, SITES, LOCAL, PERMISSION_ORIGINS, ACTIVITY_QUERY, exactShape, validXHandle, validYtHandle,
    validChannelId, lower, utf8Length, parseUrl, xProfileUrl, validateXProfileUrl, xStatusUrl,
    validateXStatusUrl, ytChannelUrl, activityUrl, watchUrl, validateWatchUrl, parseDomWatchLink, validDestination, validateStudioUrl,
    validateYtProfileHref, validateYtChannelPath, urlsMatch, validateStart, validateResult, validItem,
    validIso, exactText, isShown, absoluteHref, readXIdentity, parseXArticle, xProfile, xStatus,
    ytChannel, ytMenuRead, ytActivity, ytComment, REQUEST_ID, TOKEN, DIAG_REASON, validChannelDiag, cleanChannelDiag,
    validSetupVersion, validSetup,
    xHomeUrl, ytHomeUrl, ytChannelIdUrl, validDiscoveryDestination, DISCOVERY_PROVIDERS, validateDiscover,
    validateDiscoverInput, validDiscoveryIdentity, validateDiscoveryResult, ytSignedOut,
  });
})(globalThis);
