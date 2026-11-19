const supplierHome = "prototype_screens/prototype_supplier_01_01_supplier_home.svg";
const supplierProducts = "prototype_screens/prototype_supplier_06_06_my_products.svg";
const svgNS = "http://www.w3.org/2000/svg";
const markets = { COMPUTE: "算力", TOKEN_API: "模型 API" };
const statuses = {
  DRAFT: "草稿", SUBMITTED: "待审核", APPROVED: "已批准",
  REJECTED: "已退回", SUSPENDED: "已暂停", PUBLISHED: "已发布",
};
const evidenceKinds = {
  COMPUTE_RIGHTS: "算力权属", TOKEN_AUTHORIZATION: "模型授权", BENCHMARK: "基准测试",
};
const units = {
  GPU_HOUR: "GPU 小时", INSTANCE: "实例", RESERVATION: "预留", SERVICE: "服务",
  TOKEN: "Token", INPUT_TOKEN: "输入 Token", OUTPUT_TOKEN: "输出 Token", REQUEST: "请求",
};
const attachmentAccept = "application/pdf,image/png,image/jpeg";
const post = (ctx, path, body) => ctx.api(path, { method: "POST", body });

function expiry(value) {
  const date = new Date(value);
  if (!(date.getTime() > Date.now())) throw new Error("请选择将来的有效期。");
  return date.toISOString();
}

function labelTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN") : "无截止日期";
}

function options(entries, escape) {
  return entries.map(([value, text]) => `<option value="${escape(value)}">${escape(text)}</option>`).join("");
}

async function dashboard(ctx) {
  const { svg, panel } = await ctx.mount(supplierHome, {
    title: "供应商工作台", top: 780, bottom: 780, remove: ["Bottom Navigation", "Status_Bar"],
  });
  svg.removeAttribute("aria-hidden");
  svg.removeAttribute("role");
  svg.setAttribute("aria-label", "供应商工作台");
  panel.hidden = true;
  const content = svg.querySelector('[id="01 Supplier Home Content"]');
  const setText = (x, y, text) => {
    const node = content.querySelector(`text[x="${x}"][y="${y}"]`);
    if (node) node.textContent = text;
  };
  content.querySelector('[x="24"][y="84"]')?.remove();
  content.querySelector('circle[cx="285"][cy="123"]')?.remove();
  content.querySelector('path[d="M280 134 H290"]')?.remove();
  content.querySelector('path[stroke="#FF641A"]')?.remove();
  setText(24, 176, "供应商工作台");
  setText(40, 228, "进行中订单");
  setText(162, 228, "待报价询价");
  setText(40, 270, "…");
  setText(162, 270, "…");
  setText(40, 320, "当前企业");
  const orgName = ctx.organizationName ?? "供应团队";
  const company = content.querySelector('text[x="40"][y="354"]');
  company.setAttribute("font-size", "17");
  company.textContent = orgName.length <= 14 ? orgName : `${orgName.slice(0, 14)}…`;
  const companyTitle = document.createElementNS(svgNS, "title");
  companyTitle.textContent = orgName;
  company.append(companyTitle);

  const shortcuts = content.querySelector('[id="Home Shortcuts"]');
  const sourceNodes = [...shortcuts.children];
  for (const [index, [hash, label]] of [
    ["rfqs", "处理询价"], ["orders", "查看订单"], ["supply/products", "管理商品"], ["wallet", "订单账单"],
  ].entries()) {
    const nodes = sourceNodes.slice(index * 4, index * 4 + 4);
    const link = document.createElementNS(svgNS, "a");
    link.setAttribute("href", `#${hash}`);
    link.setAttribute("aria-label", label);
    link.setAttribute("pointer-events", "all");
    link.setAttribute("tabindex", "0");
    nodes.at(-1).textContent = label;
    shortcuts.insertBefore(link, nodes[0]);
    link.append(...nodes);
  }

  try {
    const counts = await ctx.api(`/organizations/${encodeURIComponent(ctx.organizationId)}/supplier-overview`);
    setText(40, 270, String(counts.active_orders));
    setText(162, 270, String(counts.pending_quotes));
  } catch (error) {
    setText(40, 270, "—");
    setText(162, 270, "—");
    ctx.notify(error.message);
  }
}

