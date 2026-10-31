/* SysMon 前端：登录、仪表盘、波形 / 表盘渲染、管理面板 */
"use strict";

/* ------------------------------------------------------------------ 工具 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

const ICONS = {
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3"/></svg>',
  memory: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="9" rx="2"/><path d="M7 16v3M12 16v3M17 16v3M7 10v3M12 10v3M17 10v3"/></svg>',
  disk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
  gpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="10" rx="2"/><circle cx="8" cy="11" r="2"/><path d="M13 9.5h4M13 12.5h3M5 16v3M19 16v3"/></svg>',
  net: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17V7M7 7L4 10M7 7l3 3"/><path d="M17 7v10M17 17l-3-3M17 17l3-3"/></svg>',
  drag: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 14l6-6 6 6"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 10l6 6 6-6"/></svg>',
};

const GAUGE_SVG = `<svg class="gauge" viewBox="0 0 200 150" role="img" aria-label="使用率">
  <circle class="gauge-track" cx="100" cy="96" r="64" pathLength="100" stroke-dasharray="75 100" transform="rotate(135 100 96)"/>
  <circle class="gauge-value" cx="100" cy="96" r="64" pathLength="100" stroke-dasharray="0 100" transform="rotate(135 100 96)"/>
  <text class="gauge-num" x="100" y="102">--</text>
  <text class="gauge-cap" x="100" y="132">使用率</text>
</svg>`;

function fmtPct(v) {
  return typeof v === "number" && isFinite(v) ? `${v.toFixed(1)}%` : "--";
}

function fmtBytes(n) {
  if (typeof n !== "number" || !isFinite(n) || n < 0) return "--";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const digits = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)} ${units[i]}`;
}

const fmtSpeed = (n) => `${fmtBytes(n)}/s`;

function fmtUptime(sec) {
  if (typeof sec !== "number" || !isFinite(sec) || sec <= 0) return "--";
  const days = Math.floor(sec / 86400);
  const hours = Math.floor((sec % 86400) / 3600);
  const mins = Math.floor((sec % 3600) / 60);
  if (days) return `${days} 天 ${hours} 小时`;
  if (hours) return `${hours} 小时 ${mins} 分`;
  return `${mins} 分钟`;
}

function fmtClock(ts) {
  if (!ts) return "--";
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

function truncate(text, len) {
  const s = String(text || "");
  return s.length > len ? `${s.slice(0, len - 1)}…` : s;
}

/* ------------------------------------------------------------------ 配色 */
const FALLBACK_PALETTES = [
  { id: "violet", name: "紫罗兰", primary: "#6750A4" },
  { id: "ocean", name: "海蓝", primary: "#00639B" },
  { id: "forest", name: "森林", primary: "#2E6B3E" },
  { id: "sunset", name: "日落橙", primary: "#9A4600" },
  { id: "sakura", name: "樱花粉", primary: "#8E4A5F" },
];

function hexToRgb(hex) {
  let value = String(hex || "").trim().replace("#", "");
  if (value.length === 3) {
    value = value
      .split("")
      .map((ch) => ch + ch)
      .join("");
  }
  if (!/^[0-9a-fA-F]{6}$/.test(value)) return null;
  return {
    r: parseInt(value.slice(0, 2), 16),
    g: parseInt(value.slice(2, 4), 16),
    b: parseInt(value.slice(4, 6), 16),
  };
}

