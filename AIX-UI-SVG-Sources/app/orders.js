const statusLabels = {
  CONTRACT_PENDING: "待确认合同", PAYMENT_PENDING: "待核验付款", PAID: "付款已核验",
  FULFILLING: "履约中", DELIVERED: "待验收", ACCEPTED: "已验收", COMPLETED: "已完成",
  CANCELLED: "已取消", DISPUTED: "争议中", UNPAID: "未付款",
  REFUND_REQUESTED: "退款处理中", PARTIALLY_REFUNDED: "部分退款", REFUNDED: "已退款",
};
const reservationLabels = {
  HELD: "待付款预留", CONFIRMED: "已确认", EXPIRED: "预留已过期", RELEASED: "已释放", PROVISIONED: "已开通",
};
const waiting = {
  PENDING_CONTRACT: "待归档合同", PENDING_START: "待开始履约",
  PENDING_PROVISION: "待登记开通", PENDING_COMPLETION: "待完成订单",
  PENDING_RECEIPT: "待核验到账", EXPIRED_RESERVATION: "待更新预留", OPEN_DISPUTE: "待处理争议",
  PENDING_REFUND: "待处理退款", TOKEN_REVIEW: "待对账用量", FULFILLMENT_OVERDUE: "待记录开通",
};
const opsActions = {
  PENDING_CONTRACT: "归档合同", PENDING_START: "开始履约",
  PENDING_PROVISION: "登记开通", PENDING_COMPLETION: "完成订单",
};
const timelineLabels = {
  "order.created": "订单已生成", "contract.archived": "合同已归档", "contract.confirmed": "交易方已确认合同",
  "payment.awaiting_verification": "等待付款核验", "payment.verified": "运营已核验付款",
  "fulfillment.started": "履约开始", "order.delivered": "已提交交付证据", "delivery.accepted": "买家已验收",
  "order.completed": "运营已结单", "dispute.opened": "争议已发起", "dispute.resolved": "争议已处理",
  "refund.requested": "退款已申请", "refund.reviewed": "退款已审核", "refund.verified": "退款已核验",
  COMPUTE_RESERVATION_HELD: "已预留算力容量", COMPUTE_RESERVATION_CONFIRMED: "算力预留已确认",
  COMPUTE_RESERVATION_RELEASED: "算力预留已释放", COMPUTE_PROVISIONED: "算力开通已登记",
  TOKEN_CALL_RECONCILED: "用量已对账",
};
const orderStates = Object.keys(statusLabels).slice(0, 9);
const pageSize = 30;
const latestListRequest = new WeakMap();
const status = (key) => statusLabels[key] ?? key;
const date = (value) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const amount = (value, currency) => `${value} ${currency}`;
const short = (value) => value.slice(0, 8).toUpperCase();
export { orderStates, status, reservationLabels, timelineLabels, date, amount, short };
const labelRef = (kind) => ({
  CONTRACT: "合同", PAYMENT: "付款回单", BANK_CREDIT: "收款方到账凭证",
  DELIVERY: "交付凭证", REFUND: "退款凭证", DISPUTE: "争议凭证",
})[kind] ?? kind;
const art = {
  orders: "prototype_screens/prototype_supplier_04_04_my_orders.svg",
  ops: "prototype_screens/prototype_supplier_09_09_notifications.svg",
  wallet: "aix_wallet_v2_01_home.svg",
  contract: "aix_wallet_v2_03_contract_protection.svg",
  payment: "aix_wallet_v2_04_protected_payment.svg",
  fulfillment: "aix_wallet_v2_05_order_control.svg",
  acceptance: "aix_wallet_v2_06_acceptance_settlement.svg",
};

function texts(svg, group, values) {
  const el = svg.getElementById(group);
  if (!el) return;
  const entries = [...el.children].filter((child) => child.localName === "text");
  values.forEach((value, i) => {
    if (value !== undefined && entries[i]) entries[i].textContent = value;
  });
}

function listPager(panel, rows, query, load) {
  panel.querySelector("[data-prev]").disabled = !query.offset;
  panel.querySelector("[data-next]").disabled = rows.length < pageSize;
  for (const [selector, shift] of [["[data-prev]", -pageSize], ["[data-next]", pageSize]]) {
    panel.querySelector(selector).onclick = () => {
      query.offset += shift;
      void load();
    };
  }
}

const pager = '<div class="actions"><button type="button" class="secondary" data-prev>上一页</button><button type="button" class="secondary" data-next>下一页</button></div>';