async function productPanel(ctx, title) {
  const { svg, panel } = await ctx.mount(supplierProducts, {
    title, top: 126, bottom: 780,
    remove: ["Bottom Navigation", "Product H100", "Product 4090", "Product A100", "Product API"],
  });
  const content = svg.querySelector('[id="06 My Products Content"]');
  content.querySelector('text[x="24"][y="84"]')?.remove();
  content.querySelector('text[x="22"][y="134"]')?.remove();
  content.querySelector('text[x="160"][y="132"]').textContent = title;
  for (const node of content.querySelectorAll('rect[y="158"], text[y="183"], rect[y="552"], text[y="578"]')) {
    node.remove();
  }
  return panel;
}

async function listSuppliers(ctx, operator) {
  const panel = await productPanel(ctx, operator ? "供应审核" : "供应商品与资质");
  const e = ctx.escape;
  let suppliers;
  if (operator) {
    const queues = await Promise.all(["SUBMITTED", "APPROVED", "SUSPENDED"].map(
      (status) => ctx.api(`/admin/suppliers?status=${status}&limit=100`)
    ));
    suppliers = queues.flat();
  } else {
    suppliers = await ctx.api(ctx.scoped("/suppliers"));
  }
  const availableMarkets = Object.entries(markets).filter(([market]) => !suppliers.some((item) => item.market === market));
  panel.innerHTML = `
    <div class="stack">
      ${operator ? "" : '<a class="secondary" href="#supply">返回供应商工作台</a>'}
      <section class="stack" aria-label="供应档案">
        ${suppliers.length ? suppliers.map((item) => `
          <article class="record stack">
            <strong>${e(item.name)}</strong>
            <span class="muted">${e(markets[item.market])} · ${e(statuses[item.status])}</span>
            <a class="secondary" href="#supply/${encodeURIComponent(item.id)}">${operator ? "查看审核资料" : "管理资质和商品"}</a>
          </article>
        `).join("") : '<p class="muted">当前没有供应档案。</p>'}
      </section>
      ${!operator && availableMarkets.length ? `
        <form data-register-supplier class="record stack">
          <h2>登记供应档案</h2>
          <label>供应商名称<input name="name" maxlength="120" required></label>
          <label>市场<select name="market">${options(availableMarkets, e)}</select></label>
          <button class="primary" type="submit">登记供应档案</button>
        </form>` : ""}
    </div>`;
  const form = panel.querySelector("[data-register-supplier]");
  if (form) ctx.bind(form, async (fields) => {
    const supplier = await post(ctx, "/suppliers", {
      organization_id: ctx.organizationId, name: fields.name.trim(), market: fields.market,
    });
    ctx.notify("供应档案已登记。");
    await ctx.route(`supply/${supplier.id}`);
  });
}

function productForm(compute, e) {
  const marketUnits = compute ? ["GPU_HOUR", "INSTANCE", "RESERVATION", "SERVICE"] : ["TOKEN", "INPUT_TOKEN", "OUTPUT_TOKEN", "REQUEST"];
  return `<form class="record stack" data-product>
    <h2>新建商品</h2>
    <label>商品名称<input name="name" maxlength="120" required></label>
    ${compute ? `
      <label>GPU 型号<input name="gpu_model" maxlength="120" required></label>
      <label>每份 GPU 张数<input name="gpu_count" type="number" min="1" max="10000" required></label>
      <label>显存（GB）<input name="vram_gb" type="number" min="1" max="1024" required></label>
      <label>物理机房地区<input name="physical_region" maxlength="80" placeholder="例如 us-east-1" required></label>
    ` : `
      <label>模型名称<input name="model" maxlength="120" required></label>
      <label>模型版本<input name="model_version" maxlength="120" required></label>
    `}
    <label>服务地区<input name="service_region" maxlength="80" placeholder="例如 US" required></label>
    <label>计费单位<select name="unit">${options(marketUnits.map((unit) => [unit, units[unit]]), e)}</select></label>
    <label>币种<select name="currency"><option>USD</option><option>CNY</option></select></label>
    <label>单价<input name="unit_price" type="number" min="0.00000001" step="0.00000001" required></label>
    <button class="primary" type="submit">创建草稿</button>
  </form>`;
}