function rgbToHex(r, g, b) {
  const part = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${part(r)}${part(g)}${part(b)}`;
}

function rgbToHsl(r, g, b) {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === rn) h = (gn - bn) / d + (gn < bn ? 6 : 0);
  else if (max === gn) h = (bn - rn) / d + 2;
  else h = (rn - gn) / d + 4;
  return { h: (h / 6) * 360, s, l };
}

function hslToRgb(h, s, l) {
  const hue = (((h % 360) + 360) % 360) / 360;
  if (s === 0) {
    const v = l * 255;
    return { r: v, g: v, b: v };
  }
  const hue2rgb = (p, q, t) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return {
    r: hue2rgb(p, q, hue + 1 / 3) * 255,
    g: hue2rgb(p, q, hue) * 255,
    b: hue2rgb(p, q, hue - 1 / 3) * 255,
  };
}

function mixHex(a, b, ratio) {
  const first = hexToRgb(a);
  const second = hexToRgb(b);
  if (!first || !second) return a;
  return rgbToHex(
    first.r + (second.r - first.r) * ratio,
    first.g + (second.g - first.g) * ratio,
    first.b + (second.b - first.b) * ratio
  );
}

function luminance(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return 0;
  const channel = (value) => {
    const n = value / 255;
    return n <= 0.03928 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(rgb.r) + 0.7152 * channel(rgb.g) + 0.0722 * channel(rgb.b);
}

function contrastOn(hex) {
  const level = luminance(hex);
  const white = 1.05 / (level + 0.05);
  const black = (level + 0.05) / 0.05;
  return white >= black ? "#FFFFFF" : "#1D1B20";
}

function shiftHue(hex, delta, saturationScale, lightness) {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  const hsl = rgbToHsl(rgb.r, rgb.g, rgb.b);
  const shifted = hslToRgb(
    hsl.h + delta,
    Math.min(0.9, Math.max(0.12, hsl.s * saturationScale + 0.1)),
    Math.min(0.7, Math.max(0.25, lightness))
  );
  return rgbToHex(shifted.r, shifted.g, shifted.b);
}

function buildScheme(primary, dark) {
  const main = dark ? mixHex(primary, "#FFFFFF", 0.45) : primary;
  const primaryContainer = dark
    ? mixHex(primary, "#000000", 0.28)
    : mixHex(primary, "#FFFFFF", 0.82);
  const secondaryBase = shiftHue(primary, 35, 0.45, dark ? 0.58 : 0.42);
  const secondary = dark ? mixHex(secondaryBase, "#FFFFFF", 0.3) : secondaryBase;
  const secondaryContainer = dark
    ? mixHex(secondaryBase, "#000000", 0.35)
    : mixHex(secondaryBase, "#FFFFFF", 0.8);
  const tertiaryBase = shiftHue(primary, -45, 0.5, dark ? 0.6 : 0.45);
  const tertiary = dark ? mixHex(tertiaryBase, "#FFFFFF", 0.3) : tertiaryBase;
  const tertiaryContainer = dark
    ? mixHex(tertiaryBase, "#000000", 0.35)
    : mixHex(tertiaryBase, "#FFFFFF", 0.8);
  return {
    primary: main,
    "on-primary": contrastOn(main),
    "primary-container": primaryContainer,
    "on-primary-container": contrastOn(primaryContainer),
    secondary,
    "on-secondary": contrastOn(secondary),
    "secondary-container": secondaryContainer,
    "on-secondary-container": contrastOn(secondaryContainer),
    tertiary,
    "on-tertiary": contrastOn(tertiary),
    "tertiary-container": tertiaryContainer,
    "on-tertiary-container": contrastOn(tertiaryContainer),
  };
}

function currentBranding() {
  return (S.settings && S.settings.branding) || {
    title: "SysMon",
    palette: "violet",
    primary: "#6750a4",
    wallpaper_opacity: 35,
    surface_mode: "theme",
    surface_color: "#f3edf7",
  };
}

function buildSurfaceScheme(primary, mode, customColor, dark) {
  const base = dark ? "#141218" : "#FFFFFF";
  const text = dark ? "#E6E0E9" : "#1D1B20";
  let surface;
  let lowest;
  let low;
  let container;
  let high;
  let highest;

  if (mode === "neutral") {
    if (dark) {
      surface = "#131316";
      lowest = "#0F0F12";
      low = "#1B1B1F";
      container = "#202024";
      high = "#2A2A2F";
      highest = "#35353B";
    } else {
      surface = "#FBFBFC";
      lowest = "#FFFFFF";
      low = "#F6F6F8";
      container = "#F1F1F3";
      high = "#ECECEF";
      highest = "#E6E6EA";
    }
  } else if (mode === "custom" && hexToRgb(customColor)) {
    const tone = customColor;
    surface = mixHex(tone, base, 0.15);
    lowest = mixHex(tone, base, 0.55);
    low = mixHex(tone, base, 0.35);
    container = tone;
    high = mixHex(tone, text, 0.04);
    highest = mixHex(tone, text, 0.08);
  } else {
    surface = mixHex(base, primary, dark ? 0.07 : 0.045);
    lowest = mixHex(base, primary, dark ? 0.03 : 0.02);
    low = mixHex(base, primary, dark ? 0.1 : 0.07);
    container = mixHex(base, primary, dark ? 0.14 : 0.11);
    high = mixHex(base, primary, dark ? 0.18 : 0.14);
    highest = mixHex(base, primary, dark ? 0.22 : 0.17);
  }

  const onSurface = contrastOn(surface);
  return {
    surface,
    topbar:
      mode === "custom"
        ? mixHex(customColor, base, 0.2)
        : mode === "neutral"
          ? surface
          : mixHex(base, primary, dark ? 0.1 : 0.07),
    "surface-container-lowest": lowest,
    "surface-container-low": low,
    "surface-container": container,
    "surface-container-high": high,
    "surface-container-highest": highest,
    "on-surface": onSurface,
    "on-surface-variant": mixHex(onSurface, surface, 0.3),
    outline: mixHex(onSurface, surface, 0.55),
    "outline-variant": mixHex(onSurface, surface, 0.78),
  };
}

function applyPalette() {
  const branding = currentBranding();
  const primary = hexToRgb(branding.primary) ? branding.primary : "#6750A4";
  const dark = document.documentElement.dataset.theme === "dark";
  const scheme = buildScheme(primary, dark);
  Object.entries(scheme).forEach(([key, value]) => {
    document.documentElement.style.setProperty(`--md-${key}`, value);
  });
  const mode = branding.surface_mode || "theme";
  const surfaces = buildSurfaceScheme(primary, mode, branding.surface_color || "#f3edf7", dark);
  Object.entries(surfaces).forEach(([key, value]) => {
    document.documentElement.style.setProperty(`--md-${key}`, value);
  });
  document.body.classList.toggle("custom-surfaces", mode === "custom");
}

function applyBranding() {
  const branding = currentBranding();
  const title = String(branding.title || "SysMon").trim() || "SysMon";
  document.title = title;
  $$(".brand-name").forEach((node) => {
    node.textContent = title;
  });
  applyPalette();
  applyWallpaper();
  applyIcon();
}

function applyWallpaper() {
  if (!dom.wallpaper) return;
  const assets = S.assets || {};
  const branding = currentBranding();
  if (assets.wallpaper) {
    dom.wallpaper.hidden = false;
    dom.wallpaper.style.backgroundImage = `url("${assets.wallpaper}")`;
    const opacity = Math.min(100, Math.max(0, Number(branding.wallpaper_opacity ?? 35)));
    dom.wallpaper.style.opacity = String(opacity / 100);
    document.body.classList.add("has-wallpaper");
  } else {
    dom.wallpaper.hidden = true;
    dom.wallpaper.style.backgroundImage = "";
    document.body.classList.remove("has-wallpaper");
  }
}

function applyIcon() {
  if (!dom.faviconLink) return;
  const assets = S.assets || {};
  dom.faviconLink.href = assets.icon || DEFAULT_FAVICON;
}


/* ------------------------------------------------------------------ DOM */
const dom = {
  boot: $("#boot"),
  bootMsg: $("#bootMsg"),
  bootRetry: $("#bootRetry"),

  auth: $("#view-auth"),
  loginForm: $("#loginForm"),
  setupForm: $("#setupForm"),
  loginUser: $("#loginUser"),
  loginPass: $("#loginPass"),
  loginError: $("#loginError"),
  setupToken: $("#setupToken"),
  setupUser: $("#setupUser"),
  setupPass: $("#setupPass"),
  setupPass2: $("#setupPass2"),
  setupError: $("#setupError"),

  app: $("#view-app"),
  admin: $("#view-admin"),
  hostLabel: $("#hostLabel"),
  connStatus: $("#connStatus"),
  connText: $("#connText"),
  refreshHint: $("#refreshHint"),
  themeBtn: $("#themeBtn"),
  adminBtn: $("#adminBtn"),
  logoutBtn: $("#logoutBtn"),
  loginBtn: $("#loginBtn"),
  roChip: $("#roChip"),
  authBack: $("#authBack"),
  setupBanner: $("#setupBanner"),
  bannerSetupBtn: $("#bannerSetupBtn"),

  brandTitle: $("#brandTitle"),
  titleCount: $("#titleCount"),
  iconPreview: $("#iconPreview"),
  iconFile: $("#iconFile"),
  iconRemove: $("#iconRemove"),
  wallPreview: $("#wallPreview"),
  wallFile: $("#wallFile"),
  wallRemove: $("#wallRemove"),
  wallOpacity: $("#wallOpacity"),
  wallOpacityOut: $("#wallOpacityOut"),
  paletteList: $("#paletteList"),
  customColor: $("#customColor"),
  customHex: $("#customHex"),
  surfaceSeg: $("#surfaceSeg"),
  surfaceCustom: $("#surfaceCustom"),
  surfaceColor: $("#surfaceColor"),
  surfaceHex: $("#surfaceHex"),
  wallpaper: $("#wallpaper"),
  faviconLink: $("#faviconLink"),
  pageSub: $("#pageSub"),
  lastUpdate: $("#lastUpdate"),
  dashboard: $("#dashboard"),
  emptyState: $("#emptyState"),
  emptyAdminBtn: $("#emptyAdminBtn"),
  versionLabel: $("#versionLabel"),
  footInterval: $("#footInterval"),

  backBtn: $("#backBtn"),
  dirtyHint: $("#dirtyHint"),
  saveBtn: $("#saveBtn"),
  moduleList: $("#moduleList"),
  refreshRange: $("#refreshRange"),
  refreshOut: $("#refreshOut"),
  themeSeg: $("#themeSeg"),
  gpuChip: $("#gpuChip"),
  gpuReason: $("#gpuReason"),
  rescanBtn: $("#rescanBtn"),
  gpuToggle: $("#gpuToggle"),
  gpuNote: $("#gpuNote"),
  passwordForm: $("#passwordForm"),
  pwCurrent: $("#pwCurrent"),
  pwNew: $("#pwNew"),
  pwNew2: $("#pwNew2"),
  pwMsg: $("#pwMsg"),
  aboutVersion: $("#aboutVersion"),
  aboutHost: $("#aboutHost"),
  aboutOs: $("#aboutOs"),
  aboutCpu: $("#aboutCpu"),
  aboutUptime: $("#aboutUptime"),
  aboutUser: $("#aboutUser"),

  snackbar: $("#snackbar"),
};

const DEFAULT_FAVICON = (dom.faviconLink && dom.faviconLink.getAttribute("href")) || "";

/* ------------------------------------------------------------------ 状态 */
const S = {
  authed: false,
  needsSetup: false,
  settings: null,
  catalog: [],
  palettes: [],
  assets: { icon: null, wallpaper: null },
  caps: null,
  host: null,
  metrics: null,
  version: "",
  username: "",
  cards: new Map(),
  sig: "",
  timer: null,
  pollMs: 5000,
  dirty: false,
  adminOpen: false,
};

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
let snackTimer = null;
let resizeTimer = null;

/* ------------------------------------------------------------------ API */
async function api(path, { method = "GET", body = null } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    const e = new Error("无法连接服务器");
    e.offline = true;
    throw e;
  }
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      data = null;
    }
  }
  if (!res.ok) {
    const e = new Error((data && data.error) || `请求失败（${res.status}）`);
    e.status = res.status;
    throw e;
  }
  return data || {};
}

/* ------------------------------------------------------------------ 主题 */
function resolveTheme(mode) {
  return mode === "dark" || (mode === "auto" && darkQuery.matches) ? "dark" : "light";
}

function applyTheme(mode) {
  document.documentElement.dataset.theme = resolveTheme(mode);
  applyPalette();
}

darkQuery.addEventListener("change", () => {
  if (S.settings && S.settings.theme === "auto") {
    applyTheme("auto");
    redrawAll();
  }
});

function setThemeSelection(mode) {
  $$("#themeSeg button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeValue === mode);
  });
}

function currentThemeValue() {
  const active = $("#themeSeg button.active");
  return active ? active.dataset.themeValue : (S.settings ? S.settings.theme : "auto");
}

/* ------------------------------------------------------------------ 提示条 */
function snack(message, isError = false) {
  const node = dom.snackbar;
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(snackTimer);
  snackTimer = setTimeout(() => node.classList.remove("show"), 2800);
}

/* ------------------------------------------------------------------ 认证视图 */
function showAuth(needsSetup) {
  S.authed = false;
  stopPolling();
  dom.boot.hidden = true;
  dom.app.hidden = true;
  dom.admin.hidden = true;
  dom.auth.hidden = false;
  dom.loginForm.hidden = Boolean(needsSetup);
  dom.setupForm.hidden = !needsSetup;
  dom.loginError.textContent = "";
  dom.setupError.textContent = "";
  if (needsSetup) {
    dom.setupToken.value = "";
    dom.setupUser.value = "admin";
    dom.setupPass.value = "";
    dom.setupPass2.value = "";
    setTimeout(() => dom.setupToken.focus(), 60);
  } else {
    if (!dom.loginUser.value) dom.loginUser.value = "admin";
    dom.loginPass.value = "";
    setTimeout(() => dom.loginPass.focus(), 60);
  }
}

async function withBusy(button, fn) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "请稍候…";
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function backToDashboard() {
  try {
    const state = await api("/api/state");
    enterApp(state);
  } catch (err) {
    dom.auth.hidden = true;
    dom.app.hidden = false;
    setConn(false);
  }
}

dom.loginBtn.addEventListener("click", () => showAuth(S.needsSetup));
dom.bannerSetupBtn.addEventListener("click", () => showAuth(true));
dom.authBack.addEventListener("click", backToDashboard);

dom.loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const button = dom.loginForm.querySelector("button[type=submit]");
  withBusy(button, async () => {
    dom.loginError.textContent = "";
    try {
      const state = await api("/api/login", {
        method: "POST",
        body: { username: dom.loginUser.value.trim(), password: dom.loginPass.value },
      });
      dom.loginPass.value = "";
      enterApp(state);
    } catch (err) {
      dom.loginError.textContent = err.message;
    }
  });
});

dom.setupForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const button = dom.setupForm.querySelector("button[type=submit]");
  const password = dom.setupPass.value;
  if (password.length < 8) {
    dom.setupError.textContent = "密码至少 8 位";
    return;
  }
  if (password !== dom.setupPass2.value) {
    dom.setupError.textContent = "两次输入的密码不一致";
    return;
  }
  withBusy(button, async () => {
    dom.setupError.textContent = "";
    try {
      const state = await api("/api/setup", {
        method: "POST",
        body: {
          token: dom.setupToken.value.trim(),
          username: dom.setupUser.value.trim(),
          password,
        },
      });
      dom.setupPass.value = "";
      dom.setupPass2.value = "";
      enterApp(state);
    } catch (err) {
      dom.setupError.textContent = err.message;
    }
  });
});

/* ------------------------------------------------------------------ 进入主界面 */
function enterApp(state) {
  S.authed = Boolean(state.authenticated);
  S.needsSetup = Boolean(state.needs_setup);
  S.settings = state.settings || S.settings;
  S.catalog = state.catalog || S.catalog;
  S.palettes = state.palettes && state.palettes.length ? state.palettes : FALLBACK_PALETTES;
  S.assets = state.assets || S.assets || { icon: null, wallpaper: null };
  S.caps = state.capabilities || S.caps;
  S.host = state.host || S.host;
  S.metrics = state.metrics || S.metrics;
  S.version = state.version || S.version;
  S.username = state.username || "";
  S.cards = new Map();
  S.sig = "";

  dom.boot.hidden = true;
  dom.auth.hidden = true;
  dom.admin.hidden = true;
  dom.app.hidden = false;

  applyTheme(S.settings.theme);
  applyBranding();
  updateChrome();
  renderDashboard(true);
  if (S.authed) renderAdmin();
  renderMeta();
  startPolling();
}

function updateAuthChrome() {
  dom.loginBtn.hidden = S.authed;
  dom.roChip.hidden = S.authed;
  dom.adminBtn.hidden = !S.authed;
  dom.logoutBtn.hidden = !S.authed;
  if (!S.authed) {
    dom.loginBtn.textContent = S.needsSetup ? "初始化管理" : "登录";
  }
  dom.setupBanner.hidden = S.authed || !S.needsSetup;
}

function updateChrome() {
  if (!S.settings) return;
  const host = S.host || {};
  if (host.hostname) {
    dom.hostLabel.textContent = `${host.hostname} · ${host.cores || "?"} 核`;
  }
  dom.versionLabel.textContent = `v${S.version || ""}`;
  dom.aboutVersion.textContent = `v${S.version || ""}`;
  dom.aboutUser.textContent = S.username || "—";
  dom.refreshHint.textContent = `${S.settings.refresh_seconds} 秒刷新`;
  dom.footInterval.textContent = S.settings.refresh_seconds;
  if (host.hostname) {
    dom.aboutHost.textContent = host.hostname;
    dom.aboutOs.textContent = host.os || "—";
    dom.aboutCpu.textContent = host.cpu_model || "—";
  }
  updateAuthChrome();
}

function renderMeta() {
  const m = S.metrics;
  if (!m) return;
  const host = S.host || {};
  dom.lastUpdate.textContent = m.ts ? `更新于 ${fmtClock(m.ts)}` : "—";
  dom.pageSub.textContent = `${host.os || "本机"} · ${host.hostname || ""} · 已运行 ${fmtUptime(m.uptime)}`;
  dom.aboutUptime.textContent = fmtUptime(m.uptime);
}

/* ------------------------------------------------------------------ 轮询 */
function setConn(online) {
  dom.connStatus.classList.toggle("online", online);
  dom.connStatus.classList.toggle("offline", !online);
  dom.connText.textContent = online ? "实时" : "重连中";
}

function stopPolling() {
  if (S.timer) {
    clearInterval(S.timer);
    S.timer = null;
  }
}

function restartTimer() {
  if (S.timer) clearInterval(S.timer);
  S.timer = setInterval(poll, S.pollMs);
}

function startPolling() {
  stopPolling();
  S.pollMs = Math.max(2, (S.settings && S.settings.refresh_seconds) || 5) * 1000;
  poll();
  S.timer = setInterval(poll, S.pollMs);
}

async function poll() {
  try {
    const data = await api("/api/metrics");
    const prevRefresh = S.settings ? S.settings.refresh_seconds : null;
    const changed = JSON.stringify(data.settings) !== JSON.stringify(S.settings);
    const previousAssets = JSON.stringify(S.assets);
    S.metrics = data.metrics;
    if (data.capabilities) S.caps = data.capabilities;
    if (data.settings) S.settings = data.settings;
    if (data.assets) S.assets = data.assets;
    const assetsChanged = JSON.stringify(S.assets) !== previousAssets;

    setConn(true);
    renderMeta();

    if (changed) {
      applyTheme(S.settings.theme);
      applyBranding();
      updateChrome();
      renderDashboard(true);
      if (!S.dirty) syncAdminControls();
    } else {
      if (assetsChanged) {
        applyIcon();
        applyWallpaper();
        if (S.authed) renderAssetPreviews();
      }
      renderDashboard(false);
    }

    if (prevRefresh !== S.settings.refresh_seconds) {
      S.pollMs = Math.max(2, S.settings.refresh_seconds) * 1000;
      restartTimer();
    }
  } catch (err) {
    setConn(false);
  }
}

function handleUnauthorized() {
  stopPolling();
  S.authed = false;
  S.username = "";
  S.adminOpen = false;
  clearDirty();
  dom.admin.hidden = true;
  updateAuthChrome();
  showAuth(false);
  snack("登录已过期，请重新登录", true);
}

/* ------------------------------------------------------------------ 仪表盘 */
function visibleModules() {
  if (!S.settings) return [];
  return S.settings.modules.filter(
    (m) => m.visible && (m.id !== "gpu" || S.settings.gpu_enabled)
  );
}

function renderDashboard(force = false) {
  const mods = visibleModules();
  const sig = mods.map((m) => `${m.id}:${m.chart}`).join("|");
  if (force || sig !== S.sig) {
    S.sig = sig;
    buildDashboard(mods);
  }
  dom.emptyState.hidden = mods.length > 0;
  dom.dashboard.hidden = mods.length === 0;
  updateAll();
}

function buildDashboard(mods) {
  dom.dashboard.innerHTML = "";
  S.cards = new Map();
  mods.forEach((mod) => {
    const card = buildCard(mod);
    dom.dashboard.appendChild(card.el);
    S.cards.set(mod.id, card);
  });
}

function buildCard(mod) {
  const meta = S.catalog.find((c) => c.id === mod.id) || { name: mod.id, hint: "" };
  const isGauge = mod.chart === "gauge";
  const article = document.createElement("article");
  article.className = "module card";
  article.dataset.id = mod.id;

  const head = document.createElement("div");
  head.className = "module-head";
  head.innerHTML = `<span class="module-icon" aria-hidden="true">${ICONS[mod.id] || ""}</span>
    <div class="module-title"><h2></h2><span class="module-hint"></span></div>
    <span class="module-mode">${isGauge ? "表盘" : "波形"}</span>`;
  head.querySelector("h2").textContent = meta.name;
  article.appendChild(head);

  const body = document.createElement("div");
  body.className = "module-body";
  const refs = { hint: head.querySelector(".module-hint") };

  if (mod.id === "net") {
    const nv = document.createElement("div");
    nv.className = "net-values";
    nv.innerHTML = `<div class="net-item"><span class="net-arrow down" aria-hidden="true">↓</span><span class="net-speed" data-ref="down">--</span></div>
      <div class="net-item"><span class="net-arrow up" aria-hidden="true">↑</span><span class="net-speed" data-ref="up">--</span></div>`;
    refs.down = nv.querySelector('[data-ref="down"]');
    refs.up = nv.querySelector('[data-ref="up"]');
    body.appendChild(nv);
  } else {
    const row = document.createElement("div");
    row.className = "value-row";
    if (isGauge) row.hidden = true;
    row.innerHTML = '<span class="value" data-ref="value">--</span><span class="unit" data-ref="unit"></span>';
    refs.value = row.querySelector('[data-ref="value"]');
    refs.unit = row.querySelector('[data-ref="unit"]');
    body.appendChild(row);
  }

  const sub = document.createElement("div");
  sub.className = "sub";
  sub.textContent = "正在读取…";
  refs.sub = sub;
  body.appendChild(sub);

  const viz = document.createElement("div");
  viz.className = "viz";
  let svg = null;
  let canvas = null;
  if (isGauge) {
    viz.innerHTML = GAUGE_SVG;
    svg = viz.firstElementChild;
    refs.gauge = svg;
  } else {
    canvas = document.createElement("canvas");
    canvas.className = "wave";
    canvas.setAttribute("aria-hidden", "true");
    viz.appendChild(canvas);
  }
  body.appendChild(viz);

  if (!isGauge) {
    const foot = document.createElement("div");
    foot.className = "wave-foot";
    foot.innerHTML = "<span>近 2 分钟</span><span>现在</span>";
    body.appendChild(foot);
  }

  const extra = document.createElement("div");
  extra.className = "extra";
  if (mod.id === "net") {
    extra.innerHTML = '<div class="legend"><span><i class="rx"></i>下载</span><span><i class="tx"></i>上传</span></div>';
  } else if (mod.id === "cpu") {
    extra.innerHTML = '<div class="cores"></div>';
    refs.cores = extra.firstElementChild;
  } else if (mod.id === "disk") {
    extra.innerHTML = '<div class="parts"></div>';
    refs.parts = extra.firstElementChild;
  }
  body.appendChild(extra);

  const hw = document.createElement("div");
  hw.className = "hw";
  hw.hidden = true;
  refs.hw = hw;
  body.appendChild(hw);

  article.appendChild(body);

  return { el: article, mod, refs, canvas, svg };
}

function updateAll() {
  if (!S.metrics || S.metrics.warming_up) return;
  S.cards.forEach((card, id) => updateCard(id, card));
}

function updateCard(id, card) {
  const m = S.metrics;
  switch (id) {
    case "cpu":
      updateCpu(card, m);
      break;
    case "memory":
      updateMemory(card, m);
      break;
    case "disk":
      updateDisk(card, m);
      break;
    case "gpu":
      updateGpu(card, m);
      break;
    case "net":
      updateNet(card, m);
      break;
    default:
      break;
  }
  updateHardware(card, id);
}

function fmtLinkSpeed(mbps) {
  if (!mbps || !isFinite(mbps)) return "";
  if (mbps >= 1000) {
    const gbps = mbps / 1000;
    return `${Number.isInteger(gbps) ? gbps : gbps.toFixed(1)} Gbps`;
  }
  return `${Math.round(mbps)} Mbps`;
}

function hardwareContent(id, hardware) {
  const hw = hardware || {};
  if (id === "cpu") {
    const cpu = hw.cpu || {};
    if (!cpu.model) return null;
    const cores = cpu.physical ? ` · ${cpu.physical} 核` : "";
    return { text: `CPU：${truncate(cpu.model, 70)}${cores}`, items: [], count: 0 };
  }
  if (id === "memory") {
    const mem = hw.memory || {};
    return mem.summary ? { text: `内存：${mem.summary}`, items: [], count: 0 } : null;
  }
  if (id === "disk") {
    const disks = hw.disks || [];
    if (!disks.length) return null;
    const describe = (disk, withSize) => {
      const kind = disk.kind ? `（${disk.kind}）` : "";
      const size = withSize && typeof disk.size_gb === "number" ? ` · ${Math.round(disk.size_gb)} GB` : "";
      return `${disk.name}${kind}${size}`;
    };
    const first = disks[0];
    return {
      text: `磁盘：${truncate(describe(first, false), 48)}`,
      items: disks.length > 1 ? disks.map((disk) => describe(disk, true)) : [],
      count: disks.length,
    };
  }
  if (id === "net") {
    const nics = hw.nics || [];
    if (!nics.length) return null;
    const nic = nics[0];
    const speed = fmtLinkSpeed(nic.speed_mbps);
    const iface = nic.interface && nic.interface !== nic.name ? ` · ${nic.interface}` : "";
    return { text: `网卡：${truncate(nic.name, 40)}${iface}${speed ? ` · ${speed}` : ""}`, items: [], count: 0 };
  }
  return null;
}

function renderHardware(host, content, card) {
  host.innerHTML = "";
  const line = document.createElement("div");
  line.className = "hw-line";

  const text = document.createElement("span");
  text.className = "hw-text";
  text.textContent = content.text;
  line.appendChild(text);

  if (content.items.length) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "hw-toggle";
    button.setAttribute("aria-expanded", String(Boolean(card.hwExpanded)));
    button.textContent = card.hwExpanded ? "收起" : `${content.count} 块 · 展开`;
    button.addEventListener("click", () => {
      card.hwExpanded = !card.hwExpanded;
      renderHardware(host, content, card);
    });
    line.appendChild(button);
  }
  host.appendChild(line);

  if (content.items.length) {
    const list = document.createElement("ul");
    list.className = "hw-list";
    list.hidden = !card.hwExpanded;
    content.items.forEach((item) => {
      const entry = document.createElement("li");
      entry.textContent = item;
      list.appendChild(entry);
    });
    host.appendChild(list);
  }
}

function updateHardware(card, id) {
  if (!card.refs.hw) return;
  const mod = S.settings ? S.settings.modules.find((item) => item.id === id) : null;
  const content = mod && mod.hardware ? hardwareContent(id, S.metrics && S.metrics.hardware) : null;
  const host = card.refs.hw;
  if (!content) {
    host.hidden = true;
    host.innerHTML = "";
    host.dataset.sig = "";
    card.hwExpanded = false;
    return;
  }
  const signature = JSON.stringify(content);
  if (host.dataset.sig === signature) {
    host.hidden = false;
    return;
  }
  host.dataset.sig = signature;
  host.hidden = false;
  renderHardware(host, content, card);
}

function applyLevel(card, pct) {
  const value = typeof pct === "number" ? pct : 0;
  card.el.classList.toggle("warn", value >= 75 && value < 90);
  card.el.classList.toggle("crit", value >= 90);
}

function setGauge(card, pct) {
  if (!card.svg) return;
  const ok = typeof pct === "number" && isFinite(pct);
  const value = ok ? clamp(pct, 0, 100) : 0;
  const arc = card.svg.querySelector(".gauge-value");
  arc.setAttribute("stroke-dasharray", `${((75 * value) / 100).toFixed(2)} 100`);
  card.svg.querySelector(".gauge-num").textContent = ok ? `${Math.round(value)}%` : "--";
  card.svg.classList.toggle("warn", ok && value >= 75 && value < 90);
  card.svg.classList.toggle("crit", ok && value >= 90);
}

function updateCpu(card, m) {
  const cpu = m.cpu;
  const pct = cpu.percent;
  card.refs.hint.textContent = `${cpu.cores} 核${cpu.physical ? ` · ${cpu.physical} 物理` : ""}`;

  if (card.svg) {
    setGauge(card, pct);
  } else {
    card.refs.value.textContent = fmtPct(pct);
    card.refs.unit.textContent = "";
    drawWave(card.canvas, [{ data: m.history.cpu, color: readColors().primary }], 100);
  }

  const bits = [];
  if (cpu.load && cpu.load.length) bits.push(`负载 ${cpu.load[0].toFixed(2)}`);
  if (cpu.freq_ghz) bits.push(`${cpu.freq_ghz} GHz`);
  if (cpu.temp_c != null) bits.push(`${cpu.temp_c}°C`);
  if (cpu.processes) bits.push(`${cpu.processes} 进程`);
  card.refs.sub.textContent = bits.join(" · ") || "—";

  renderCores(card, cpu.per_core || []);
  applyLevel(card, pct);
}

function renderCores(card, cores) {
  const wrap = card.refs.cores;
  if (!wrap) return;
  const list = cores.slice(0, 64);
  if (wrap.children.length !== list.length) {
    wrap.innerHTML = list.map(() => '<span class="core"><i></i></span>').join("");
  }
  list.forEach((value, index) => {
    const bar = wrap.children[index].firstElementChild;
    if (bar) bar.style.height = `${clamp(value, 1, 100)}%`;
  });
}

function updateMemory(card, m) {
  const mem = m.memory;
  const pct = mem.percent;
  card.refs.hint.textContent = `${fmtBytes(mem.total)}`;
  if (card.svg) {
    setGauge(card, pct);
  } else {
    card.refs.value.textContent = fmtPct(pct);
    card.refs.unit.textContent = "已用";
    drawWave(card.canvas, [{ data: m.history.memory, color: readColors().primary }], 100);
  }
  const swap = mem.swap_total ? ` · 交换 ${fmtPct(mem.swap_percent)}` : "";
  card.refs.sub.textContent = `${fmtBytes(mem.used)} 已用 · ${fmtBytes(mem.available)} 可用${swap}`;
  applyLevel(card, pct);
}

function updateDisk(card, m) {
  const disk = m.disk;
  const pct = typeof disk.max_percent === "number" ? disk.max_percent : disk.percent;
  card.refs.hint.textContent = `${disk.parts.length} 个分区`;
  if (card.svg) {
    setGauge(card, pct);
  } else {
    card.refs.value.textContent = fmtPct(pct);
    card.refs.unit.textContent = "最高占用";
    drawWave(card.canvas, [{ data: m.history.disk, color: readColors().primary }], 100);
  }
  card.refs.sub.textContent = `合计 ${fmtBytes(disk.used)} / ${fmtBytes(disk.total)} · 读 ${fmtSpeed(
    disk.read_bps
  )} · 写 ${fmtSpeed(disk.write_bps)}`;
  renderParts(card, disk.parts || []);
  applyLevel(card, pct);
}

function renderParts(card, parts) {
  const wrap = card.refs.parts;
  if (!wrap) return;
  const list = parts.slice(0, 4);
  if (wrap.children.length !== list.length) {
    wrap.innerHTML = list
      .map(
        () =>
          '<div class="part"><span class="part-name"></span><span class="part-pct"></span><div class="bar"><i></i></div></div>'
      )
      .join("");
  }
  list.forEach((part, index) => {
    const row = wrap.children[index];
    if (!row) return;
    row.querySelector(".part-name").textContent = part.mount;
    row.querySelector(".part-pct").textContent = fmtPct(part.percent);
    const bar = row.querySelector(".bar i");
    bar.style.width = `${clamp(part.percent, 0, 100)}%`;
    bar.className = part.percent >= 90 ? "crit" : part.percent >= 75 ? "warn" : "";
  });
}

function updateGpu(card, m) {
  const gpu = m.gpu;
  const device = (gpu.devices && gpu.devices[0]) || null;
  const pct = gpu.util;
  card.refs.hint.textContent = device ? truncate(device.name, 26) : "GPU";

  if (card.svg) {
    setGauge(card, pct);
  } else {
    card.refs.value.textContent = fmtPct(pct);
    card.refs.unit.textContent = "使用率";
    drawWave(card.canvas, [{ data: m.history.gpu, color: readColors().primary }], 100);
  }

  if (device) {
    const used = typeof device.mem_used_mb === "number" ? device.mem_used_mb * 1024 * 1024 : null;
    const total = typeof device.mem_total_mb === "number" ? device.mem_total_mb * 1024 * 1024 : null;
    const bits = [];
    if (used != null && total != null) bits.push(`${fmtBytes(used)} / ${fmtBytes(total)} 显存`);
    if (device.temp_c != null) bits.push(`${Math.round(device.temp_c)}°C`);
    if (device.power_w != null) bits.push(`${Math.round(device.power_w)} W`);
    card.refs.sub.textContent = bits.join(" · ") || "共享显存";
  } else {
    card.refs.sub.textContent = "未读取到 GPU 指标";
  }
  applyLevel(card, pct);
}

function updateNet(card, m) {
  const net = m.net;
  card.refs.down.textContent = fmtSpeed(net.down_bps);
  card.refs.up.textContent = fmtSpeed(net.up_bps);
  card.refs.hint.textContent = `↓ ${fmtSpeed(net.down_bps)}`;
  card.refs.sub.textContent = `累计 ↓ ${fmtBytes(net.rx_total)} · ↑ ${fmtBytes(net.tx_total)}`;
  const colors = readColors();
  drawWave(
    card.canvas,
    [
      { data: m.history.net_down, color: colors.primary },
      { data: m.history.net_up, color: colors.tertiary },
    ],
    "auto"
  );
}

function redrawAll() {
  updateAll();
}

/* ------------------------------------------------------------------ 图表 */
function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const pick = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
  return {
    primary: pick("--md-primary", "#6750A4"),
    tertiary: pick("--md-tertiary", "#7D5260"),
    surface: pick("--md-surface", "#FFFFFF"),
    grid: pick("--md-outline-variant", "#CAC4D0"),
  };
}

function withAlpha(color, alpha) {
  const value = String(color).trim();
  if (value.startsWith("#")) {
    let hex = value.slice(1);
    if (hex.length === 3) {
      hex = hex
        .split("")
        .map((ch) => ch + ch)
        .join("");
    }
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    if ([r, g, b].every((n) => Number.isFinite(n))) {
      return `rgba(${r},${g},${b},${alpha})`;
    }
  }
  return value;
}

function cleanSeries(data) {
  const out = [];
  let last = 0;
  (data || []).forEach((value) => {
    if (typeof value === "number" && isFinite(value)) last = Math.max(0, value);
    out.push(last);
  });
  return out;
}

function drawWave(canvas, series, maxValue) {
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const colors = readColors();
  const padX = 2;
  const padTop = 8;
  const padBottom = 8;
  const usable = height - padTop - padBottom;

  ctx.save();
  ctx.strokeStyle = colors.grid;
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 5]);
  for (let i = 1; i <= 3; i += 1) {
    const y = padTop + (usable * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padX, y);
    ctx.lineTo(width - padX, y);
    ctx.stroke();
  }
  ctx.restore();

  const prepared = series.map((item) => ({ data: cleanSeries(item.data), color: item.color }));
  const all = prepared.flatMap((item) => item.data);
  if (!all.length) return;

  let max;
  if (typeof maxValue === "number") {
    max = maxValue;
  } else {
    max = Math.max(1, ...all) * 1.15;
  }
  max = Math.max(max, 1);

  const xAt = (index, count) =>
    count <= 1 ? width / 2 : padX + ((width - padX * 2) * index) / (count - 1);
  const yAt = (value) => padTop + usable - (clamp(value, 0, max) / max) * usable;

  prepared.forEach((item) => {
    const data = item.data;
    if (data.length < 2) return;
    const points = data.map((value, index) => [xAt(index, data.length), yAt(value)]);

    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) {
      const [x0, y0] = points[i - 1];
      const [x1, y1] = points[i];
      const mid = (x0 + x1) / 2;
      ctx.bezierCurveTo(mid, y0, mid, y1, x1, y1);
    }
    ctx.lineTo(width - padX, height - padBottom);
    ctx.lineTo(padX, height - padBottom);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, padTop, 0, height - padBottom);
    gradient.addColorStop(0, withAlpha(item.color, 0.18));
    gradient.addColorStop(1, withAlpha(item.color, 0));
    ctx.fillStyle = gradient;
    ctx.fill();
  });

  prepared.forEach((item) => {
    const data = item.data;
    if (!data.length) return;
    const points = data.map((value, index) => [xAt(index, data.length), yAt(value)]);
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) {
      const [x0, y0] = points[i - 1];
      const [x1, y1] = points[i];
      const mid = (x0 + x1) / 2;
      ctx.bezierCurveTo(mid, y0, mid, y1, x1, y1);
    }
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();

    const [lx, ly] = points[points.length - 1];
    ctx.beginPath();
    ctx.arc(lx, ly, 3, 0, Math.PI * 2);
    ctx.fillStyle = colors.surface;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = item.color;
    ctx.stroke();
  });
}

/* ------------------------------------------------------------------ 管理面板 */
function openAdmin() {
  if (!S.authed) {
    showAuth(false);
    return;
  }
  clearDirty();
  S.adminOpen = true;
  dom.app.hidden = true;
  dom.admin.hidden = false;
  renderAdmin();
  window.scrollTo(0, 0);
}

function closeAdmin(force = false) {
  if (!force && S.dirty && !window.confirm("有未保存的更改，确定离开吗？")) return;
  clearDirty();
  S.adminOpen = false;
  dom.admin.hidden = true;
  dom.app.hidden = false;
  renderDashboard(true);
  window.scrollTo(0, 0);
}

function markDirty() {
  if (!S.dirty) {
    S.dirty = true;
    dom.saveBtn.disabled = false;
    dom.dirtyHint.hidden = false;
  }
}

function clearDirty() {
  S.dirty = false;
  dom.saveBtn.disabled = true;
  dom.dirtyHint.hidden = true;
}

function renderAdmin() {
  if (!S.settings) return;
  dom.moduleList.innerHTML = "";
  S.settings.modules.forEach((mod) => {
    const meta = S.catalog.find((c) => c.id === mod.id) || { name: mod.id, hint: "", charts: ["wave"] };
    const row = document.createElement("li");
    row.className = "module-row";
    row.draggable = true;
    row.dataset.id = mod.id;

    const charts = meta.charts || ["wave"];
    const options = charts
      .map((chart) => `<option value="${chart}"${chart === mod.chart ? " selected" : ""}>${chart === "gauge" ? "表盘" : "波形"}</option>`)
      .join("");
    const hardwareSwitch = meta.hardware
      ? `<label class="switch hw-switch" title="在卡片底部显示硬件信息"><input type="checkbox" class="hw" aria-label="显示硬件信息"${mod.hardware ? " checked" : ""}><span class="track" aria-hidden="true"></span></label>`
      : '<span class="switch-placeholder" aria-hidden="true"></span>';

    row.innerHTML = `<span class="drag" title="拖动排序" aria-hidden="true">${ICONS.drag}</span>
      <span class="row-icon" aria-hidden="true">${ICONS[mod.id] || ""}</span>
      <div class="row-name"><strong></strong><small></small></div>
      <select class="select" aria-label="显示方式">${options}</select>
      <label class="switch" title="显示模块"><input type="checkbox" class="vis" aria-label="显示模块"><span class="track" aria-hidden="true"></span></label>
      ${hardwareSwitch}
      <div class="row-btns">
        <button class="mini" type="button" data-act="up" aria-label="上移">${ICONS.up}</button>
        <button class="mini" type="button" data-act="down" aria-label="下移">${ICONS.down}</button>
      </div>`;
    row.querySelector(".row-name strong").textContent = meta.name;
    row.querySelector(".row-name small").textContent = meta.hint || "";
    const vis = row.querySelector(".vis");
    vis.checked = Boolean(mod.visible);
    dom.moduleList.appendChild(row);
  });
  syncAdminControls();
}

function syncAdminControls() {
  if (!S.settings || !S.authed) return;
  dom.refreshRange.value = S.settings.refresh_seconds;
  dom.refreshOut.textContent = `${S.settings.refresh_seconds} 秒`;
  setThemeSelection(S.settings.theme);

  const gpu = (S.caps && S.caps.gpu) || { supported: false, reason: "尚未检测" };
  const supported = Boolean(gpu.supported);
  const vendors = { nvidia: "NVIDIA", amd: "AMD", intel: "Intel" };
  const vendorLabel = vendors[gpu.vendor] || "GPU";
  dom.gpuChip.textContent = supported ? `已支持 ${vendorLabel}` : "不支持";
  dom.gpuChip.className = `chip ${supported ? "ok" : "bad"}`;
  dom.gpuReason.textContent = supported
    ? `检测到 ${gpu.device_count} 块 ${vendorLabel} GPU`
    : gpu.reason || "未检测到可用 GPU";
  dom.gpuToggle.disabled = !supported;
  dom.gpuToggle.checked = supported && Boolean(S.settings.gpu_enabled);
  dom.gpuNote.textContent = supported
    ? "开启后会在面板上显示 GPU 模块，可在模块列表里调整显示方式或隐藏。"
    : "当前主机没有可采集的 GPU，模块默认关闭且无法开启；安装驱动或采集工具后可点击「重新检测」。";

  updateGpuRowState();
  if (S.adminOpen) renderBranding();
}

function ensureBranding() {
  if (!S.settings) return currentBranding();
  if (!S.settings.branding) S.settings.branding = { ...currentBranding() };
  return S.settings.branding;
}

function updateTitleCount() {
  dom.titleCount.textContent = `${dom.brandTitle.value.length}/40`;
}

function renderPaletteList() {
  if (!dom.paletteList) return;
  const branding = currentBranding();
  const palettes = S.palettes.length ? S.palettes : FALLBACK_PALETTES;
  dom.paletteList.innerHTML = palettes
    .map(
      (preset) =>
        `<button class="palette${branding.palette === preset.id ? " active" : ""}" type="button" data-palette="${preset.id}">` +
        `<span class="palette-dot" data-color="${preset.primary}"></span><span>${preset.name}</span></button>`
    )
    .join("");
  $$(".palette-dot", dom.paletteList).forEach((dot) => {
    dot.style.background = dot.dataset.color;
  });
}

function renderAssetPreviews() {
  const assets = S.assets || {};
  if (dom.iconPreview) dom.iconPreview.src = assets.icon || DEFAULT_FAVICON;
  if (dom.wallPreview) {
    dom.wallPreview.hidden = !assets.wallpaper;
    dom.wallPreview.src = assets.wallpaper || "";
  }
  dom.iconRemove.disabled = !assets.icon;
  dom.wallRemove.disabled = !assets.wallpaper;
}

function renderBranding() {
  const branding = currentBranding();
  dom.brandTitle.value = branding.title || "SysMon";
  updateTitleCount();
  const opacity = Math.min(100, Math.max(0, Number(branding.wallpaper_opacity ?? 35)));
  dom.wallOpacity.value = String(opacity);
  dom.wallOpacityOut.textContent = `${opacity}%`;
  const custom = branding.palette === "custom" ? branding.primary : hexToRgb(dom.customColor.value) ? dom.customColor.value : "#6750A4";
  dom.customColor.value = custom;
  dom.customHex.value = custom;

  const surfaceMode = branding.surface_mode || "theme";
  $$("#surfaceSeg button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.surface === surfaceMode);
  });
  dom.surfaceCustom.hidden = surfaceMode !== "custom";
  const surfaceColor = branding.surface_color || "#f3edf7";
  dom.surfaceColor.value = surfaceColor;
  dom.surfaceHex.value = surfaceColor;

  renderPaletteList();
  renderAssetPreviews();
}

function selectPalette(id, primary) {
  const branding = ensureBranding();
  branding.palette = id;
  branding.primary = primary;
  applyBranding();
  $$("#paletteList .palette").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.palette === id);
  });
  redrawAll();
  markDirty();
}

function useCustomColor(hex, syncHex = true) {
  const value = String(hex || "").trim().toLowerCase();
  if (!hexToRgb(value)) return;
  const branding = ensureBranding();
  branding.palette = "custom";
  branding.primary = value;
  dom.customColor.value = value;
  if (syncHex) dom.customHex.value = value;
  applyBranding();
  $$("#paletteList .palette").forEach((btn) => btn.classList.remove("active"));
  redrawAll();
  markDirty();
}

function setSurfaceColor(hex, syncHex = true) {
  const value = String(hex || "").trim().toLowerCase();
  if (!hexToRgb(value)) return;
  const branding = ensureBranding();
  branding.surface_mode = "custom";
  branding.surface_color = value;
  dom.surfaceColor.value = value;
  if (syncHex) dom.surfaceHex.value = value;
  $$("#surfaceSeg button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.surface === "custom");
  });
  dom.surfaceCustom.hidden = false;
  applyBranding();
  redrawAll();
  markDirty();
}

async function uploadBranding(kind, file) {
  if (!file) return;
  const limit = kind === "icon" ? 512 * 1024 : 8 * 1024 * 1024;
  if (file.size > limit) {
    snack(`文件过大，上限 ${Math.round(limit / 1024)} KB`, true);
    return;
  }
  try {
    const buffer = await file.arrayBuffer();
    const response = await fetch(`/api/branding/${kind}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: buffer,
    });
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok) {
      const error = new Error((data && data.error) || `上传失败（${response.status}）`);
      error.status = response.status;
      throw error;
    }
    S.assets = (data && data.assets) || S.assets;
    applyBranding();
    renderAssetPreviews();
    snack(kind === "icon" ? "图标已更新" : "壁纸已更新");
  } catch (err) {
    snack(err.message, true);
    if (err.status === 401) handleUnauthorized();
  }
}

