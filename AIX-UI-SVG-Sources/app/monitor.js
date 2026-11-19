import { amount, date, orderStates, reservationLabels, short, status, timelineLabels } from "./orders.js";

const artwork = "prototype_screens/prototype_supplier_05_05_order_monitor.svg";
const pageSize = 25;
const slaLabels = { OUTAGE: "故障", RECOVERY: "已恢复", SLA_BREACH: "SLA 违约" };
const stateColors = { FULFILLING: "#FF641A", DELIVERED: "#FF641A", ACCEPTED: "#2FB25B",
  COMPLETED: "#2FB25B", CANCELLED: "#888884", DISPUTED: "#A3361D" };
function shorten(text, limit) {
  const characters = Array.from(text ?? "");
  return characters.length > limit ? `${characters.slice(0, limit).join("")}…` : characters.join("");
}

async function monitorBoard(ctx, order) {
  const { svg, panel, board } = await ctx.mount(artwork, {
    title: "订单监控", top: order ? 278 : 127, remove: ["Bottom Navigation"],
  });
  const source = svg.getElementById("05 Order Monitor Content");
  const write = (y, value) => {
    const label = source.querySelector(`text[y="${y}"]`);
    label.textContent = value;
    return label;
  };
  source.querySelector('text[y="84"]').remove();
  write(132, "订单监控");
  for (const element of [...source.children]) {
    if (Number(element.getAttribute("y")) >= (order ? 304 : 156) || (!order && element.localName === "circle")) element.remove();
  }
  if (order) {
    write(184, `订单 ${short(order.id)}`);
    const name = write(216, shorten(order.product_name, 19));
    if (name.getComputedTextLength() > 206) {
      name.setAttribute("textLength", "206");
      name.setAttribute("lengthAdjust", "spacingAndGlyphs");
    }
    write(244, `${shorten(order.buyer_name, 15)} · ${status(order.state)}`);
    source.querySelector('circle[cy="215"]').setAttribute("fill", stateColors[order.state] ?? "#FF641A");
  }
  const back = document.createElement("a");
  back.href = order ? "#monitor" : "#orders";
  back.setAttribute("aria-label", order ? "返回订单监控列表" : "返回订单");
  Object.assign(back.style, {
    position: "absolute", left: "2.56%", top: "8.8%", width: "12.31%", height: "5.69%", borderRadius: "8px",
  });
  board.append(back);
  return panel;
}

async function renderList(ctx, path) {
  const panel = await monitorBoard(ctx);
  const { escape } = ctx;
  panel.insertAdjacentHTML("beforeend", `<div class="stack">
    <label>订单状态<select name="state"><option value="">全部状态</option>${
      orderStates.map((state) => `<option value="${state}">${escape(status(state))}</option>`).join("")
    }</select></label>
    <div class="actions"><button type="button" class="secondary" data-refresh>刷新状态</button></div>
    <p role="status" data-status class="muted"></p>
    <p role="alert" class="notice" data-error hidden></p>
    <section class="stack" aria-label="供应订单" data-list></section>
    <div class="actions" data-pager hidden>
      <button class="secondary" type="button" data-prev>上一页</button>
      <button class="secondary" type="button" data-next>下一页</button>
    </div>
  </div>`);
  const state = panel.querySelector("select");
  const refresh = panel.querySelector("[data-refresh]");
  const summary = panel.querySelector("[data-status]");
  const error = panel.querySelector("[data-error]");
  const list = panel.querySelector("[data-list]");
  const pager = panel.querySelector("[data-pager]");
  const previous = panel.querySelector("[data-prev]");
  const next = panel.querySelector("[data-next]");
  let offset = 0;
  let sequence = 0;

  async function load(newOffset = offset) {
    const current = ++sequence;
    const params = new URLSearchParams({ limit: pageSize, offset: newOffset });
    if (state.value) params.set("state", state.value);
    error.hidden = true;
    summary.textContent = "正在读取订单…";
    list.setAttribute("aria-busy", "true");
    refresh.disabled = previous.disabled = next.disabled = true;
    try {
      const orders = await ctx.api(`${path}?${params}`);
      if (current !== sequence) return;
      offset = newOffset;
      list.innerHTML = orders.map((order) => `<article class="record stack">
        <h2>${escape(order.product_name)}</h2>
        <p><strong>${escape(status(order.state))}</strong> · ${escape(order.buyer_name)}</p>
        <p class="muted">订单 ${escape(short(order.id))} · ${escape(date(order.created_at))}</p>
        <a class="secondary" href="#monitor/${encodeURIComponent(order.id)}">查看监控</a>
      </article>`).join("");
      summary.textContent = orders.length ? `第 ${offset / pageSize + 1} 页 · 本页 ${orders.length} 笔订单` : "本页暂无符合条件的供应订单。";
      pager.hidden = !offset && orders.length < pageSize;
      previous.disabled = !offset;
      next.disabled = orders.length < pageSize;
    } catch (failure) {
      if (current !== sequence) return;
      list.replaceChildren();
      pager.hidden = true;
      summary.textContent = "";
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      if (current === sequence) {
        list.removeAttribute("aria-busy");
        refresh.disabled = false;
      }
    }
  }

  state.addEventListener("change", () => { void load(0); });
  refresh.addEventListener("click", () => { void load(); });
  previous.addEventListener("click", () => { void load(offset - pageSize); });
  next.addEventListener("click", () => { void load(offset + pageSize); });
  await load(0);
}