const reviewOptions = {
  SUBMITTED: [["APPROVED", "批准"], ["REJECTED", "退回"]],
  APPROVED: [["SUSPENDED", "暂停"]],
  SUSPENDED: [["APPROVED", "恢复"]],
};

async function supplierDetails(ctx, id, operator) {
  const e = ctx.escape;
  const path = `/suppliers/${encodeURIComponent(id)}`;
  const supplier = await ctx.api(path);
  if (!operator && supplier.organization_id !== ctx.organizationId) throw new Error("当前企业无法访问此供应档案。");
  const compute = supplier.market === "COMPUTE";
  const rightKind = compute ? "COMPUTE_RIGHTS" : "TOKEN_AUTHORIZATION";
  const panel = await productPanel(ctx, operator ? "供应审核" : "供应商品与资质");
  const [evidence, products, attachments] = await Promise.all([
    ctx.api(`${path}/evidences`), ctx.api(`${path}/products`), ctx.api(`${path}/attachments?limit=100`),
  ]);
  const activeRights = evidence.filter((item) => item.kind === rightKind && Date.parse(item.valid_until) > Date.now());
  const currentReviewed = activeRights.filter((item) => item.reviewed_at);
  const fileByRef = new Map(attachments.map((item) => [item.reference, item]));
  const canEditEvidence = !operator && supplier.status !== "SUBMITTED";
  const canSubmit = !operator && activeRights.length && ["DRAFT", "REJECTED", "APPROVED"].includes(supplier.status);
  panel.innerHTML = `<div class="stack">
    <a class="secondary" href="#supply/products">返回供应档案</a>
    <div class="record stack">
      <h2>${e(supplier.name)}</h2>
      <span class="muted">${e(markets[supplier.market])} · ${e(statuses[supplier.status])}</span>
      ${supplier.review_reason ? `<p>${e(supplier.review_reason)}</p>` : ""}
    </div>
    <section class="record stack" aria-label="供应商凭证">
      <h2>供应商凭证</h2>
      ${evidence.length ? evidence.map((item) => {
        const file = fileByRef.get(item.reference);
        return `<div class="stack">
          <strong>${e(evidenceKinds[item.kind])} · ${item.reviewed_at ? "已审" : "待审"}</strong>
          <span class="muted">${e(labelTime(item.valid_until))}</span>
          ${file ? `<button class="secondary" type="button" data-file="${e(file.id)}">下载 ${e(file.filename)}</button>` : `<span class="muted">${e(item.reference)}</span>`}
        </div>`;
      }).join("") : '<p class="muted">暂无凭证。</p>'}
      ${canEditEvidence ? `<form class="stack" data-evidence>
        <label>凭证类型<select name="kind">${options((compute ? [rightKind, "BENCHMARK"] : [rightKind]).map(
          (kind) => [kind, evidenceKinds[kind]]
        ), e)}</select></label>
        <label>有效期<input name="valid_until" type="datetime-local" required></label>
        <label>凭证文件<input name="file" type="file" accept="${attachmentAccept}" required></label>
        <button type="submit" class="secondary">上传并登记凭证</button>
      </form>` : ""}
      ${canSubmit ? '<form data-submit><button type="submit" class="primary">提交供应审核</button></form>' : ""}
      ${operator && reviewOptions[supplier.status] ? `<form data-review class="stack">
        <label>审核决定<select name="decision">${options(reviewOptions[supplier.status], e)}</select></label>
        <label>审核说明<textarea name="reason" maxlength="500" required></textarea></label>
        <button type="submit" class="primary">保存审核决定</button>
      </form>` : ""}
    </section>
    <section class="stack" aria-label="供应商品">
      <h2>供应商品</h2>
      ${products.length ? products.map((item) => `
        <article class="record stack"><strong>${e(item.name)}</strong>
          <span class="muted">${e(item.unit_price)} ${e(item.currency)} / ${e(units[item.unit] ?? item.unit)} · ${e(statuses[item.status])}</span>
          ${compute ? `
            <details data-resources="${e(item.id)}"><summary>算力资源</summary><div class="stack" data-resources-body></div></details>
          ` : ""}
          ${operator && supplier.status === "APPROVED" && item.status === "DRAFT" && currentReviewed.length ? `
            <form data-publish="${e(item.id)}" class="stack">
              <label>发布凭证<select name="evidence_id">${options(currentReviewed.map(
                (entry) => [entry.id, `${fileByRef.get(entry.reference)?.filename ?? entry.reference} · ${labelTime(entry.valid_until)}`]
              ), e)}</select></label>
              <button type="submit" class="primary">发布商品</button>
            </form>
          ` : ""}
        </article>`).join("") : '<p class="muted">暂无商品。</p>'}
    </section>
    ${operator ? "" : productForm(compute, e)}
  </div>`;

  const save = (form, action, text) => {
    if (form) ctx.bind(form, async (fields) => {
      await action(fields);
      ctx.notify(text);
      await ctx.refresh();
    });
  };
  const evidenceForm = panel.querySelector("[data-evidence]");
  if (evidenceForm) {
    const syncValidity = () => {
      evidenceForm.elements.valid_until.required = evidenceForm.elements.kind.value !== "BENCHMARK";
    };
    evidenceForm.elements.kind.addEventListener("change", syncValidity);
    save(evidenceForm, async (fields) => {
      const valid_until = fields.valid_until ? expiry(fields.valid_until) : null;
      const uploaded = await ctx.upload(`${path}/attachments`, fields.file);
      await post(ctx, `${path}/evidences`, { kind: fields.kind, reference: uploaded.reference, valid_until });
    }, "已登记供应凭证。");
  }
  save(panel.querySelector("[data-submit]"), () => post(ctx, `${path}/submit`), "供应档案已提交审核。");
  save(panel.querySelector("[data-review]"), (fields) => post(ctx, `/admin${path}/review`, {
    decision: fields.decision, reason: fields.reason.trim(),
  }), "已保存审核决定。");
  save(panel.querySelector("[data-product]"), (fields) => post(ctx, "/products", {
    supplier_id: supplier.id, market: supplier.market,
    name: fields.name.trim(), service_region: fields.service_region.trim(),
    unit: fields.unit, currency: fields.currency, unit_price: fields.unit_price,
    specification: compute ? {
      gpu_model: fields.gpu_model.trim(), gpu_count: Number(fields.gpu_count),
      vram_gb: Number(fields.vram_gb), physical_region: fields.physical_region.trim(),
    } : { model: fields.model.trim(), model_version: fields.model_version.trim(), api_type: "TEXT" },
  }), "已创建商品草稿。");
  for (const form of panel.querySelectorAll("[data-publish]")) {
    save(form, (fields) => post(ctx, `/products/${encodeURIComponent(form.dataset.publish)}/publish`, {
      evidence_id: fields.evidence_id,
    }), "商品已发布。");
  }
  for (const button of panel.querySelectorAll("[data-file]")) {
    button.addEventListener("click", async () => {
      const file = attachments.find((item) => item.id === button.dataset.file);
      try {
        await ctx.download(`${path}/attachments/${encodeURIComponent(file.id)}`, file.filename);
      } catch (error) {
        ctx.notify(error.message);
      }
    });
  }

  for (const details of panel.querySelectorAll("[data-resources]")) {
    const product = products.find((item) => item.id === details.dataset.resources);
    let loaded = false;
    details.addEventListener("toggle", async () => {
      if (!details.open || loaded) return;
      loaded = true;
      const body = details.querySelector("[data-resources-body]");
      try {
        await renderResources(ctx, body, product, activeRights, evidence, fileByRef, operator, save);
      } catch (error) {
        loaded = false;
        body.textContent = error.message;
      }
    });
  }
}

