const marketplace = "aix_buyer_region_filtered_marketplace.svg";
const productScreen = "aix_product_details_reviews_unified.svg";
const rfqListScreen = "prototype_screens/prototype_quote_01_phone_01_quote_requests.svg";
const rfqDetailScreen = "prototype_screens/prototype_quote_02_phone_02_review_quote.svg";
const threadListScreen = "aix_supplier_messages_unified.svg";
const threadScreen = "aix_supplier_buyer_chat_unified.svg";
const rfqStates = { SUBMITTED: "待报价", QUOTED: "已报价", ACCEPTED: "已生成订单", REJECTED: "已拒绝" };
const quoteStates = { SENT: "待确认", ACCEPTED: "已确认", DECLINED: "已关闭", EXPIRED: "已过期" };
const units = {
  GPU_HOUR: "GPU 小时", INSTANCE: "实例", RESERVATION: "预留周期", SERVICE: "服务",
  INPUT_TOKEN: "输入 Token", OUTPUT_TOKEN: "输出 Token", TOKEN: "Token", REQUEST: "次调用",
  IMAGE: "张图片", SECOND: "秒",
};
const discrete = new Set(["TOKEN", "INPUT_TOKEN", "OUTPUT_TOKEN", "IMAGE", "REQUEST", "INSTANCE"]);
const displayTime = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });
const pageSize = 25;
const itemId = encodeURIComponent;
const time = (value) => displayTime.format(new Date(value));
const money = (value, currency) => `${String(value).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "")} ${currency}`;
const post = (ctx, path, body) => ctx.api(path, { method: "POST", body });