async function removeBranding(kind) {
  try {
    const response = await fetch(`/api/branding/${kind}/remove`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok) {
      const error = new Error((data && data.error) || `操作失败（${response.status}）`);
      error.status = response.status;
      throw error;
    }
    S.assets = (data && data.assets) || { icon: null, wallpaper: null };
    applyBranding();
    renderAssetPreviews();
    snack(kind === "icon" ? "已恢复默认图标" : "已移除壁纸");
  } catch (err) {
    snack(err.message, true);
    if (err.status === 401) handleUnauthorized();
  }
}

function updateGpuRowState() {
  const row = dom.moduleList.querySelector('.module-row[data-id="gpu"]');
  if (!row) return;
  const supported = Boolean(S.caps && S.caps.gpu && S.caps.gpu.supported);
  const enabled = dom.gpuToggle.checked && supported;
  const vis = row.querySelector(".vis");
  const select = row.querySelector(".select");
  vis.disabled = !enabled;
  select.disabled = !supported;
  if (!enabled) vis.checked = false;
}

function collectSettings() {
  const rows = $$("#moduleList .module-row");
  const branding = currentBranding();
  return {
    refresh_seconds: Number(dom.refreshRange.value),
    theme: currentThemeValue(),
    gpu_enabled: dom.gpuToggle.checked,
    branding: {
      title: dom.brandTitle.value.trim() || "SysMon",
      palette: branding.palette,
      primary: branding.primary,
      wallpaper_opacity: Number(dom.wallOpacity.value),
      surface_mode: branding.surface_mode || "theme",
      surface_color: branding.surface_color || "#f3edf7",
    },
    modules: rows.map((row) => ({
      id: row.dataset.id,
      visible: row.querySelector(".vis").checked,
      chart: row.querySelector(".select").value,
      hardware: Boolean(row.querySelector(".hw") && row.querySelector(".hw").checked),
    })),
  };
}

