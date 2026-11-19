const artwork = "prototype_screens/prototype_supplier_09_09_notifications.svg";
const categories = { ALL: "全部", ORDERS: "订单", QUOTES: "询报价", SYSTEM: "系统" };
const actionLabels = {
  "order.created": "订单已生成",
  "contract.archived": "合同已归档",
  "contract.confirmed": "交易方已确认合同",
  "payment.awaiting_verification": "等待付款核验",
  "payment.verified": "付款已核验",
  "fulfillment.started": "履约已开始",
  COMPUTE_PROVISIONED: "算力开通已登记",
  "order.delivered": "交付证据已提交",
  "delivery.accepted": "买家已验收",
  "order.completed": "订单已完成",
  "dispute.opened": "争议已发起",
  "dispute.resolved": "争议已处理",
  "refund.requested": "退款已申请",
  "refund.reviewed": "退款已审核",
  "refund.verified": "退款已核验",
  "rfq.submitted": "收到新询价",
  "rfq.declined": "询价已拒绝",
  "quote.sent": "报价已发出",
  "quote.accepted": "报价已确认",
  COMPUTE_RESERVATION_HELD: "算力容量已预留",
  COMPUTE_RESERVATION_CONFIRMED: "算力预留已确认",
  COMPUTE_RESERVATION_RELEASED: "算力容量已释放",
};
const slaLabels = { OUTAGE: "算力服务中断", RECOVERY: "算力服务已恢复", SLA_BREACH: "算力 SLA 未达标" };
const resolutionLabels = {
  FULFILLING: "恢复履约", DELIVERED: "待验收", ACCEPTED: "已验收", CANCELLED: "订单已取消",
};
const categoryMarks = { ORDERS: "▣", QUOTES: "✓", SYSTEM: "!" };
const relativeTime = new Intl.RelativeTimeFormat("zh-CN", { numeric: "always" });
const absoluteTime = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });

function displayTime(date) {
  const minutes = Math.floor((Date.now() - date.getTime()) / 60_000);
  if (minutes < 0) return absoluteTime.format(date);
  if (minutes === 0) return "刚刚";
  if (minutes < 60) return relativeTime.format(-minutes, "minute");
  if (minutes < 1440) return relativeTime.format(-Math.floor(minutes / 60), "hour");
  if (minutes < 10080) return relativeTime.format(-Math.floor(minutes / 1440), "day");
  return absoluteTime.format(date);
}

function eventRow(item, escape) {
  const { action, category, target_type, target_id, product_name, created_at, event_kind, to_state } = item;
  const title = action === "COMPUTE_SLA_EVENT"
    ? slaLabels[event_kind] ?? "算力服务事件"
    : actionLabels[action] ?? categories[category];
  const quote = target_type === "rfq";
  const reference = `${quote ? "询价" : "订单"} ${target_id.slice(0, 8).toUpperCase()}`;
  const resolution = ["dispute.resolved", "refund.verified"].includes(action) && resolutionLabels[to_state];
  const detail = [product_name, reference, resolution].filter(Boolean).join(" · ");
  const mark = action === "payment.verified" ? "$" : categoryMarks[category];
  const date = new Date(created_at);
  return `<li><a class="notice-event" href="#${quote ? "rfqs" : "orders"}/${encodeURIComponent(target_id)}">
    <span class="notice-icon" aria-hidden="true">${mark}</span>
    <div class="notice-message">
      <h2>${escape(title)}</h2>
      <time class="notice-time muted" datetime="${escape(created_at)}" title="${escape(absoluteTime.format(date))}">${escape(displayTime(date))}</time>
      <p class="muted">${escape(detail)}</p>
    </div>
  </a></li>`;
}

export async function renderNotifications(ctx) {
  const { board, panel, svg } = await ctx.mount(artwork, {
    title: "通知", top: 130, remove: ["Bottom Navigation", "Notice 01", "Notice 02", "Notice 03", "Notice 04"],
  });
  const heading = Array.from(svg.querySelectorAll("text")).find((text) => text.textContent === "Notifications");
  if (heading) heading.textContent = "通知";
  const back = document.createElement("a");
  back.href = "#supply";
  back.setAttribute("aria-label", "返回供给工作台");
  Object.assign(back.style, { position: "absolute", inset: "9% auto auto 3%", width: "11%", height: "6%" });
  board.append(back);

  panel.classList.add("supplier-notifications");
  panel.insertAdjacentHTML("beforeend", `
    <div class="notice-filters" role="group" aria-label="通知分类">
      ${Object.entries(categories).map(([value, label]) => `<button type="button" class="notice-filter"
        data-category="${value}" aria-pressed="${value === "ALL"}" aria-controls="notice-feed">${label}</button>`).join("")}
    </div>
    <div class="row between">
      <p data-notice-status class="muted" role="status"></p>
      <button type="button" class="secondary" data-refresh>刷新通知</button>
    </div>
    <p data-error class="form-error" role="alert" hidden></p>
    <ul id="notice-feed" class="notice-feed" aria-label="通知列表"></ul>
    <button type="button" class="secondary" data-more hidden>加载更多通知</button>
  `);
  const filters = Array.from(panel.querySelectorAll("[data-category]"));
  const status = panel.querySelector("[data-notice-status]");
  const refresh = panel.querySelector("[data-refresh]");
  const error = panel.querySelector("[data-error]");
  const list = panel.querySelector("#notice-feed");
  const more = panel.querySelector("[data-more]");
  let category = "ALL";
  let beforeId = null;
  let requestNumber = 0;

  async function load(append = false) {
    const request = ++requestNumber;
    const query = new URLSearchParams({ limit: 25 });
    if (category !== "ALL") query.set("category", category);
    if (append) query.set("before_id", beforeId);
    error.hidden = true;
    refresh.disabled = more.disabled = true;
    status.textContent = "正在加载通知…";
    list.setAttribute("aria-busy", "true");
    if (!append) {
      list.replaceChildren();
      beforeId = null;
      more.hidden = true;
    }
    try {
      const path = `/organizations/${encodeURIComponent(ctx.organizationId)}/supplier-notifications?${query}`;
      const { items, next_before_id: nextBeforeId } = await ctx.api(path);
      if (request !== requestNumber) return;
      list.insertAdjacentHTML("beforeend", items.map((item) => eventRow(item, ctx.escape)).join(""));
      beforeId = nextBeforeId;
      more.hidden = beforeId === null;
      status.textContent = list.children.length ? "" : "当前分类暂无通知。";
    } catch (failure) {
      if (request !== requestNumber) return;
      error.textContent = failure.message;
      error.hidden = false;
      status.textContent = "";
    } finally {
      if (request === requestNumber) {
        list.removeAttribute("aria-busy");
        refresh.disabled = more.disabled = false;
      }
    }
  }

  for (const filter of filters) filter.addEventListener("click", () => {
    if (category === filter.dataset.category) return;
    category = filter.dataset.category;
    for (const button of filters) button.setAttribute("aria-pressed", String(button === filter));
    void load();
  });
  refresh.addEventListener("click", () => void load());
  more.addEventListener("click", () => void load(true));
  await load();
}