function localTime(hoursAhead) {
  const date = new Date(Date.now() + hoursAhead * 3_600_000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function isoTime(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) throw new Error("请选择有效的日期和时间。");
  return date.toISOString();
}

async function optionalProduct(ctx, id) {
  try {
    return await ctx.api(`/products/${itemId(id)}`);
  } catch (error) {
    if (error.status !== 404) throw error;
    return null;
  }
}

function pager(offset, count) {
  if (!offset && count < pageSize) return "";
  return `<form class="row" aria-label="分页" data-pager>
    <button type="submit" class="secondary" name="offset" value="${offset - pageSize}" ${offset ? "" : "disabled"}>上一页</button>
    <span>第 ${1 + offset / pageSize} 页</span>
    <button type="submit" class="secondary" name="offset" value="${offset + pageSize}" ${count < pageSize ? "disabled" : ""}>下一页</button>
  </form>`;
}

function bindPager(ctx, panel, handler) {
  const form = panel.querySelector("[data-pager]");
  if (!form) return;
  // The submitting button is excluded by FormData(form).
  form.addEventListener("submit", (event) => {
    form.dataset.offset = event.submitter?.value ?? "";
  });
  ctx.bind(form, (_, current) => current.dataset.offset && handler(Number(current.dataset.offset)));
}

function marketArtwork(svg) {
  const group = svg.querySelector('[id="Product_China_Telecom"]');
  const art = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  art.setAttribute("viewBox", "40 306 100 96");
  art.setAttribute("width", "72");
  art.setAttribute("height", "70");
  art.setAttribute("aria-hidden", "true");
  if (group) {
    art.append(...Array.from(group.children).slice(1, 4).map((node) => node.cloneNode(true)));
    group.remove();
  }
  return art;
}

function sourceHitArea(board, control, label, x, y, width, height) {
  control.setAttribute("aria-label", label);
  control.style.position = "absolute";
  control.style.left = `${x / 390 * 100}%`;
  control.style.top = `${y / 844 * 100}%`;
  control.style.width = `${width / 390 * 100}%`;
  control.style.height = `${height / 844 * 100}%`;
  control.style.borderRadius = "8px";
  board.append(control);
}

function sourceBack(board, destination, label) {
  const anchor = document.createElement("a");
  anchor.href = destination;
  sourceHitArea(board, anchor, label, 10, 47, 34, 39);
}

function boardLabel(svg, original, label) {
  const node = Array.from(svg.querySelectorAll("text")).find((text) => text.textContent === original);
  if (node) node.textContent = label;
}

async function renderMarket(ctx) {
  const escape = ctx.escape;
  const { board, panel, svg } = await ctx.mount(marketplace, {
    title: "采购市场", top: 101, bottom: 780,
    remove: ["Search_Field", "Region_Context", "Product_US_Compute_Hub"],
  });
  boardLabel(svg, "Compute Marketplace", "采购市场");
  const illustration = marketArtwork(svg);
  panel.insertAdjacentHTML("beforeend", `<form data-search class="stack">
    <label>搜索商品、型号或供应商<input type="search" name="q" maxlength="120"></label>
    <label>商品市场<select name="market"><option value="COMPUTE">GPU 算力</option><option value="TOKEN_API">模型 API</option></select></label>
    <details><summary>地区和规格</summary>
      <div class="stack">
        <label>服务地区<input name="service_region" maxlength="80" placeholder="如 US"></label>
        <label data-compute-filter>物理部署地区<input name="physical_region" maxlength="80" placeholder="如 us-east-1"></label>
        <label data-compute-filter>GPU 型号<input name="gpu_model" maxlength="120"></label>
      </div>
    </details>
    <button class="primary" type="submit">搜索</button>
  </form><section data-products class="stack" aria-label="搜索结果"></section>`);
  const search = panel.querySelector("[data-search]");
  const results = panel.querySelector("[data-products]");
  const filters = search.querySelector("details");
  filters.id = "market-filters";
  const expand = document.createElement("button");
  expand.type = "button";
  expand.style.background = "transparent";
  expand.style.border = "none";
  expand.setAttribute("aria-expanded", "false");
  expand.setAttribute("aria-controls", filters.id);
  filters.addEventListener("toggle", () => expand.setAttribute("aria-expanded", String(filters.open)));
  expand.addEventListener("click", () => {
    filters.open = !filters.open;
    if (filters.open) filters.querySelector("input").focus();
  });
  sourceHitArea(board, expand, "地区和规格筛选", 327, 52, 50, 44);
  search.elements.market.addEventListener("change", () => {
    const compute = search.elements.market.value === "COMPUTE";
    for (const field of search.querySelectorAll("[data-compute-filter]")) {
      field.hidden = !compute;
      field.querySelector("input").disabled = !compute;
    }
  });
  let currentFilters = { market: "COMPUTE" };
  let loadSequence = 0;

  async function load(filters, offset = 0) {
    const currentLoad = ++loadSequence;
    const params = new URLSearchParams({ ...filters, limit: pageSize, offset });
    const items = await ctx.api(`/products?${params}`);
    if (currentLoad !== loadSequence) return;
    currentFilters = filters;
    results.innerHTML = `<h2>商品结果</h2><p role="status">${items.length ? `本页 ${items.length} 件商品` : "暂无匹配商品。"} </p>
      ${items.map((product) => `<article class="record stack">
        <div class="row" data-product-item>
          <div><h3>${escape(product.name)}</h3><p class="muted">${escape(product.supplier_name)}</p></div>
        </div>
        <p>${escape(product.specification.gpu_model ?? product.specification.model)} · ${escape(product.service_region)}</p>
        <p><strong>${escape(money(product.unit_price, product.currency))}</strong> / ${escape(units[product.unit] ?? product.unit)}</p>
        <a class="primary" href="#product/${itemId(product.id)}" aria-label="查看详情：${escape(product.name)}">查看详情</a>
      </article>`).join("")}${pager(offset, items.length)}`;
    for (const card of results.querySelectorAll("[data-product-item]")) card.prepend(illustration.cloneNode(true));
    bindPager(ctx, results, (next) => load(currentFilters, next));
  }

  ctx.bind(search, (fields) => {
    const filters = Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value.trim()]).filter(([, value]) => value));
    return load(filters);
  });
  await load(currentFilters);
}

function productSpec(product) {
  const spec = product.specification;
  return product.market === "COMPUTE" ? [
    ["GPU 型号", spec.gpu_model], ["GPU 规格", `${spec.gpu_count} 卡 · ${spec.vram_gb} GB / 卡`],
    ["物理部署地区", spec.physical_region],
  ] : [["模型", spec.model], ["模型版本", spec.model_version]];
}