async function renderDetail(ctx, path, id) {
  const order = await ctx.api(`${path}/${encodeURIComponent(id)}`);
  const panel = await monitorBoard(ctx, order);
  const { escape } = ctx;
  const { events, provisioning, reservation, sla_events: sla, statement } = order;
  const field = (label, value) => `<dt>${label}</dt><dd>${escape(value)}</dd>`;
  panel.insertAdjacentHTML("beforeend", `<div class="stack">
    <section class="panel" aria-label="当前订单状态">
      <h2>${escape(order.product_name)}</h2>
      <p class="muted">采购方 ${escape(order.buyer_name)} · 订单 ${escape(short(order.id))}</p>
      <p role="status"><strong>${escape(status(order.state))}</strong></p>
      <div class="actions">
        <button type="button" class="secondary" data-refresh>刷新状态</button>
        <a class="secondary" href="#orders/${encodeURIComponent(order.id)}">查看订单详情</a>
      </div>
    </section>
    ${order.market === "COMPUTE" ? `<section class="panel">
      <h2>资源与 SLA</h2>
      ${reservation || provisioning ? `<dl>
        ${reservation ? field("算力预留", reservationLabels[reservation.status] ?? reservation.status) : ""}
        ${field("GPU 数量", provisioning?.gpu_count ?? reservation.gpu_count)}
        ${field("服务时间", `${date(provisioning?.starts_at ?? reservation.starts_at)} 至 ${date(provisioning?.ends_at ?? reservation.ends_at)}`)}
        ${reservation?.status === "HELD" ? field("预留有效期", date(reservation.expires_at)) : ""}
        ${provisioning ? field("实例", provisioning.instance_reference) : ""}
        ${provisioning?.opened_at ? field("实际开通时间", date(provisioning.opened_at)) : ""}
      </dl>` : '<p class="muted">暂无预留或开通记录。</p>'}
      <h3>SLA 事件</h3>
      ${sla.length ? `<ul>${sla.map((event) => `<li>${escape(slaLabels[event.kind] ?? event.kind)} · ${escape(date(event.occurred_at))}</li>`).join("")}</ul>`
        : '<p class="muted">暂无 SLA 事件。</p>'}
    </section>` : ""}
    <section class="panel"><h2>交易账单</h2>
      <dl>
        ${field("订单金额", amount(statement.amount, statement.currency))}
        ${field("付款状态", status(statement.payment_status))}
        ${field("已核验收款", amount(statement.verified_paid, statement.currency))}
        ${field("已核验退款", amount(statement.verified_refunded, statement.currency))}
      </dl>
    </section>
    <div class="actions"><a class="primary" href="#messages/${encodeURIComponent(order.rfq_id)}">联系买家</a></div>
    <section class="panel"><h2>最近订单动态</h2>
      ${events.length ? `<ol>${events.map((event) => `<li>
        ${escape(timelineLabels[event.action] ?? (event.action === "COMPUTE_SLA_EVENT" ? slaLabels[event.event_kind] : null) ?? "订单记录已更新")}
        ${event.to_state ? `<strong>${escape(status(event.to_state))}</strong>` : ""}
        <time class="muted" datetime="${escape(event.created_at)}">${escape(date(event.created_at))}</time>
      </li>`).join("")}</ol>` : '<p class="muted">暂无订单动态。</p>'}
    </section>
  </div>`);
  panel.querySelector("[data-refresh]").addEventListener("click", () => { void ctx.refresh(); });
}

export async function renderMonitor(ctx, id) {
  const path = `/organizations/${encodeURIComponent(ctx.organizationId)}/supplier-orders`;
  if (id) await renderDetail(ctx, path, id);
  else await renderList(ctx, path);
}
