import { renderTrade } from "./trade.js";
import { renderOrders, renderOps, renderWallet } from "./orders.js";
import { renderSupplier } from "./supplier.js";
import { renderMonitor } from "./monitor.js";
import { renderNotifications } from "./notifications.js";

const content = document.querySelector("#content");
const identity = document.querySelector("#identity");
const account = document.querySelector("#account");
const notice = document.querySelector("#notice");
const profileArt = "prototype_screens/prototype_supplier_10_10_profile_and_settings.svg";
const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const svgNamespace = "http://www.w3.org/2000/svg";
const svgElements = new Set("svg g rect circle path text line tspan defs linearGradient stop".split(" "));
const svgAttributes = new Set("cx cy d fill font-family font-size font-weight height id letter-spacing offset opacity r rx stop-color stroke stroke-linecap stroke-linejoin stroke-opacity stroke-width text-anchor transform viewBox width x x1 x2 y y1 y2".split(" "));
const navLabels = {
  market: "市场", supply: "供给", rfqs: "询价", orders: "订单", messages: "沟通",
  wallet: "账单", team: "企业", ops: "待办", monitor: "监控", notifications: "通知",
};
const supplierTabs = ["supply", "market", "orders", "monitor", "messages", "notifications", "team"];
const navPages = {
  buyer: ["market", "rfqs", "orders", "messages", "wallet", "team"],
  supplier: [...supplierTabs, "rfqs", "wallet"],
  finance: ["orders", "wallet", "messages", "team"],
  operator: ["ops", "market", "supply", "rfqs", "orders", "messages", "wallet"],
  member: ["team", "market"],
};
const roleModes = { BUYER: "buyer", SUPPLIER: "supplier", FINANCE: "finance" };
const modeLabels = { buyer: "采购", supplier: "供应", finance: "财务", member: "成员" };
const receiptErrors = new Map([
  ["Receiving account credit must match the receipt channel, reference, amount and currency",
    "到账渠道、流水号、金额或币种与买方回单不一致，请核对后重试。"],
  ["Receiving account credit time must be between order creation and now",
    "入账时间须在订单生成之后，且不能晚于当前时间。"],
  ["Attach independent proof from the receiving account", "请上传收款方的独立到账凭证。"],
]);
const dateLabel = (value) => value ? new Date(value).toLocaleString("zh-CN") : "—";
let session = null;
let requested = "";
let currentRoot;

function notify(message = "") {
  notice.textContent = message;
  notice.hidden = !message;
}

function route(value) {
  const hash = `#${value}`;
  if (location.hash === hash) return render();
  history.pushState(null, "", hash);
  return render();
}

async function request(path, { method = "GET", body, token, headers = {}, raw = false, download = false } = {}) {
  const requestHeaders = new Headers(headers);
  if (token) requestHeaders.set("Authorization", `Bearer ${token}`);
  if (body !== undefined && !raw) requestHeaders.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, {
    method, headers: requestHeaders, body: raw ? body : JSON.stringify(body),
  });
  if (response.ok && download) return response.blob();
  if (response.status === 204) return null;
  const result = response.headers.get("content-type")?.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail = result?.detail;
    let message = Array.isArray(detail)
      ? detail.map((issue) => `${issue.loc.at(-1)}：${issue.msg}`).join("，")
      : detail || `请求失败（${response.status}）`;
    if (/^Attachment scan/.test(message) && response.status === 503) message = "安全扫描暂不可用，附件未归档。";
    if (message === "Attachment failed the security scan") message = "附件未通过安全扫描。";
    message = receiptErrors.get(message) ?? message;
    throw Object.assign(new Error(message), { status: response.status });
  }
  return result;
}

function bind(form, action) {
  const error = document.createElement("p");
  error.className = "form-error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  form.append(error);
  let busy = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const fields = Object.fromEntries(new FormData(form));
    const buttons = [...form.querySelectorAll('button:not([type]), button[type="submit"]')].filter((button) => !button.disabled);
    busy = true;
    buttons.forEach((button) => { button.disabled = true; });
    error.hidden = true;
    try {
      await action(fields, form);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      error.scrollIntoView({ block: "nearest" });
    } finally {
      busy = false;
      buttons.forEach((button) => { button.disabled = false; });
    }
  });
}