async function records(ctx, panel, path, query, getItems, row, empty) {
  const results = panel.querySelector("[data-results]");
  const error = panel.querySelector("[data-error]");
  const current = {};
  latestListRequest.set(panel, current);
  results.setAttribute("aria-busy", "true");
  panel.querySelector("[data-prev]").disabled = true;
  panel.querySelector("[data-next]").disabled = true;
  try {
    error.hidden = true;
    const result = await ctx.api(ctx.scoped(path, { limit: pageSize, ...query }));
    if (latestListRequest.get(panel) !== current) return null;
    const items = getItems(result);
    results.innerHTML = items.length ? items.map(row).join("") : `<p class="muted">${empty}</p>`;
    listPager(panel, items, query, () => records(ctx, panel, path, query, getItems, row, empty));
    return result;
  } catch (e) {
    if (latestListRequest.get(panel) === current) {
      error.textContent = e.message;
      error.hidden = false;
    }
    return null;
  } finally {
    if (latestListRequest.get(panel) === current) results.removeAttribute("aria-busy");
  }
}

export async function renderOrders(ctx, id) {
  if (id) return renderOrder(ctx, id);
  const { svg, panel } = await ctx.mount(art.orders, {
    title: "订单", top: 123,
    remove: ["Bottom Navigation", "Order 01", "Order 02", "Order 03"],
  });
  texts(svg, "04 My Orders Content", ["", "", "订单"]);
  const query = { offset: 0 };
  panel.innerHTML = `<h1 class="sr-only">订单</h1><div class="stack"><label>订单状态
    <select name="state"><option value="">全部状态</option>${orderStates.map((state) => `<option value="${state}">${status(state)}</option>`).join("")}</select></label>
    ${ctx.role === "supplier" ? '<div><a class="secondary" href="#monitor">打开订单监控</a></div>' : ""}
    <p data-error role="alert" hidden></p><div class="stack" data-results></div>${pager}</div>`;
  const load = () => records(ctx, panel, "/orders", query, (items) => items, (order) =>
    `<article class="record"><h2>${ctx.escape(order.snapshot.name)}</h2><p>${ctx.escape(status(order.state))} · ${ctx.escape(amount(order.amount, order.currency))}</p>
      <p class="muted">${short(order.id)} · ${date(order.created_at)}</p><a href="#orders/${encodeURIComponent(order.id)}">查看订单</a></article>`, "当前企业暂无订单。");
  panel.querySelector("select").onchange = (event) => {
    query.offset = 0;
    query.state = event.target.value || undefined;
    void load();
  };
  await load();
}

export async function renderWallet(ctx) {
  const { svg, panel } = await ctx.mount(art.wallet, {
    title: "交易账单", top: 275, remove: [
      "Status_Bar", "Lifecycle_Status", "Quick_Actions", "Active_Order", "Bottom_Navigation",
    ],
  });
  texts(svg, "Header", ["交易账单"]);
  texts(svg, "Wallet_Summary", ["AIX 交易记录", "订单账单", "金额按原币种列示", "本页订单", "—", "待核验付款", "—"]);
  panel.innerHTML = `<h1 class="sr-only">交易账单</h1><div class="stack"><p data-error role="alert" hidden></p><div class="stack" data-results></div>${pager}</div>`;
  const { escape } = ctx;
  const updateSummary = (items) => {
    texts(svg, "Wallet_Summary", [undefined, undefined, undefined, undefined, items.length, undefined,
      items.filter((item) => item.order_state === "PAYMENT_PENDING").length]);
    return items;
  };
  await records(ctx, panel, "/billing/statements", { offset: 0 }, updateSummary, (item) =>
    `<article class="record"><h2>订单 ${short(item.order_id)}</h2>
      <p>${escape(status(item.order_state))} · ${escape(status(item.payment_status))}</p>
      <p>应付 ${escape(amount(item.amount, item.currency))}<br>已核验 ${escape(amount(item.verified_paid, item.currency))}<br>已退款 ${escape(amount(item.verified_refunded, item.currency))}</p>
      <a href="#orders/${encodeURIComponent(item.order_id)}">查看账单和订单</a></article>`, "当前企业暂无交易账单。");
}

