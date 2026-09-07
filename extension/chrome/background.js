/*
 * Reports the browser's tab list to taldock over native messaging.
 *
 * The dock cannot see browser tabs (they are not X11 windows), and the
 * extension cannot see X11 window ids. The two are matched on the dock side
 * by comparing each browser window's active tab title to the X window title,
 * so the active tab must always be included in the payload.
 */

const HOST = "com.taldock.tabs";
const DEBOUNCE_MS = 250;
const FAVICON_MAX_BYTES = 24 * 1024;

let port = null;
let pending = null;
let reconnectDelay = 1000;
const faviconCache = new Map();   // favIconUrl -> data: URL (or "")

function connect() {
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (err) {
    scheduleReconnect();
    return;
  }
  port.onDisconnect.addListener(() => {
    port = null;
    scheduleReconnect();
  });
  port.onMessage.addListener(handleCommand);
  reconnectDelay = 1000;
  push();
}

function scheduleReconnect() {
  // The dock may not be running; back off rather than spin.
  setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

/* Commands coming back from the dock. */
function handleCommand(msg) {
  if (!msg || !msg.type) return;
  if (msg.type === "activate" && msg.tabId != null) {
    chrome.tabs.update(msg.tabId, { active: true });
    if (msg.windowId != null) {
      chrome.windows.update(msg.windowId, { focused: true });
    }
  } else if (msg.type === "close" && msg.tabId != null) {
    chrome.tabs.remove(msg.tabId);
  }
}

function toBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

async function faviconDataUrl(url) {
  if (!url) return "";
  if (url.startsWith("data:")) return url;
  if (faviconCache.has(url)) return faviconCache.get(url);
  let result = "";
  try {
    const response = await fetch(url);
    if (response.ok) {
      const buffer = await response.arrayBuffer();
      if (buffer.byteLength <= FAVICON_MAX_BYTES) {
        const type = response.headers.get("content-type") || "image/png";
        result = `data:${type};base64,${toBase64(buffer)}`;
      }
    }
  } catch (err) {
    result = "";
  }
  // Cache failures too, so a broken icon is not refetched on every update.
  faviconCache.set(url, result);
  if (faviconCache.size > 300) faviconCache.clear();
  return result;
}

async function buildPayload() {
  const windows = await chrome.windows.getAll({ populate: true });
  const out = [];
  for (const win of windows) {
    if (win.type !== "normal" || !win.tabs) continue;
    const tabs = [];
    for (const tab of win.tabs) {
      tabs.push({
        id: tab.id,
        index: tab.index,
        title: tab.title || "",
        url: tab.url || "",
        active: !!tab.active,
        favicon: await faviconDataUrl(tab.favIconUrl),
      });
    }
    if (tabs.length) out.push({ id: win.id, tabs });
  }
  return { type: "tabs", browser: detectBrowser(), windows: out };
}

function detectBrowser() {
  // Matches the .desktop id the dock groups this browser's windows under.
  const agent = navigator.userAgent;
  if (agent.includes("Edg/")) return "microsoft-edge.desktop";
  if (agent.includes("OPR/")) return "opera.desktop";
  if (agent.includes("Brave")) return "brave-browser.desktop";
  if (agent.includes("Chromium")) return "chromium.desktop";
  return "google-chrome.desktop";
}

async function push() {
  if (!port) return;
  try {
    port.postMessage(await buildPayload());
  } catch (err) {
    port = null;
    scheduleReconnect();
  }
}

function schedulePush() {
  if (pending) clearTimeout(pending);
  pending = setTimeout(() => { pending = null; push(); }, DEBOUNCE_MS);
}

for (const event of [
  chrome.tabs.onCreated, chrome.tabs.onRemoved, chrome.tabs.onMoved,
  chrome.tabs.onActivated, chrome.tabs.onAttached, chrome.tabs.onDetached,
  chrome.tabs.onReplaced, chrome.windows.onCreated, chrome.windows.onRemoved,
  chrome.windows.onFocusChanged,
]) {
  event.addListener(() => schedulePush());
}
chrome.tabs.onUpdated.addListener((_id, info) => {
  // Only title/icon/url changes affect what the dock draws.
  if (info.title || info.favIconUrl || info.url || info.status === "complete") {
    schedulePush();
  }
});

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