async function saveSettings() {
  if (!S.settings) return;
  dom.saveBtn.disabled = true;
  try {
    const data = await api("/api/config", { method: "POST", body: { settings: collectSettings() } });
    S.settings = data.settings;
    if (data.capabilities) S.caps = data.capabilities;
    applyTheme(S.settings.theme);
    applyBranding();
    clearDirty();
    updateChrome();
    renderAdmin();
    renderDashboard(true);
    startPolling();
    snack("设置已保存");
  } catch (err) {
    dom.saveBtn.disabled = false;
    snack(err.message, true);
    if (err.status === 401) handleUnauthorized();
  }
}

async function rescanGpu() {
  dom.rescanBtn.disabled = true;
  const label = dom.rescanBtn.textContent;
  dom.rescanBtn.textContent = "检测中…";
  try {
    const data = await api("/api/gpu/rescan", { method: "POST", body: {} });
    S.caps = data.capabilities || S.caps;
    S.settings = data.settings || S.settings;
    if (S.dirty) {
      syncAdminControls();
    } else {
      renderAdmin();
    }
    renderDashboard(true);
    const vendors = { nvidia: "NVIDIA", amd: "AMD", intel: "Intel" };
    const vendorLabel = vendors[S.caps && S.caps.gpu && S.caps.gpu.vendor] || "GPU";
    snack(
      S.caps && S.caps.gpu.supported
        ? `检测到 ${vendorLabel} GPU`
        : "仍未检测到可采集的 GPU"
    );
  } catch (err) {
    snack(err.message, true);
    if (err.status === 401) handleUnauthorized();
  } finally {
    dom.rescanBtn.disabled = false;
    dom.rescanBtn.textContent = label;
  }
}