async function renderProduct(ctx, id) {
  const escape = ctx.escape;
  const { board, panel, svg } = await ctx.mount(productScreen, {
    title: "商品详情", top: 258, bottom: 780,
    remove: ["Product Summary", "Service Facts", "Tabs", "Reviews Summary", "Buyer Review", "Sticky Actions"],
  });
  boardLabel(svg, "Product details", "商品详情");
  // The source header's heart is a prototype decoration without a wishlist endpoint.
  svg.querySelector('[id="Header"] path:last-child')?.remove();
  sourceBack(board, "#market", "返回采购市场");
  const product = await ctx.api(`/products/${itemId(id)}`);
  const profile = await ctx.api(`/suppliers/${itemId(product.supplier_id)}/public-profile`);
  const spec = [...productSpec(product), ["服务地区", product.service_region]];
  const scheduled = product.market === "COMPUTE" && product.unit === "GPU_HOUR";
  const step = discrete.has(product.unit) ? "1" : "0.001";
  const canInquire = ctx.role === "buyer" && Boolean(ctx.organizationId);
  panel.insertAdjacentHTML("beforeend", `<section class="stack">
    <h2>${escape(product.name)}</h2>
    <p>${escape(profile.name)}${profile.verified ? " · 授权已核验" : ""}</p>
    <p><strong>${escape(money(product.unit_price, product.currency))}</strong> / ${escape(units[product.unit] ?? product.unit)}</p>
    <dl>${spec.map(([label, value]) => `<div><dt>${label}</dt><dd>${escape(value ?? "—")}</dd></div>`).join("")}</dl>
    ${canInquire ? `<details><summary>发起询价</summary><form class="stack" data-rfq>
      ${scheduled ? `
        <label>GPU 数量<input type="number" name="gpu_count" min="1" max="10000" value="${escape(product.specification.gpu_count)}" required></label>
        <label>租用时长（小时）<input type="number" name="duration_hours" min="1" max="2160" value="24" required></label>
        <label>开始时间（本地）<input type="datetime-local" name="starts_at" value="${localTime(48)}" required></label>
      ` : `<label>采购数量（${escape(units[product.unit] ?? product.unit)}）<input type="number" name="quantity" min="${step}" step="${step}" required></label>`}
      <label>采购要求<textarea name="requirements" maxlength="3000" ${scheduled ? "" : "required"}></textarea></label>
      <button type="submit" class="primary">提交询价</button>
    </form></details>` : '<p class="muted">使用采购企业身份发起询价。</p>'}
  </section>`);

  const form = panel.querySelector("[data-rfq]");
  if (form) ctx.bind(form, async (fields) => {
    const body = {
      organization_id: ctx.organizationId, product_id: product.id, requirements: fields.requirements.trim(),
    };
    if (scheduled) body.compute_schedule = {
      gpu_count: Number(fields.gpu_count), starts_at: isoTime(fields.starts_at), duration_hours: Number(fields.duration_hours),
    };
    else body.quantity = fields.quantity;
    const rfq = await post(ctx, "/rfqs", body);
    ctx.notify("询价已提交，等待供应商报价。");
    ctx.route(`rfqs/${itemId(rfq.id)}`);
  });
}

async function renderRfqList(ctx, messages) {
  const escape = ctx.escape;
  const route = messages ? "messages" : "rfqs";
  const { panel, svg } = await ctx.mount(messages ? threadListScreen : rfqListScreen, {
    title: messages ? "询价沟通" : "询价列表", top: messages ? 122 : 105, bottom: 780,
    remove: messages ? [
      "Message Filters", "Conversation Nova AI", "Conversation Pixel Lab",
      "Conversation Sora Compute", "Conversation Global Media", "Conversation Beijing Robotics",
    ] : ["Quote Card Pending", "Quote Card Approved", "Quote Card Declined"],
  });
  boardLabel(svg, messages ? "Messages" : "Quote Requests", messages ? "询价沟通" : "询价列表");
  panel.insertAdjacentHTML("beforeend", `
    ${messages ? "" : `<form data-state-filter class="stack">
      <label>询价状态<select name="state"><option value="">全部</option>${Object.entries(rfqStates).map(([value, label]) =>
        `<option value="${value}">${label}</option>`).join("")}</select></label>
      <button class="secondary" type="submit">筛选</button>
    </form>`}
    <section class="stack" data-inquiries aria-label="${messages ? "询价对话列表" : "询价记录"}"></section>`);
  const results = panel.querySelector("[data-inquiries]");
  let activeState = "";
  async function load(state, offset = 0) {
    const params = { limit: pageSize, offset, ...(state && { state }) };
    const items = await ctx.api(ctx.scoped("/rfqs", params));
    const products = await Promise.all([...new Set(items.map((rfq) => rfq.product_id))].map(async (id) => [id, await optionalProduct(ctx, id)]));
    const byId = new Map(products);
    activeState = state;
    results.innerHTML = `${items.map((rfq) => {
      const product = byId.get(rfq.product_id);
      return `<article class="record stack">
        <h2>${escape(product?.name ?? `询价 ${rfq.id.slice(0, 8)}`)}</h2>
        <p>${escape(rfqStates[rfq.state] ?? rfq.state)} · ${escape(time(rfq.created_at))}</p>
        ${messages ? "" : `<p class="muted">${escape(rfq.quantity)} ${escape(units[product?.unit] ?? (rfq.compute_schedule ? "GPU 小时" : ""))}</p>`}
        <a class="secondary" href="#${route}/${itemId(rfq.id)}">${messages ? "查看对话" : "查看询价"}</a>
      </article>`;
    }).join("") || `<p>${messages ? "暂无询价对话。" : "暂无符合条件的询价。"}</p>`}
    ${pager(offset, items.length)}`;
    bindPager(ctx, results, (next) => load(activeState, next));
  }
  const form = panel.querySelector("[data-state-filter]");
  if (form) ctx.bind(form, (fields) => load(fields.state));
  await load("");
}