async function renderResources(ctx, body, product, activeRights, evidence, fileByRef, operator, save) {
  const e = ctx.escape;
  const resources = await ctx.api(`/compute/resources?product_id=${encodeURIComponent(product.id)}`);
  const benchmarks = evidence.filter((item) => item.kind === "BENCHMARK" && (
    !item.valid_until || Date.parse(item.valid_until) > Date.now()
  ));
  const documentOptions = (docs) => options(docs.map((item) => [
    item.id, fileByRef.get(item.reference)?.filename ?? item.reference,
  ]), e);
  body.innerHTML = `
    ${resources.length ? resources.map((item) => `<div class="record stack">
      <strong>${e(item.gpu_model)} · ${e(item.gpu_count)} 卡 · ${e(item.physical_region)}</strong>
      <span class="muted">${item.verified_at ? "已核验" : "待核验"} · 权属有效至 ${e(labelTime(item.rights_valid_until))}</span>
      ${operator && !item.verified_at ? `<form class="stack" data-check-resource="${e(item.id)}">
        <label>核验说明<textarea name="note" maxlength="500" required></textarea></label>
        <button type="submit" class="primary">核验算力资源</button></form>` : ""}
      </div>`).join("") : '<p class="muted">暂无已登记的算力资源。</p>'}
    ${!operator ? activeRights.length && benchmarks.length ? `
      <form class="stack" data-register-resource><label>可用 GPU 张数<input type="number" name="count" required min="${e(product.specification.gpu_count)}" max="10000"></label>
        <label>权属凭证<select name="rights">${documentOptions(activeRights)}</select></label>
        <label>基准报告<select name="benchmark">${documentOptions(benchmarks)}</select></label>
        <button type="submit" class="secondary">登记算力资源</button>
      </form>` : '<p class="muted">登记算力资源需要有效权属凭证和基准测试报告。</p>' : ""}`;
  save(body.querySelector("[data-register-resource]"), (fields) => {
    const rights = activeRights.find((item) => item.id === fields.rights);
    const benchmark = benchmarks.find((item) => item.id === fields.benchmark);
    return post(ctx, "/compute/resources", {
      product_id: product.id, gpu_model: product.specification.gpu_model, vram_gb: product.specification.vram_gb,
      physical_region: product.specification.physical_region ?? product.service_region,
      gpu_count: Number(fields.count), ownership_reference: rights.reference,
      rights_valid_until: expiry(rights.valid_until), benchmark_reference: benchmark.reference,
    });
  }, "已登记算力资源。");
  for (const form of body.querySelectorAll("[data-check-resource]")) {
    save(form, (fields) => post(ctx, `/compute/resources/${encodeURIComponent(form.dataset.checkResource)}/verification`, {
      note: fields.note.trim(),
    }), "算力资源已核验。");
  }
}

export async function renderSupplier(ctx, id) {
  const operator = ctx.role === "operator";
  if (!operator && (ctx.role !== "supplier" || !ctx.organizationId)) {
    throw new Error("请切换至有供给权限的企业。");
  }
  if (!id && !operator) return dashboard(ctx);
  if (!id || id === "products") return listSuppliers(ctx, operator);
  return supplierDetails(ctx, id, operator);
}