export async function renderOps(ctx) {
  const { svg, panel } = await ctx.mount(art.ops, {
    title: "运营待办", top: 123,
    remove: ["Bottom Navigation", "Notice 01", "Notice 02", "Notice 03", "Notice 04"],
  });
  texts(svg, "09 Notifications Content", ["", "", "运营待办"]);
  panel.innerHTML = `<h1 class="sr-only">运营待办</h1><div class="stack"><label>待办类型<select name="kind"><option value="">全部待办</option>${
    Object.entries(waiting).map(([key, text]) => `<option value="${key}">${text}</option>`).join("")
  }</select></label><div class="row between"><p data-total class="muted" role="status"></p>
    <button type="button" class="secondary" data-refresh-ops>刷新待办</button></div>
    <p data-error role="alert" hidden></p><div class="stack" data-results></div>${pager}</div>`;
  const query = { offset: 0 };
  const load = async () => {
    const result = await records(ctx, panel, "/admin/orders/exception-list", query, (json) => json.items, (item) =>
      `<article class="record"><h2>${ctx.escape(item.product_name)}</h2><p>${ctx.escape(waiting[item.kind] ?? item.kind)} · ${ctx.escape(status(item.order_state))}</p>
        <p class="muted">订单 ${short(item.order_id)} · ${date(item.opened_at)}</p>
        <a href="#orders/${encodeURIComponent(item.order_id)}">${opsActions[item.kind] ?? "处理订单"}</a></article>`, "当前没有待办。");
    if (result) panel.querySelector("[data-total]").textContent = `共 ${result.total} 项`;
  };
  panel.querySelector("select").onchange = (event) => {
    query.offset = 0;
    query.kind = event.target.value || undefined;
    void load();
  };
  panel.querySelector("[data-refresh-ops]").onclick = () => { void load(); };
  await load();
}

const input = (name, title, attrs = "required") => `<label>${title}<input name="${name}" ${attrs}></label>`;
const textarea = (name, title, attrs = "required maxlength=\"3000\"") => `<label>${title}<textarea name="${name}" ${attrs}></textarea></label>`;
const fileField = (title, required = true) => input("file", title,
  `type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" ${required ? "required" : ""}`);
const currencyField = '<label>到账币种<select name="currency" required><option value="">请选择</option><option>USD</option><option>CNY</option></select></label>';
const timeAttrs = 'type="datetime-local" step="1" required';
const amountAttrs = 'type="text" inputmode="decimal" maxlength="24" required';
const pair = (key, value, escape) => `<dt>${key}</dt><dd>${escape(value ?? "—")}</dd>`;
const confirmation = (value) => value ? `已确认 · ${date(value)}` : "待确认";

function creditText(credit) {
  return `${amount(credit.amount, credit.currency)} · ${credit.channel} · ${credit.external_reference}`;
}

const usageLabels = { RECORDED: "已记录", PENDING: "记录中", REVIEW_REQUIRED: "待对账", NO_CHARGE: "未收费" };
const usageUnits = { INPUT_TOKEN: "输入 Token", OUTPUT_TOKEN: "输出 Token", TOKEN: "Token", REQUEST: "次调用" };

function usageRows(usage, escape) {
  if (!usage.length) return '<li class="muted">暂无模型用量记录。</li>';
  return usage.map((entry) => {
    const details = [date(entry.created_at), usageLabels[entry.status] ?? entry.status];
    const unit = usageUnits[entry.unit] ?? entry.unit;
    if (entry.input_tokens != null && entry.output_tokens != null) {
      details.push(`输入 ${entry.input_tokens} Token，输出 ${entry.output_tokens} Token`);
    }
    if (entry.billable_units != null) details.push(`计费 ${entry.billable_units} ${unit}`);
    if (entry.amount != null) details.push(`用量金额 ${amount(entry.amount, entry.currency)}`);
    return `<li>${escape(details.join(" · "))}</li>`;
  }).join("");
}

async function mountOrder(ctx, order) {
  const ref = `订单 ${short(order.id)}`;
  const money = amount(order.amount, order.currency);
  if (order.state === "CONTRACT_PENDING") {
    const stage = await ctx.mount(art.contract, { title: "合同与确认", top: 215,
      remove: ["Status_Bar", "Protection_Terms", "Release_Schedule", "Contract_Document", "Acknowledgement", "CTA_Sign_Continue"] });
    texts(stage.svg, "Header", ["合同与确认", "02 · 合同"]);
    texts(stage.svg, "Order_Summary", [ref, status(order.state), "", money]);
    return stage;
  }
  if (order.state === "PAYMENT_PENDING") {
    const stage = await ctx.mount(art.payment, { title: "付款核验", top: 284,
      remove: ["Status_Bar", "Partner_Notice", "Payment_Methods", "Coverage", "CTA_Authorize_Payment"] });
    texts(stage.svg, "Header", ["付款核验", "03 · 付款"]);
    texts(stage.svg, "Amount_Summary", ["订单应付金额", money, "订单编号", short(order.id), "付款状态", status(order.state)]);
    return stage;
  }
  if (["DELIVERED", "ACCEPTED", "COMPLETED", "CANCELLED", "DISPUTED"].includes(order.state)) {
    const stage = await ctx.mount(art.acceptance, { title: "验收与交易记录", top: 209,
      remove: ["Status_Bar", "Contract_Actual", "Evidence", "Settlement", "Decision_Options", "CTA_Accept_Settle"] });
    texts(stage.svg, "Header", ["验收与交易记录", "05 · 验收"]);
    texts(stage.svg, "Acceptance_Window", ["当前订单状态", status(order.state), short(order.id)]);
    return stage;
  }
  const stage = await ctx.mount(art.fulfillment, { title: "履约与交付", top: 310,
    remove: ["Status_Bar", "Milestones", "Delivery_Intelligence", "CTA_Review_Acceptance"] });
  texts(stage.svg, "Header", ["履约与交付", "04 · 履约"]);
  texts(stage.svg, "Running_Order", [ref, "", status(order.state), "", "订单金额", money, "", ""]);
  const card = stage.svg.getElementById("Running_Order");
  for (const element of card?.querySelectorAll('circle, rect[y="117"], rect[y="266"]') ?? []) element.remove();
  return stage;
}