function rfqSummary(ctx, rfq, product, quotes) {
  const escape = ctx.escape;
  const name = product?.name ?? quotes[0]?.snapshot.name ?? `询价 ${rfq.id.slice(0, 8)}`;
  const schedule = rfq.compute_schedule;
  return `<section class="record stack">
    <h2>${escape(name)}</h2><p>${escape(rfqStates[rfq.state] ?? rfq.state)}</p>
    <p>采购数量 ${escape(rfq.quantity)} ${escape(units[product?.unit ?? quotes[0]?.snapshot.unit] ?? (schedule ? "GPU 小时" : ""))}</p>
    ${schedule ? `<p>${escape(schedule.gpu_count)} 张 GPU · ${escape(schedule.duration_hours)} 小时<br>
      ${escape(time(schedule.starts_at))} 至 ${escape(time(schedule.ends_at))}</p>` : ""}
    ${rfq.requirements ? `<p>${escape(rfq.requirements)}</p>` : ""}
  </section>`;
}

async function renderRfq(ctx, id) {
  const escape = ctx.escape;
  const path = `/rfqs/${itemId(id)}`;
  const { panel, svg } = await ctx.mount(rfqDetailScreen, { title: "询价与报价", top: 105, bottom: 780 });
  boardLabel(svg, "Review Quote Request", "询价与报价");
  const rfq = await ctx.api(ctx.scoped(path));
  const [quotes, product] = await Promise.all([ctx.api(`${path}/quotes`), optionalProduct(ctx, rfq.product_id)]);
  const canQuote = ctx.role === "operator" || (ctx.role === "supplier" && ctx.organizationId === rfq.supplier_organization_id);
  const canAccept = ctx.role === "buyer" && ctx.organizationId === rfq.buyer_organization_id && rfq.state === "QUOTED";
  panel.insertAdjacentHTML("beforeend", `<div class="stack">
    <div class="row"><a href="#rfqs">返回询价</a><a href="#messages/${itemId(id)}">进入询价对话</a>
      <button type="button" class="secondary" data-refresh-quotes>刷新报价</button></div>
    ${rfqSummary(ctx, rfq, product, quotes)}
    <section class="stack" aria-label="供应商报价">
      <h2>供应商报价</h2>
      ${quotes.map((quote) => `<article class="record stack">
        <h3>${escape(money(quote.amount, quote.snapshot.currency))}</h3>
        <p>${escape(money(quote.snapshot.unit_price, quote.snapshot.currency))} / ${escape(units[quote.snapshot.unit] ?? quote.snapshot.unit)}</p>
        <p>${escape(quote.snapshot.terms)}</p>
        <p>${escape(quoteStates[quote.state] ?? quote.state)} · ${escape(time(quote.expires_at))} 到期</p>
        ${canAccept && quote.state === "SENT" ? `<form data-accept-quote="${escape(quote.id)}">
          <button type="submit" class="primary">确认报价并生成订单</button>
        </form>` : ""}
      </article>`).join("") || "<p>暂未收到供应商报价。</p>"}
      ${rfq.state === "ACCEPTED" ? '<a href="#orders" class="secondary">查看订单</a>' : ""}
    </section>
    ${canQuote && product && ["SUBMITTED", "QUOTED"].includes(rfq.state) ? `<section class="record stack"><h2>回复报价</h2>
      <form class="stack" data-quote>
        <label>单价（${escape(product.currency)}）<input name="unit_price" type="number" min="0.00000001" step="any" value="${escape(product.unit_price)}" required></label>
        <label>税费及其他费用<input name="tax_and_fees" type="number" min="0" step="0.01" value="0.00" required></label>
        <label>报价有效期（本地）<input name="expires_at" type="datetime-local" value="${localTime(24)}" required></label>
        <label>交付条款<textarea name="terms" maxlength="3000" required></textarea></label>
        <button type="submit" class="primary">发送报价</button>
      </form></section>` : ""}
    ${canQuote && rfq.state === "SUBMITTED" ? `<details><summary>无法承接</summary><form data-decline class="stack">
      <label>拒绝原因<textarea name="reason" maxlength="3000" required></textarea></label>
      <button type="submit" class="secondary">拒绝询价</button>
    </form></details>` : ""}
  </div>`);
  panel.querySelector("[data-refresh-quotes]").addEventListener("click", () => { void ctx.refresh(); });
  const quote = panel.querySelector("[data-quote]");
  if (quote) ctx.bind(quote, async (fields) => {
    const { unit_price, tax_and_fees, expires_at, terms } = fields;
    await post(ctx, `${path}/quotes`, { unit_price, tax_and_fees, expires_at: isoTime(expires_at), terms: terms.trim() });
    ctx.notify("报价已发送。");
    await ctx.refresh();
  });
  const decline = panel.querySelector("[data-decline]");
  if (decline) ctx.bind(decline, async (fields) => {
    await post(ctx, `${path}/decline`, { reason: fields.reason.trim() });
    ctx.notify("询价已拒绝。");
    await ctx.refresh();
  });
  for (const form of panel.querySelectorAll("[data-accept-quote]")) {
    ctx.bind(form, async () => {
      const order = await post(ctx, `/quotes/${itemId(form.dataset.acceptQuote)}/accept`);
      ctx.notify("报价已确认，等待运营归档合同。");
      ctx.route(`orders/${itemId(order.id)}`);
    });
  }
}