function setPwMsg(message, isError) {
  dom.pwMsg.textContent = message;
  dom.pwMsg.className = `muted small${isError ? " bad" : message ? " ok" : ""}`;
}

dom.passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setPwMsg("", false);
  const current = dom.pwCurrent.value;
  const next = dom.pwNew.value;
  if (next.length < 8) {
    setPwMsg("新密码至少 8 位", true);
    return;
  }
  if (next !== dom.pwNew2.value) {
    setPwMsg("两次输入的新密码不一致", true);
    return;
  }
  const button = dom.passwordForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api("/api/password", { method: "POST", body: { current, new: next } });
    dom.passwordForm.reset();
    setPwMsg("密码已更新", false);
    snack("密码已更新");
  } catch (err) {
    setPwMsg(err.message, true);
    if (err.status === 401) handleUnauthorized();
  } finally {
    button.disabled = false;
  }
});

/* ------------------------------------------------------------------ 事件绑定 */
function wireAdminEvents() {
  dom.moduleList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const row = button.closest(".module-row");
    if (!row) return;
    if (button.dataset.act === "up" && row.previousElementSibling) {
      dom.moduleList.insertBefore(row, row.previousElementSibling);
      markDirty();
    } else if (button.dataset.act === "down" && row.nextElementSibling) {
      dom.moduleList.insertBefore(row.nextElementSibling, row);
      markDirty();
    }
  });

  dom.moduleList.addEventListener("change", (event) => {
    if (event.target.matches(".vis, .select, .hw")) markDirty();
  });

  let dragRow = null;
  dom.moduleList.addEventListener("dragstart", (event) => {
    const row = event.target.closest(".module-row");
    if (!row) return;
    dragRow = row;
    row.classList.add("dragging");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", row.dataset.id);
    }
  });

  dom.moduleList.addEventListener("dragend", () => {
    if (dragRow) dragRow.classList.remove("dragging");
    dragRow = null;
  });

  dom.moduleList.addEventListener("dragover", (event) => {
    if (!dragRow) return;
    event.preventDefault();
    const row = event.target.closest(".module-row");
    if (!row || row === dragRow) return;
    const rect = row.getBoundingClientRect();
    const after = event.clientY - rect.top > rect.height / 2;
    dom.moduleList.insertBefore(dragRow, after ? row.nextElementSibling : row);
    markDirty();
  });

  dom.moduleList.addEventListener("drop", (event) => event.preventDefault());

  dom.gpuToggle.addEventListener("change", () => {
    const supported = Boolean(S.caps && S.caps.gpu && S.caps.gpu.supported);
    const enabled = dom.gpuToggle.checked && supported;
    updateGpuRowState();
    if (enabled) {
      const row = dom.moduleList.querySelector('.module-row[data-id="gpu"]');
      const vis = row && row.querySelector(".vis");
      if (vis && !vis.disabled) vis.checked = true;
    }
    markDirty();
  });

  dom.refreshRange.addEventListener("input", () => {
    dom.refreshOut.textContent = `${dom.refreshRange.value} 秒`;
    markDirty();
  });

  dom.themeSeg.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-theme-value]");
    if (!button) return;
    setThemeSelection(button.dataset.themeValue);
    applyTheme(button.dataset.themeValue);
    redrawAll();
    markDirty();
  });

  dom.brandTitle.addEventListener("input", () => {
    const branding = ensureBranding();
    branding.title = dom.brandTitle.value.slice(0, 40);
    updateTitleCount();
    applyBranding();
    markDirty();
  });

  dom.wallOpacity.addEventListener("input", () => {
    const branding = ensureBranding();
    branding.wallpaper_opacity = Number(dom.wallOpacity.value);
    dom.wallOpacityOut.textContent = `${branding.wallpaper_opacity}%`;
    applyWallpaper();
    markDirty();
  });

  dom.paletteList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-palette]");
    if (!button) return;
    const preset = S.palettes.find((item) => item.id === button.dataset.palette);
    if (preset) selectPalette(preset.id, preset.primary);
  });

  dom.customColor.addEventListener("input", () => useCustomColor(dom.customColor.value));

  dom.customHex.addEventListener("input", () => {
    const value = dom.customHex.value.trim();
    if (/^#[0-9a-fA-F]{6}$/.test(value)) useCustomColor(value, false);
  });

  dom.surfaceSeg.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-surface]");
    if (!button) return;
    const branding = ensureBranding();
    branding.surface_mode = button.dataset.surface;
    $$("#surfaceSeg button").forEach((btn) => {
      btn.classList.toggle("active", btn === button);
    });
    dom.surfaceCustom.hidden = button.dataset.surface !== "custom";
    applyBranding();
    redrawAll();
    markDirty();
  });

  dom.surfaceColor.addEventListener("input", () => setSurfaceColor(dom.surfaceColor.value));

  dom.surfaceHex.addEventListener("input", () => {
    const value = dom.surfaceHex.value.trim();
    if (/^#[0-9a-fA-F]{6}$/.test(value)) setSurfaceColor(value, false);
  });

  dom.iconFile.addEventListener("change", () => {
    const file = dom.iconFile.files && dom.iconFile.files[0];
    uploadBranding("icon", file);
    dom.iconFile.value = "";
  });

  dom.wallFile.addEventListener("change", () => {
    const file = dom.wallFile.files && dom.wallFile.files[0];
    uploadBranding("wallpaper", file);
    dom.wallFile.value = "";
  });

  dom.iconRemove.addEventListener("click", () => removeBranding("icon"));
  dom.wallRemove.addEventListener("click", () => removeBranding("wallpaper"));
}