async function maybe(ctx, path) {
  try {
    return await ctx.api(path);
  } catch (e) {
    if (e.status !== 404) throw e;
    return null;
  }
}

async function renderOrder(ctx, id) {
  const orderPath = `/orders/${encodeURIComponent(id)}`;
  const computePath = `/compute/orders/${encodeURIComponent(id)}`;
  const order = await ctx.api(ctx.scoped(orderPath));
  const { panel } = await mountOrder(ctx, order);
  const { escape } = ctx;
  const operator = ctx.role === "operator";
  const buyer = ctx.role === "buyer" && ctx.organizationId === order.buyer_organization_id;
  const supplier = ctx.role === "supplier" && ctx.organizationId === order.supplier_organization_id;
  const finance = ctx.role === "finance" && ctx.organizationId === order.buyer_organization_id;
  const compute = order.market === "COMPUTE";
  const tokenOrder = order.market === "TOKEN_API";
  const contract = order.contract;
  const usePath = `${operator ? "/admin/token/usage" : "/usage/records"}?order_id=${encodeURIComponent(id)}&limit=200`;
  const [attachments, timeline, reservation, provision, receipts, usage] = await Promise.all([
    ctx.api(ctx.scoped(`${orderPath}/attachments`, { limit: 100 })),
    ctx.api(`${orderPath}/timeline?limit=100`),
    compute && order.compute_reservation ? maybe(ctx, `${computePath}/reservation`) : null,
    compute ? maybe(ctx, `${computePath}/provisioning`) : null,
    operator || buyer || finance ? ctx.api(`${orderPath}/payment-receipts?limit=50`) : [],
    tokenOrder ? ctx.api(usePath) : [],
  ]);
  const activeReceipt = receipts.find((receipt) => receipt.state === "PENDING");
  const rejectedReceipt = receipts.find((receipt) => receipt.state === "REJECTED");
  const reservationExpired = reservation?.status === "EXPIRED";
  const contractFile = contract && attachments.find((attachment) => attachment.reference === contract.evidence_ref);
  const actionHandlers = [];
  const form = (fields, label, handler, variant = "primary") => {
    const index = actionHandlers.push(handler) - 1;
    return `<form class="stack" data-order-action="${index}">${fields}<button type="submit" class="${variant}">${label}</button></form>`;
  };
  const post = (path, body, options = {}) => ctx.api(path, { method: "POST", body, ...options });
  const change = (path, body, message) => async (fields, el) => {
    const value = typeof body === "function" ? await body(fields, el) : body;
    await post(path, value);
    ctx.notify(message);
    return ctx.refresh();
  };
  const staged = new WeakMap();
  const upload = async (fields, el, kind) => {
    if (!fields.file?.size) throw new Error("请选择非空附件。");
    const saved = staged.get(el);
    if (saved?.file === fields.file) return saved.attachment;
    const attachment = await ctx.upload(`${orderPath}/attachments`, fields.file, kind);
    staged.set(el, { file: fields.file, attachment });
    return attachment;
  };
  const downloadButtons = [];
  const download = (path, name, label) => {
    const index = downloadButtons.push(() => ctx.download(path, name)) - 1;
    return `<button type="button" class="secondary" data-order-download="${index}">${escape(label)}</button>`;
  };
  const downloadAttachment = (attachment, label) => download(ctx.scoped(`${orderPath}/attachments/${encodeURIComponent(attachment.id)}`), attachment.filename, label);

  let actions = "";
  if (order.state === "CONTRACT_PENDING") {
    if (!contract && operator) {
      actions = '<p class="muted">归档后由采购方与供应方分别确认合同。</p>';
      actions += form(input("version", "合同版本", "required maxlength=160") + textarea("payment_instructions", "付款指引") + fileField("合同附件"),
        "上传并归档合同", change(`${orderPath}/contract`, async (fields, el) => {
          const attachment = await upload(fields, el, "CONTRACT");
          return { version: fields.version, payment_instructions: fields.payment_instructions,
            evidence_ref: attachment.reference, sha256: attachment.sha256 };
        }, "合同附件已归档"));
    } else if (!contract) {
      actions = '<p class="muted">等待运营归档合同。归档后由采购方与供应方分别确认。</p>';
    } else {
      const pending = [!contract.buyer_confirmed_at && "采购方", !contract.supplier_confirmed_at && "供应方"].filter(Boolean);
      actions = `<dl>${pair("采购方确认", confirmation(contract.buyer_confirmed_at), escape)}
        ${pair("供应方确认", confirmation(contract.supplier_confirmed_at), escape)}</dl>
        <p class="muted">${pending.length ? `等待${pending.join("与")}确认合同。` : ""}双方确认后，采购方或财务可在此提交付款回单。</p>`;
      if ((buyer && !contract.buyer_confirmed_at) || (supplier && !contract.supplier_confirmed_at)) {
        actions += form("", "确认合同",
          change(`${orderPath}/contract/confirm`, { organization_id: ctx.organizationId }, "合同已确认"));
      } else if (ctx.actorRole === "OWNER") {
        if (ctx.organizationId === order.supplier_organization_id && !contract.supplier_confirmed_at) {
          actions += '<p class="muted">请在顶部将工作模式切换为「供应」，再确认合同。</p>';
        } else if (ctx.organizationId === order.buyer_organization_id && !contract.buyer_confirmed_at) {
          actions += '<p class="muted">请在顶部将工作模式切换为「采购」，再确认合同。</p>';
        }
      }
    }
  }
  if (reservationExpired && ["CONTRACT_PENDING", "PAYMENT_PENDING"].includes(order.state)) {
    actions += '<p class="notice">算力预留已过期。请由运营更新预留后再继续付款与核验。</p>';
    if (operator) actions += form("", "更新算力预留", change(`${computePath}/reservation`, undefined, "算力预留已更新"));
  }
  if (order.state === "PAYMENT_PENDING") {
    if (rejectedReceipt && !activeReceipt) {
      actions += `<p class="muted">上一笔回单已退回。原因：${escape(rejectedReceipt.review_note)}</p>`;
    }
    if (activeReceipt && operator) {
      const reviewPath = `${orderPath}/payment-receipts/${encodeURIComponent(activeReceipt.id)}/review`;
      const attachment = attachments.find((item) => item.id === activeReceipt.attachment_id);
      actions += `<p>待核验回单 ${escape(creditText(activeReceipt))}。</p>${attachment ? downloadAttachment(attachment, "下载买方回单") : ""}`;
      if (!reservationExpired) {
        actions += '<p class="muted">请以收款方到账记录为准，核对渠道、流水、金额和币种，并上传收款方凭证。</p>';
        actions += form(input("channel", "到账渠道", "required maxlength=160") + input("external_reference", "到账流水号", "required maxlength=160") +
          input("amount", "到账金额", amountAttrs) + currencyField + input("credited_at", "入账时间（本地）", timeAttrs) +
          fileField("收款方到账凭证") + textarea("note", "核验说明"), "确认到账",
          change(reviewPath, async (fields, el) => {
            const creditedAt = new Date(fields.credited_at).toISOString();
            const proof = await upload(fields, el, "BANK_CREDIT");
            return { decision: "APPROVE", note: fields.note, bank_credit: { channel: fields.channel,
              external_reference: fields.external_reference, amount: fields.amount.trim(), currency: fields.currency,
              credited_at: creditedAt, evidence_ref: proof.reference } };
          }, "付款已核验"));
      }
      actions += form(textarea("note", "退回原因"), "退回回单", change(reviewPath,
        (fields) => ({ decision: "REJECT", note: fields.note }), "回单已退回"), "secondary");
    } else if (activeReceipt) {
      actions += `<p class="muted">${reservationExpired ? "付款凭证已提交，等待运营更新预留后核验。" : "付款凭证已提交，等待运营核验。"}</p>`;
    } else if ((buyer || finance) && contract?.payment_instructions && (reservation?.status ?? "HELD") === "HELD") {
      actions += `<p>双方已确认合同。请按付款指引完成外部付款，再提交回单供运营核验。</p>
        <dl>${pair("应付金额", amount(order.amount, order.currency), escape)}
        ${pair("付款指引", contract.payment_instructions, escape)}</dl>`;
      actions += form(input("channel", "付款渠道", "required maxlength=160") + input("external_reference", "外部流水号", "required maxlength=160") + fileField("付款回单"),
        "提交付款凭证", change(`${orderPath}/payment-receipts`, async (fields, el) => ({
          channel: fields.channel, external_reference: fields.external_reference, amount: order.amount,
          currency: order.currency, evidence_ref: (await upload(fields, el, "PAYMENT")).reference,
        }), "付款凭证已提交，等待运营核验。"));
    } else if (!reservationExpired) {
      if (buyer || finance) {
        actions += '<p class="muted">付款指引暂未开放。请联系运营核对订单，再刷新查看。</p>';
      } else if (ctx.actorRole === "OWNER" && ctx.organizationId === order.buyer_organization_id) {
        actions += '<p class="muted">双方已确认合同。请在顶部将工作模式切换为「采购」，再提交付款回单。</p>';
      } else {
        actions += '<p class="muted">双方已确认合同，等待采购方或财务提交付款回单。</p>';
      }
    }
  }
  if (order.state === "PAID") {
    actions = operator ? form("", "开始履约", change(`${orderPath}/start-fulfillment`, undefined, "履约已开始"))
      : '<p class="muted">到账已核验，等待运营开始履约。</p>';
  }
  if (order.state === "FULFILLING") {
    if (compute && !provision && operator && (!reservation || reservation.status === "CONFIRMED")) {
      let schedule = "";
      if (!reservation) {
        const resources = await ctx.api(`/compute/resources?product_id=${encodeURIComponent(order.snapshot.product_id)}`);
        schedule = `<label>已验证资源<select name="resource_id" required><option value="">请选择</option>${resources
          .filter((item) => item.verified_at && Date.parse(item.rights_valid_until) > Date.now())
          .map((item) => `<option value="${escape(item.id)}">${escape(item.gpu_model)} · ${short(item.id)}</option>`).join("")}</select></label>` +
          input("starts_at", "服务开始时间（本地）", timeAttrs) + input("ends_at", "服务结束时间（本地）", timeAttrs);
      }
      actions = form(schedule + input("instance_reference", "实例引用", "required maxlength=200") + input("opened_at", "实际开通时间（本地）", timeAttrs) +
        input("delivery_owner", "交付负责人", "required maxlength=200") + fileField("开通凭证"),
        "登记算力开通", change(`${computePath}/provisioning`, async (fields, el) => {
          const openedAt = new Date(fields.opened_at).toISOString();
          const proof = await upload(fields, el, "DELIVERY");
          return { resource_id: reservation?.resource_id ?? fields.resource_id,
            starts_at: reservation?.starts_at ?? new Date(fields.starts_at).toISOString(),
            ends_at: reservation?.ends_at ?? new Date(fields.ends_at).toISOString(),
            opened_at: openedAt, instance_reference: fields.instance_reference,
            delivery_owner: fields.delivery_owner, opening_attachment_id: proof.id };
        }, "已登记算力开通"));
    }
    if (tokenOrder && buyer) {
      actions += form(textarea("prompt", "提示词", "required maxlength=8000") + '<p data-model-output aria-live="polite"></p>', "调用模型",
        async (fields, el) => {
          const result = await post(`/model-gateway/${encodeURIComponent(order.snapshot.specification.model)}/chat/completions`,
            { order_id: order.id, messages: [{ role: "user", content: fields.prompt }], max_output_tokens: 128 },
            { headers: { "Idempotency-Key": crypto.randomUUID() } });
          el.querySelector("[data-model-output]").textContent = result.output.content;
          if (!usage.some((entry) => entry.id === result.usage.id)) usage.push(result.usage);
          panel.querySelector("[data-model-usage]").innerHTML = usageRows(usage, escape);
          ctx.notify("模型已回复，用量已记录");
        });
    }
    if ((supplier || operator) && (compute ? provision : usage.some((event) => event.status === "RECORDED"))) {
      actions += form(textarea("summary", "交付说明") + fileField("交付附件（选填）", false) + input("evidence_ref", "外部交付凭证引用（无附件时必填）", "maxlength=500"),
        "提交交付凭证", change(`${orderPath}/delivery-evidences`, async (fields, el) => {
          const evidence = fields.file?.size ? (await upload(fields, el, "DELIVERY")).reference : fields.evidence_ref.trim();
          if (!evidence) throw new Error("请上传附件或填写外部交付凭证引用。");
          return { summary: fields.summary, evidence_ref: evidence };
        }, "交付凭证已提交"));
    }
    if (!actions && compute) {
      actions = provision ? '<p class="muted">算力已开通，等待供应方提交交付凭证。</p>'
        : '<p class="muted">等待运营登记算力开通。</p>';
    } else if (!actions && tokenOrder) {
      actions = '<p class="muted">等待采购方在本订单调用模型。记录用量后可提交交付凭证。</p>';
    }
  }
  if (order.state === "DELIVERED") {
    actions = buyer ? form(textarea("note", "验收说明（选填）", "maxlength=3000"), "确认验收", change(`${orderPath}/acceptance`,
      (fields) => fields.note.trim() ? { note: fields.note } : {}, "验收已确认"))
      : '<p class="muted">交付凭证已提交，等待采购方确认验收。</p>';
  }
  if (order.state === "ACCEPTED") {
    actions = operator ? form("", "完成订单", change(`${orderPath}/complete`, undefined, "订单已完成"))
      : '<p class="muted">采购方已验收，等待运营完成订单。</p>';
  }

  const openStates = ["PAID", "FULFILLING", "DELIVERED", "ACCEPTED"];
  const afterSale = [];
  if (buyer && openStates.includes(order.state)) {
    afterSale.push(form(textarea("reason", "争议原因"), "发起争议",
      change(`${orderPath}/disputes`, (fields) => ({ reason: fields.reason }), "争议已发起"), "secondary"));
  }
  if ((buyer || finance) && order.payment && order.state !== "CANCELLED" &&
      !order.refunds.some((refund) => ["REQUESTED", "APPROVED"].includes(refund.state))) {
    afterSale.push(form(input("amount", "退款金额", amountAttrs) + textarea("reason", "退款原因"), "申请退款",
      change(`${orderPath}/refund-requests`, (fields) => ({ amount: fields.amount.trim(), reason: fields.reason }), "退款已申请"), "secondary"));
  }
  for (const item of order.disputes) {
    const path = `${orderPath}/disputes/${encodeURIComponent(item.id)}`;
    afterSale.push(`<p>争议 · ${escape(item.reason)}${item.resolved_at ? ` · ${escape(item.decision)}` : " · 待处理"}</p>`);
    if (!item.resolved_at) {
      afterSale.push(form(textarea("summary", "补充说明") + fileField("争议凭证"), "补充争议凭证",
        change(`${path}/evidences`, async (fields, el) => ({ summary: fields.summary,
          evidence_ref: (await upload(fields, el, "DISPUTE")).reference }), "争议凭证已补充"), "secondary"));
      if (operator) afterSale.push(form(textarea("decision", "处理决定") + '<label><input type="checkbox" name="redeliver">需要重新交付</label>',
        "处理争议", change(`${path}/resolve`, (fields) => ({ decision: fields.decision, redeliver: fields.redeliver === "on" }), "争议已处理")));
    }
  }
  for (const item of order.refunds) {
    const path = `${orderPath}/refund-requests/${encodeURIComponent(item.id)}`;
    const states = { REQUESTED: "待审核", APPROVED: "待退款核验", REJECTED: "已驳回", PROCESSED: "已退款" };
    afterSale.push(`<p>退款 ${escape(amount(item.amount, order.currency))} · ${escape(states[item.state] ?? item.state)} · ${escape(item.reason)}</p>`);
    if (operator && item.state === "REQUESTED") {
      afterSale.push(form('<label>审核决定<select name="decision" required><option value="">请选择</option><option value="APPROVE">通过</option><option value="REJECT">驳回</option></select></label>' +
        textarea("note", "审核说明"), "审核退款", change(`${path}/review`, ({ decision, note }) => ({ decision, note }), "退款已审核")));
    } else if (operator && item.state === "APPROVED") {
      afterSale.push(form(input("external_reference", "外部退款流水号", "required maxlength=160") + fileField("退款凭证"),
        "核验退款", change(`${path}/confirm`, async (fields, el) => ({ external_reference: fields.external_reference,
          evidence_ref: (await upload(fields, el, "REFUND")).reference }), "退款已核验")));
    }
  }

  const reconcile = operator ? usage.filter((item) => item.status === "REVIEW_REQUIRED" ||
    (item.status === "PENDING" && Date.now() - Date.parse(item.created_at) > 120000)).map((item) => {
      const path = `/admin/token/usage/${encodeURIComponent(item.id)}/reconciliation`;
      const evidence = input("evidence_ref", "用量对账凭证引用", "required maxlength=500");
      const numberAttrs = 'type="number" min="0" step="1" required';
      const billable = input("provider_request_id", "服务商请求编号", "required maxlength=500") +
        input("provider_model", "服务商模型", "required maxlength=500") +
        input("prompt_tokens", "输入 Token", numberAttrs) + input("completion_tokens", "输出 Token", numberAttrs);
      return `<details><summary>对账 · ${short(item.id)}</summary><div class="stack">
        ${form(evidence + billable, "登记账单用量", change(path, (fields) => ({
          outcome: "RECORDED", evidence_ref: fields.evidence_ref, provider_request_id: fields.provider_request_id,
          provider_model: fields.provider_model, usage: {
            prompt_tokens: Number(fields.prompt_tokens), completion_tokens: Number(fields.completion_tokens),
          },
        }), "用量已登记"))}
        ${form(evidence, "登记未收费", change(path, (fields) => ({ outcome: "NO_CHARGE", evidence_ref: fields.evidence_ref }),
          "未收费核验已登记"), "secondary")}</div></details>`;
    }).join("") : "";

  const bill = order.statement;
  const details = [
    ["采购方", order.snapshot.buyer_organization_name],
    ["供应方", order.snapshot.supplier_organization_name], ["下单时间", date(order.created_at)],
  ];
  if (order.snapshot.terms) details.push(["报价条款", order.snapshot.terms]);
  if (reservation) {
    details.push(["预留状态", reservationLabels[reservation.status]],
      ["预订资源", `${reservation.gpu_count} 卡 · ${date(reservation.starts_at)} 至 ${date(reservation.ends_at)}`]);
  }
  if (provision) details.push(["开通实例", provision.instance_reference], ["开通时间", date(provision.opened_at)], ["交付负责人", provision.delivery_owner]);
  if (contract) {
    details.push(["合同版本", contract.version]);
    if (order.state !== "CONTRACT_PENDING") details.push(["采购方确认", confirmation(contract.buyer_confirmed_at)], ["供应方确认", confirmation(contract.supplier_confirmed_at)]);
    if (contract.payment_instructions && (operator || ((buyer || finance) && (activeReceipt || order.payment)))) {
      details.push(["付款指引", contract.payment_instructions]);
    }
  }
  if (order.payment) details.push(["付款核验时间", date(order.payment.verified_at)], ["收款账户入账时间", date(order.payment.bank_credited_at)]);
  const actionTitle = order.state === "PAYMENT_PENDING" ? "付款与回单" : "当前操作";
  panel.innerHTML = `<div class="stack">
    <nav class="actions" aria-label="订单导航"><a href="#orders">返回订单</a>${ctx.role === "finance" ? "" :
      `<a href="#rfqs/${encodeURIComponent(order.rfq_id)}">查看关联询价</a>`}
      <button type="button" class="secondary" data-order-refresh>刷新订单</button></nav>
    <section class="record"><h1>${escape(order.snapshot.name)}</h1><p>订单 ${short(order.id)} · ${escape(status(order.state))}</p>
      ${contractFile ? downloadAttachment(contractFile, "下载当前合同") : ""}</section>
    ${actions ? `<section class="record stack"><h2>${actionTitle}</h2>${actions}</section>` : ""}
    <section class="record"><h2>订单信息</h2><dl>${details.map(([key, value]) => pair(key, value, escape)).join("")}</dl></section>
    <section class="record stack"><h2>交易账单</h2><dl>
      ${pair("应付", amount(bill.amount, bill.currency), escape)}${pair("已核验", amount(bill.verified_paid, bill.currency), escape)}
      ${pair("已退款", amount(bill.verified_refunded, bill.currency), escape)}${pair("付款状态", status(bill.payment_status), escape)}</dl>
      ${download(`${orderPath}/statement.csv`, `aix-statement-${order.id}.csv`, "下载交易账单 CSV")}</section>
    ${attachments.length ? `<section class="record stack"><h2>订单附件</h2>${attachments.map((item) => `<p>${escape(labelRef(item.kind))} · ${escape(item.filename)}</p>
      ${downloadAttachment(item, provision?.opening_attachment_id === item.id ? "下载开通凭证" : `下载${labelRef(item.kind)}`)}`).join("")}</section>` : ""}
    ${tokenOrder ? `<section class="record stack"><h2>模型用量</h2>
      <p class="muted">这里显示每次调用的用量金额。交易账单显示订单应付和已核验收款。</p>
      <ul data-model-usage>${usageRows(usage, escape)}</ul>${reconcile}</section>` : ""}
    ${afterSale.length ? `<details class="record"><summary>争议与退款</summary><div class="stack">${afterSale.join("")}</div></details>` : ""}
    <details class="record"><summary>订单进度</summary><ol>${timeline.map((event) => `<li>${escape(timelineLabels[event.action] ?? event.action)} · ${date(event.created_at)}${
      event.action === "order.delivered" && event.details?.summary ? `<p>${escape(event.details.summary)}</p>` : ""}</li>`).join("")}</ol></details>
  </div>`;
  panel.querySelector("[data-order-refresh]").onclick = () => { void ctx.refresh(); };
  for (const el of panel.querySelectorAll("[data-order-action]")) ctx.bind(el, actionHandlers[Number(el.dataset.orderAction)]);
  for (const el of panel.querySelectorAll("[data-order-download]")) {
    el.addEventListener("click", async () => {
      el.disabled = true;
      try {
        await downloadButtons[Number(el.dataset.orderDownload)]();
      } catch (error) {
        ctx.notify(error.message);
      } finally {
        el.disabled = false;
      }
    });
  }
}