async function renderThread(ctx, id) {
  const escape = ctx.escape;
  const rfqPath = `/rfqs/${itemId(id)}`;
  const path = `${rfqPath}/messages`;
  const { board, panel, svg } = await ctx.mount(threadScreen, {
    title: "询价对话", top: 99, bottom: 780,
    remove: ["Order Context", "Messages", "Quick Actions", "Message Composer"],
  });
  sourceBack(board, "#messages", "返回沟通");
  const header = svg.querySelector('[id="Chat Header"]');
  if (header) {
    const [titleNode, statusNode, optionsNode] = header.querySelectorAll("text");
    if (titleNode) titleNode.textContent = "询价对话";
    for (const node of [statusNode, optionsNode, header.children[3], header.children[6]]) node?.remove();
  }
  let { messages, next_before_id: before, can_send: canSend } = await ctx.api(ctx.scoped(path));
  panel.insertAdjacentHTML("beforeend", `<div class="stack">
    <div class="row"><a href="#rfqs/${itemId(id)}">查看询价</a></div>
    <form data-older hidden><button type="submit" class="secondary">加载较早消息</button></form>
    <section data-transcript class="stack" aria-label="对话记录"></section>
    ${canSend ? `<form class="stack" data-message>
      <label>消息<textarea name="body" maxlength="2000" required></textarea></label>
      <button type="submit" class="primary">发送消息</button>
    </form>` : '<p class="muted">此账号可查看对话。</p>'}
    <form data-refresh-thread><button type="submit" class="secondary">刷新对话</button></form>
  </div>`);
  const transcript = panel.querySelector("[data-transcript]");
  const paginate = panel.querySelector("[data-older]");
  function show() {
    transcript.innerHTML = messages.map((message) => `<article class="record stack">
      <h2>${escape(message.sender_name)}</h2><time>${escape(time(message.created_at))}</time>
      <p>${escape(message.body).replace(/\r?\n/g, "<br>")}</p>
    </article>`).join("") || "<p>暂无消息。</p>";
    paginate.hidden = !before;
  }
  show();
  ctx.bind(paginate, async () => {
    const older = await ctx.api(ctx.scoped(path, { before_id: before }));
    messages = [...older.messages, ...messages];
    before = older.next_before_id;
    show();
  });
  const form = panel.querySelector("[data-message]");
  if (form) ctx.bind(form, async (fields, current) => {
    const message = await post(ctx, ctx.scoped(path), { body: fields.body.trim() });
    messages.push(message);
    current.reset();
    show();
    ctx.notify("询价消息已发送。");
  });
  ctx.bind(panel.querySelector("[data-refresh-thread]"), () => ctx.refresh());
}

export function renderTrade(ctx, page, id) {
  if (page === "market") return renderMarket(ctx);
  if (page === "product") return renderProduct(ctx, id);
  if (page === "messages" && id) return renderThread(ctx, id);
  if (page === "rfqs" && id) return renderRfq(ctx, id);
  return renderRfqList(ctx, page === "messages");
}