function wireEvents() {
  dom.adminBtn.addEventListener("click", openAdmin);
  dom.emptyAdminBtn.addEventListener("click", openAdmin);
  dom.backBtn.addEventListener("click", () => closeAdmin(false));
  dom.saveBtn.addEventListener("click", saveSettings);
  dom.rescanBtn.addEventListener("click", rescanGpu);

  dom.logoutBtn.addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST", body: {} });
    } catch (err) {
      /* 忽略网络错误 */
    }
    try {
      const state = await api("/api/state");
      enterApp(state);
      snack("已退出登录");
    } catch (err) {
      showAuth(false);
    }
  });

  dom.themeBtn.addEventListener("click", async () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    redrawAll();
    if (!S.settings || !S.authed) return; // 访客只做本地切换，不改服务端设置
    S.settings.theme = next;
    setThemeSelection(next);
    try {
      const data = await api("/api/config", {
        method: "POST",
        body: { settings: { ...S.settings, gpu_enabled: S.settings.gpu_enabled } },
      });
      S.settings = data.settings;
      updateChrome();
    } catch (err) {
      if (err.status === 401) handleUnauthorized();
    }
  });

  dom.bootRetry.addEventListener("click", () => window.location.reload());

  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawAll, 120);
  });

  window.addEventListener("beforeunload", (event) => {
    if (S.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  wireAdminEvents();
}

/* ------------------------------------------------------------------ 启动 */
async function init() {
  applyTheme("auto");
  wireEvents();
  try {
    const state = await api("/api/state");
    enterApp(state);
  } catch (err) {
    dom.bootMsg.textContent = err.offline
      ? "无法连接服务器，请确认 SysMon 已启动"
      : err.message || "加载失败";
    dom.bootRetry.hidden = false;
  }
}

init();
