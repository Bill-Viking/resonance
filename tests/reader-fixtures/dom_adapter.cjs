"use strict";

// A small, dependency-free fixture DOM for Node tests. It exists only so the
// real shared.js / reader.js parsers can run against the generated fixture
// HTML without a browser. LIMITS: well-formed HTML only (no implied end tags
// or table repair); layout is a fixture model only (default sized box below the first
// screen, data-fx-rect to override, inline style display/visibility for getComputedStyle;
// no CSS cascade or checkVisibility); selectors are
// limited to tag, #id, .class, [attr], [attr=v], [attr^=v], [attr*=v],
// compounds, descendant and child (>) combinators and comma lists; events
// support bubbling only (click, plus fireInput for trusted or untrusted input). The parent runs native browser fixtures.

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const decode = text => text.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);/g, (whole, name) => {
  if (name[0] === "#") {
    const code = name[1] === "x" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
    return String.fromCodePoint(code);
  }
  return Object.hasOwn(ENTITIES, name) ? ENTITIES[name] : whole;
});

class Node {
  constructor(type) { this.nodeType = type; this.parentNode = null; this.childNodes = []; this.listeners = []; }
  get textContent() {
    return this.nodeType === 3 ? this.data : this.childNodes.map(child => child.textContent).join("");
  }
  set textContent(value) {
    this.childNodes = [];
    const text = new Node(3);
    text.data = String(value);
    text.parentNode = this;
    this.childNodes.push(text);
  }
}