async function loadArt(name) {
  const path = name.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(`/source/design/${path}`);
  if (!response.ok) throw new Error("无法读取源画板。");
  const svg = new DOMParser().parseFromString(await response.text(), "image/svg+xml").documentElement;
  if (svg.namespaceURI !== svgNamespace || svg.localName !== "svg" || svg.getAttribute("viewBox") !== "0 0 390 844") {
    throw new Error("当前画板尺寸无效。");
  }
  for (const element of [svg, ...svg.querySelectorAll("*")]) {
    if (element.namespaceURI !== svgNamespace || !svgElements.has(element.localName)) {
      element.remove();
      continue;
    }
    for (const attribute of [...element.attributes]) {
      const value = attribute.value;
      if (attribute.namespaceURI || !svgAttributes.has(attribute.name) ||
          (/url\s*\(/i.test(value) && !/^url\(#[\w-]+\)$/.test(value))) {
        element.removeAttributeNode(attribute);
      }
    }
  }
  return document.importNode(svg, true);
}

async function mount(root, role, page, artwork, { title, top = 120, bottom = role ? 780 : 844, remove = [] } = {}) {
  const svg = await loadArt(artwork);
  for (const id of remove) svg.getElementById(id)?.remove();
  svg.classList.add("source-art");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const board = document.createElement("div");
  board.className = "board";
  board.dataset.artwork = artwork;
  board.style.setProperty("--stage-top", `${top / 844 * 100}%`);
  board.style.setProperty("--stage-bottom", `${(844 - bottom) / 844 * 100}%`);
  const panel = document.createElement("section");
  panel.className = "board-body";
  if (top === bottom) panel.style.padding = "0";
  if (title) panel.innerHTML = `<h1 class="sr-only">${escape(title)}</h1>`;
  board.append(svg, panel);
  if (role) {
    const nav = document.createElement("nav");
    nav.className = "board-nav";
    nav.setAttribute("aria-label", "主导航");
    const tabs = role === "supplier" ? supplierTabs : navPages[role];
    const current = page === "product" ? "market" : page;
    nav.innerHTML = tabs.map((tab) => {
      const selected = tab === current;
      const icon = ["monitor", "notifications"].includes(tab)
        ? `<img class="nav-source-icon" src="/source/design/aix_${tab}_icon${selected ? "_active" : ""}.svg" alt="">`
        : '<span class="nav-dot" aria-hidden="true"></span>';
      return `<a href="#${tab}"${selected ? ' aria-current="page"' : ""}>${icon}${navLabels[tab]}</a>`;
    }).join("");
    board.append(nav);
  }
  root.replaceChildren(board);
  return { svg, panel, board };
}

function renameProfile(svg, title) {
  for (const label of svg.querySelectorAll("text")) {
    if (label.textContent === "Profile") label.textContent = title;
    if (label.textContent === "‹") label.remove();
  }
}

async function renderAuth(root, page) {
  const register = page === "register";
  const title = register ? "创建账号" : "登录 AIX";
  const { svg, panel } = await mount(root, null, page, profileArt, { top: 124 });
  renameProfile(svg, title);
  panel.innerHTML = `<div class="panel stack"><h1>${title}</h1>
    <form data-auth="${page}">
      <label>邮箱<input name="email" type="email" maxlength="254" autocomplete="username" required></label>
      <label>密码<input name="password" type="password" minlength="12" maxlength="128" autocomplete="${register ? "new-password" : "current-password"}" required></label>
      <button type="submit" class="primary">${register ? "注册并登录" : "登录"}</button>
    </form>
    <a href="#${register ? "login" : "register"}">${register ? "已有账号，去登录" : "创建账号"}</a></div>`;
  bind(panel.querySelector("form"), async ({email, password}) => {
    const body = {email: email.trim(), password};
    if (register) await request("/auth/register", { method: "POST", body });
    const result = await request("/auth/login", { method: "POST", body });
    if (currentRoot !== root) {
      await request("/auth/logout", { method: "POST", token: result.access_token });
      return;
    }
    session = { token: result.access_token };
    const destination = requested || "home";
    requested = "";
    notify();
    await route(destination);
  });
}

function logout(message) {
  session = null;
  requested = "";
  notify(message);
  history.replaceState(null, "", "#login");
  return render();
}

async function renderAccount(ctx, user) {
  const { svg, panel } = await ctx.mount(profileArt, { top: 124 });
  renameProfile(svg, "账户");
  panel.innerHTML = `<div class="panel stack"><h1>账户</h1><p>${escape(user.email)}</p>
    <form>
      <label>当前密码<input type="password" name="current_password" autocomplete="current-password" required></label>
      <label>新密码<input type="password" name="new_password" minlength="12" maxlength="128" autocomplete="new-password" required></label>
      <button type="submit" class="primary">修改密码</button>
    </form><div><button type="button" class="secondary" data-logout>退出登录</button></div></div>`;
  ctx.bind(panel.querySelector("form"), async (body) => {
    await ctx.api("/auth/password/change", { method: "POST", body });
    await logout("密码已修改，请重新登录。");
  });
  panel.querySelector("[data-logout]").addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try {
      await ctx.api("/auth/logout", { method: "POST" });
      await logout("已退出登录。");
    } catch (error) {
      if (session) await logout(error.status === 401 ? "已退出登录。" : "已退出当前窗口，服务端注销未确认。");
    }
  });
}

async function renderTeam(ctx, organization) {
  const { svg, panel } = await ctx.mount(profileArt, { top: 124 });
  renameProfile(svg, "企业");
  const company = organization ? `/organizations/${encodeURIComponent(organization.id)}` : null;
  const [members, invitations] = company ? await Promise.all([
    ctx.api(`${company}/members`), organization.role === "OWNER" ? ctx.api(`${company}/invitations`) : [],
  ]) : [[], []];
  panel.innerHTML = `<div class="stack"><h1>企业</h1>
    ${organization ? `<section class="panel"><h2>${escape(organization.name)}</h2>
      <p class="muted">注册地区 ${escape(organization.registration_country || "未填写")}</p>
      ${members.map((member) => `<p>${escape(member.email)} <span class="muted">${escape(member.role)}</span></p>`).join("")}</section>` : ""}
    ${organization?.role === "OWNER" ? `<section class="panel"><h2>邀请成员</h2>
      <form data-team="invite"><label>受邀邮箱<input name="email" type="email" required></label>
      <label>权限<select name="role"><option value="BUYER">采购</option><option value="SUPPLIER">供给</option><option value="FINANCE">财务</option></select></label>
      <button type="submit" class="primary">生成邀请</button></form><p data-invitation role="status" class="notice" hidden></p>
      ${invitations.map((item) => `<p class="muted">${escape(item.email)} · ${escape(dateLabel(item.expires_at))}</p>`).join("")}</section>` : ""}
    <section class="panel"><h2>创建企业</h2><form data-team="create">
      <label>企业名称<input name="name" maxlength="120" required></label>
      <label>注册国家或地区代码<input name="registration_country" placeholder="例如 CN" pattern="[A-Za-z]{2}" maxlength="2"></label>
      <button type="submit" class="primary">创建企业</button></form></section>
    <section class="panel"><h2>接受邀请</h2><form data-team="accept">
      <label>邀请码<input name="invitation_code" required></label><button type="submit" class="secondary">接受邀请</button>
    </form></section></div>`;
  for (const form of panel.querySelectorAll("[data-team]")) ctx.bind(form, async (fields) => {
    const action = form.dataset.team;
    if (action === "invite") {
      const result = await ctx.api(`${company}/invitations`, { method: "POST", body: fields });
      const output = panel.querySelector("[data-invitation]");
      output.textContent = `邀请码 ${result.invitation_code}`;
      output.hidden = false;
      return;
    }
    const body = action === "create" ? { name: fields.name.trim(), registration_country: fields.registration_country.trim().toUpperCase() || null } : fields;
    const result = await ctx.api(action === "create" ? "/organizations" : "/organization-invitations/accept", { method: "POST", body });
    if (action === "create") session.organizationId = result.id;
    ctx.notify(action === "create" ? "企业已创建。" : "已加入企业。");
    await ctx.refresh();
  });
}

async function render() {
  const root = document.createElement("div");
  root.className = "view";
  currentRoot = root;
  content.replaceChildren(root);
  root.innerHTML = '<p class="loading" role="status">正在加载…</p>';
  const active = session;
  const fragment = location.hash.slice(1);
  let [page, id] = fragment.split("/");
  const current = () => currentRoot === root && session === active;
  try {
    if (!active) {
      identity.hidden = account.hidden = true;
      identity.replaceChildren();
      if (!["login", "register"].includes(page)) {
        requested = fragment;
        page = "login";
        history.replaceState(null, "", "#login");
      }
      await renderAuth(root, page);
      return;
    }
    const api = async (path, options) => {
      if (!current()) throw new Error("页面已切换。");
      try {
        return await request(path, { ...options, token: active.token });
      } catch (error) {
        if (error.status === 401 && current()) logout("请重新登录后继续。");
        throw error;
      }
    };
    const [user, companies] = await Promise.all([active.user || api("/users/me"), api("/organizations")]);
    if (!current()) return;
    active.user = user;
    const organization = user.is_operator ? null : companies.find(({id}) => id === active.organizationId) || companies[0];
    const role = user.is_operator ? "operator" : organization?.role === "OWNER"
      ? active.organizationId === organization.id && active.mode === "supplier" ? "supplier" : "buyer"
      : roleModes[organization?.role] || "member";
    active.organizationId = organization?.id;
    active.mode = role;
    const allowed = navPages[role];
    if (!allowed.includes(page) && page !== "account" && !(page === "product" && allowed.includes("market"))) {
      page = allowed[0];
      id = undefined;
      history.replaceState(null, "", `#${page}`);
    }
    account.hidden = false;
    identity.hidden = !organization && role !== "operator";
    identity.innerHTML = organization ? `<label>当前企业<select data-organization>
      ${companies.map((company) => `<option value="${escape(company.id)}"${company.id === organization.id ? " selected" : ""}>${escape(company.name)}</option>`).join("")}
    </select></label><label>工作模式<select data-mode${organization.role === "OWNER" ? "" : " disabled"}>
      ${(organization.role === "OWNER" ? ["buyer", "supplier"] : [role]).map((mode) =>
        `<option value="${mode}"${role === mode ? " selected" : ""}>${modeLabels[mode]}</option>`).join("")}</select></label>`
      : role === "operator" ? '<span class="muted">运营</span>' : "";
    identity.querySelector("[data-organization]")?.addEventListener("change", (event) => {
      active.organizationId = event.target.value;
      active.mode = null;
      notify();
      route("home");
    });
    identity.querySelector("[data-mode]")?.addEventListener("change", (event) => {
      active.mode = event.target.value;
      notify();
      if (id && ["orders", "rfqs", "messages"].includes(page)) void render();
      else void route("home");
    });
    const ctx = {
      role, actorRole: user.is_operator ? "OPERATOR" : organization?.role, organizationId: organization?.id,
      organizationName: organization?.name,
      api, escape, bind, route: (next) => current() && route(next), refresh: () => current() && render(),
      notify: (message) => current() && notify(message),
      mount: (art, options) => mount(root, role, page, art, options),
      scoped(path, parameters = {}) {
        const [base, query] = path.split("?");
        const params = new URLSearchParams(query);
        for (const [key, value] of Object.entries(parameters)) if (value != null) params.set(key, value);
        if (organization) params.set("organization_id", organization.id);
        return params.size ? `${base}?${params}` : base;
      },
      upload(path, file, kind) {
        if (!(file instanceof File) || !file.size || file.size > 5 * 1024 * 1024) {
          throw new Error("请选择不超过 5 MiB 的非空附件。");
        }
        const url = kind ? `${path}${path.includes("?") ? "&" : "?"}kind=${encodeURIComponent(kind)}` : path;
        return api(url, { method: "POST", body: file, raw: true, headers: {
          "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name),
        } });
      },
      async download(path, filename) {
        const blob = await api(path, { download: true });
        if (!current()) return;
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      },
    };
    if (page === "supply") await renderSupplier(ctx, id);
    else if (page === "orders") await renderOrders(ctx, id);
    else if (page === "monitor") await renderMonitor(ctx, id);
    else if (page === "notifications") await renderNotifications(ctx);
    else if (page === "wallet") await renderWallet(ctx);
    else if (page === "ops") await renderOps(ctx);
    else if (page === "team") await renderTeam(ctx, organization);
    else if (page === "account") await renderAccount(ctx, user);
    else await renderTrade(ctx, page, id);
  } catch (error) {
    if (!current()) return;
    root.innerHTML = `<section class="panel"><h1>无法加载此页面</h1><p role="alert">${escape(error.message)}</p>
      <button class="secondary" type="button">重试</button></section>`;
    root.querySelector("button").addEventListener("click", render);
  }
}

window.addEventListener("hashchange", () => {
  notify();
  render();
});
render();