class Element extends Node {
  constructor(tag) { super(1); this.tagName = tag.toUpperCase(); this.attrs = new Map(); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  removeAttribute(name) { this.attrs.delete(name); }
  get id() { return this.attrs.get("id") || ""; }
  querySelectorAll(selector) {
    const chains = parseSelector(selector);
    const out = [];
    (function walk(node) {
      for (const child of node.childNodes) {
        if (child.nodeType !== 1) continue;
        if (chains.some(chain => matchChain(child, chain, chain.length - 1))) out.push(child);
        walk(child);
      }
    })(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, listener) { this.listeners.push({ type, listener }); }
  click() { clickLog.push(this.getAttribute("id") || this.tagName); dispatchClick(this); }
  // Attached to a document through every parent's child list (not just parent links).
  get isConnected() {
    let node = this;
    while (node.parentNode) {
      if (!node.parentNode.childNodes.includes(node)) return false;
      node = node.parentNode;
    }
    return node.nodeType === 9;
  }
  // Fixture layout: below the first screen and sized, unless data-fx-rect="left,top,width,height" says otherwise
  // (any entry may be NaN, Infinity, empty or text, to model invalid geometry).
  getBoundingClientRect() {
    const raw = this.getAttribute("data-fx-rect");
    const [left, top, width, height] = raw === null ? DEFAULT_RECT
      : raw.split(",").map(part => (part.trim() === "" ? NaN : Number(part)));
    return { left, top, width, height, right: left + width, bottom: top + height };
  }
  scrollIntoView() {
    scrollLog.push(this.tagName.toLowerCase());
    for (const entry of this.listeners) if (entry.type === "scrollintoview") entry.listener({ type: "scrollintoview", target: this });
  }
  appendHtml(html) {
    for (const child of parseHtml(html).childNodes) { child.parentNode = this; this.childNodes.push(child); }
  }
}
class Document extends Element {
  constructor() { super("#document"); this.nodeType = 9; }
}

const DEFAULT_RECT = [0, 2400, 640, 480];
const inlineStyle = element => {
  const out = {};
  for (const declaration of (element.getAttribute("style") || "").split(";")) {
    const colon = declaration.indexOf(":");
    if (colon > 0) out[declaration.slice(0, colon).trim().toLowerCase()] = declaration.slice(colon + 1).trim().toLowerCase();
  }
  return out;
};
// window.getComputedStyle for the fixture DOM: inline style only. display is per element; visibility inherits.
function getComputedStyle(element) {
  let visibility = "visible";
  for (let node = element; node && node.nodeType === 1; node = node.parentNode) {
    const own = inlineStyle(node).visibility;
    if (own) { visibility = own; break; }
  }
  return { display: inlineStyle(element).display || "block", visibility };
}

const clickLog = [];
const scrollLog = [];
function dispatchClick(target) {
  const event = { type: "click", target, isTrusted: false };
  for (let node = target; node; node = node.parentNode) {
    for (const entry of node.listeners) if (entry.type === "click") entry.listener(event);
  }
}

// Dispatches any input event with bubbling, as the user (trusted) or as a script (untrusted).
function fireInput(target, type, trusted = true) {
  const event = { type, target, isTrusted: trusted };
  for (let node = target; node; node = node.parentNode) {
    for (const entry of node.listeners) if (entry.type === type) entry.listener(event);
  }
}

// ---- selectors ----------------------------------------------------------
function parseCompound(text) {
  const compound = { tag: null, id: null, classes: [], attrs: [] };
  const re = /([A-Za-z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[\s*([\w-]+)\s*(?:([\^*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]/y;
  let match;
  re.lastIndex = 0;
  while (re.lastIndex < text.length && (match = re.exec(text))) {
    if (match[1]) compound.tag = match[1].toLowerCase();
    else if (match[2]) compound.id = match[2];
    else if (match[3]) compound.classes.push(match[3]);
    else compound.attrs.push({ name: match[4], op: match[5] || null, value: match[6] ?? match[7] ?? match[8] ?? null });
  }
  if (re.lastIndex !== text.length) throw new Error("unsupported selector: " + text);
  return compound;
}
function parseSelector(selector) {
  return selector.split(/,(?![^\[]*\])/).map(part => {
    const tokens = part.trim().replace(/\s*>\s*/g, " > ").match(/(?:[^\s\[]|\[[^\]]*\])+/g);
    const chain = [];
    let combinator = " ";
    for (const token of tokens) {
      if (token === ">") { combinator = ">"; continue; }
      chain.push({ ...parseCompound(token), combinator });
      combinator = " ";
    }
    return chain;
  });
}
function matchCompound(element, c) {
  if (c.tag && element.tagName.toLowerCase() !== c.tag) return false;
  if (c.id && element.getAttribute("id") !== c.id) return false;
  const classes = (element.getAttribute("class") || "").split(/\s+/);
  if (!c.classes.every(name => classes.includes(name))) return false;
  return c.attrs.every(({ name, op, value }) => {
    const actual = element.getAttribute(name);
    if (actual === null) return false;
    if (op === null) return true;
    if (op === "=") return actual === value;
    if (op === "^=") return actual.startsWith(value);
    return actual.includes(value);
  });
}
function matchChain(element, chain, index) {
  const compound = chain[index];
  if (!matchCompound(element, compound)) return false;
  if (index === 0) return true;
  if (compound.combinator === ">") {
    return !!element.parentNode && element.parentNode.nodeType === 1 && matchChain(element.parentNode, chain, index - 1);
  }
  for (let up = element.parentNode; up && up.nodeType === 1; up = up.parentNode) {
    if (matchChain(up, chain, index - 1)) return true;
  }
  return false;
}

// ---- parser ---------------------------------------------------------------
function parseHtml(html) {
  const doc = new Document();
  let current = doc;
  const append = node => { node.parentNode = current; current.childNodes.push(node); };
  const re = /<!--[\s\S]*?-->|<!doctype[^>]*>|<\/([A-Za-z][\w:-]*)\s*>|<([A-Za-z][\w:-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/gi;
  let last = 0;
  let match;
  const text = value => {
    if (!value) return;
    const node = new Node(3);
    node.data = decode(value);
    append(node);
  };
  while ((match = re.exec(html))) {
    text(html.slice(last, match.index));
    last = re.lastIndex;
    if (match[1]) {
      const name = match[1].toLowerCase();
      for (let node = current; node && node !== doc; node = node.parentNode) {
        if (node.tagName.toLowerCase() === name) { current = node.parentNode; break; }
      }
    } else if (match[2]) {
      const element = new Element(match[2]);
      const attrRe = /([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      let attr;
      while ((attr = attrRe.exec(match[3]))) element.attrs.set(attr[1].toLowerCase(), decode(attr[2] ?? attr[3] ?? attr[4] ?? ""));
      append(element);
      const name = match[2].toLowerCase();
      if (name === "script" || name === "style") {
        const close = html.toLowerCase().indexOf("</" + name, last);
        const end = close < 0 ? html.length : close;
        const raw = new Node(3);
        raw.data = html.slice(last, end);
        raw.parentNode = element;
        if (raw.data) element.childNodes.push(raw);
        re.lastIndex = last = html.indexOf(">", end) + 1 || html.length;
      } else if (!VOID.has(name) && !match[4]) current = element;
    }
  }
  text(html.slice(last));
  return doc;
}

module.exports = { parseHtml, Element, Node, Document, clickLog, scrollLog, fireInput, getComputedStyle };
