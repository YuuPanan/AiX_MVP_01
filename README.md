# AiX_MVP_01

第一阶段试点采用 FastAPI、PostgreSQL 和 Alembic。买家、供应商和运营共用一套询报价、订单、合同、付款核验、履约验收、争议和账单接口。Token 试点接一款授权文本模型。Compute 由运营核验资源和记录实际开通。

## 本地试点入口

[打开 AIX 源画板工作台](http://127.0.0.1:8765/)

运行目录为 `AIX-UI-SVG-Sources/app`。入口按路由加载同目录外层的原始 SVG，在对应画板上渲染企业、目录、询报价、订单与账单数据。画板下方的导航和供应面板中的快捷入口可直接操作。以下流程使用同源 `/api/v1`。

1. 用邮箱和密码注册及登录，从「企业」创建企业或接受成员邀请。顶部可切换企业。企业负责人可在采购和供应模式间切换，其他成员按授权角色进入相应页面。
2. 买家从市场筛选 GPU 或模型商品并发起询价。供应商从工作台查看待报价和进行中订单，打开询价后可沟通并提交报价。买家确认报价后生成订单。
3. 买家确认报价后，运营在「待办」归档合同和付款指引。订单会分别显示买方与供应方是否已确认。双方确认后，买家可用「刷新订单」读取付款指引并提交回单，运营按收款机构的已入账记录核验。
4. 回单通过后，运营在「待办」开始履约，GPU 订单还需登记实例开通。供应商提交交付、买家验收后，订单进入运营的结单待办。「账单」可查看金额以及下载 CSV。
5. 供应商可从「供给」登记档案、提交资质、建商品草稿和登记 GPU 资源。操作由当前企业权限和运营审核状态控制。

附件可使用 PDF、PNG 和 JPEG，单文件最多 5 MiB。上传、下载和回单审核都有审计记录。浏览器仅在当前页面会话中持有访问令牌，刷新后重新登录。本地试点使用数据库 `aix`。

### 供应商订单监控和通知

在「供应」模式下，从底部「监控」或订单列表进入订单监控。选择订单可查看当前状态、容量预留、已登记的实例开通、SLA 事件、账单及近期状态记录。可从详情打开订单或联系买家，点击「刷新状态」重新读取后端记录。

底部「通知」按订单、询报价和系统分类显示同一供应企业的业务事件，点击可打开对应订单或询价。「刷新通知」会保留当前分类。监控以订单和已登记的履约记录为准，供应商侧目前不采集实时 GPU 利用率或温度，通知在站内查看。

## 本地启动

在仓库根目录的 PowerShell 中执行，需要 Python 3.12+、uv 和 Docker。首次启动前设置本机专用的数据库密码。以后复用同一数据卷时沿用该密码。

```powershell
$env:AIX_POSTGRES_PASSWORD = '<本机数据库密码>'
docker compose -p aix-mvp-01 --profile scanner up -d --wait
uv sync --locked
$dbPassword = [Uri]::EscapeDataString($env:AIX_POSTGRES_PASSWORD)
$env:DATABASE_URL = "postgresql://aix:${dbPassword}@127.0.0.1:5436/aix"
$env:AIX_CLAMD_PORT = '13310'
uv run --locked alembic upgrade head
uv run --locked python -m aix operator operator@example.com
uv run --locked uvicorn aix.main:app --host 127.0.0.1 --port 8765
```

首次创建运营账号时在终端交互输入密码。普通账号可通过工作台注册。容器启动后保留数据库卷，运行工作台前确认本机的 8765、5436 和 13310 端口可用。

本地 Compose 在回环地址 `127.0.0.1:13310` 提供 ClamAV。等容器健康后再进行文件上传。已有本机 ClamAV 时可改用 `AIX_CLAMSCAN` 指定 `clamscan` 可执行文件并维护签名库，设置 `AIX_CLAMD_PORT` 时优先使用守护进程。 [ClamAV 扫描文档](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)

- 接口文档在 [Swagger UI](http://127.0.0.1:8765/docs)，机器可读契约在 [OpenAPI JSON](http://127.0.0.1:8765/openapi.json)。
- 健康检查在 [/healthz](http://127.0.0.1:8765/healthz)。业务 API 前缀为 `/api/v1`。
- 独立域名前端对接时，在 API 进程启动前设置 `AIX_CORS_ORIGINS`，填写实际前端源，如 `http://localhost:5173`。多个源用逗号分隔。
- 受保护的接口使用 `Authorization: Bearer <access_token>`。每个响应包含 `X-Request-ID`，可用于关联审计事件。

## 前端联调顺序

| 顺序 | 页面或流程 | 接口 |
| --- | --- | --- |
| 1 | 双市场目录、筛选和详情 | `GET /products`、`GET /products/{id}`、`GET /suppliers/{id}/public-profile` |
| 2 | 登录、企业和成员权限 | `/auth/register`、`/auth/login`、`/users/me`、`/organizations`、`/organizations/{id}/members`、`/organizations/{id}/invitations` |
| 3 | 询价、报价、下单 | `POST /rfqs`、`GET /rfqs?state=SUBMITTED`、`POST /rfqs/{id}/quotes`、`POST /rfqs/{id}/decline`、`POST /quotes/{id}/accept` |
| 4 | 订单、容量预留、Wallet 和售后 | `/orders`、`/orders/{id}/timeline`、`/orders/{id}/contract`、`/compute/orders/{id}/reservation`、`/billing/statements` |
| 5 | 受控附件与回单审核 | `/orders/{id}/attachments`、`/suppliers/{id}/attachments`、`/orders/{id}/payment-receipts`、`/orders/{id}/payment-receipts/{receiptId}/review` |
| 6 | Token 用量与算力交付 | `/usage/records?order_id=…`、`/usage/summary?order_id=…`、`/compute/orders/{id}/provisioning`、`/compute/orders/{id}/sla-events` |
| 7 | 供应商概览和运营待办 | `GET /organizations/{id}/supplier-overview`、`GET /admin/orders/exception-list` |
| 8 | 供应商订单监控和通知 | `GET /organizations/{id}/supplier-orders`、`GET /organizations/{id}/supplier-orders/{orderId}`、`GET /organizations/{id}/supplier-notifications` |

金额和数量在 JSON 中建议用十进制字符串提交，前端按订单币种及原生单位展示。试点订单使用 USD 或 CNY。产品目录只显示已发布且供应商授权证据仍有效的商品。

商品搜索 `q` 支持按商品名、供应商名、GPU 型号或 API 模型名匹配。GPU 目录支持 `gpu_model`、`physical_region`、`service_region`、`min_price` 和 `max_price`。价格范围和 `sort=price_asc|price_desc` 需同时指定币种与计价单位，例如 `currency=USD&unit=GPU_HOUR`。商品返回 `supplier_name`。公开供应商资料的 `verified` 表示当前至少有一款授权有效的已发布商品。

## 试点流程

1. 供应商创建企业和供应商档案，登记授权或资源权属证据，提交审核。运营批准档案后审核发布商品。商务区域和资质附件由运营按实际业务资料核验。
2. 买家提交询价。供应商或运营报价后，买家接受报价。订单冻结规格、计价单位、价格、双方主体和交易条款。重复接受同一报价返回同一订单。
3. 运营归档合同版本、受控文件引用及 SHA-256。Compute 须先由供应商登记资源权属与基准测试引用，再由运营核验。买卖双方各自确认合同后，运营通过回单审核接口登记收款方已入账记录。审批时需要同一订单上的 `BANK_CREDIT` 附件，凭证内容应来自收款方。
4. Token 订单由买家在履约中调用模型，形成用量和成本核对记录。Compute 的实际实例和开通由供应商与运营执行，试点记录实例、时间、负责人和扫描通过的开通凭证。容量冲突按已核验资源和时间窗检查。对同一物理容量的不同申报，需要运营核对。
5. 交付记录关联履约证据。买家验收后，运营完成订单。争议可补证并记录处理结论。退款由运营审批并录入外部退款流水，账单展示应付、已核验实收和实退。

新附件使用系统生成的 `attachment:<id>` 引用。历史外部档案可继续使用原引用，读取权限由档案系统管理。Wallet 用于记录订单和外部支付状态。邀请由企业所有者发放一次性代码并交付给成员。

### 按时间预订 GPU

对 `COMPUTE`、`GPU_HOUR` 商品，使用以下格式调用 `POST /api/v1/rfqs`。将示例日期替换为确认证据有效期内的未来时间。

```json
{
  "organization_id": "<买家企业 ID>",
  "product_id": "<商品 ID>",
  "compute_schedule": {
    "gpu_count": 8,
    "starts_at": "2026-12-01T09:00:00+08:00",
    "duration_hours": 168
  },
  "requirements": "用于一周的模型推理测试"
}
```

服务端计算 1,344 GPU 小时，并把规范化的时间窗保存到报价和订单。页面可用目录单价展示估算，付款以正式报价为准。

1. 运营归档合同时，系统为订单在一个已核验资源池预留 GPU。供应商授权和资源权属都须覆盖整个租用时间窗。容量不足时返回 `409`。
2. 未付款的预留最多保留 24 小时，且最晚到租用开始时间。买卖双方确认合同且预留有效时，才向交易双方展示付款指引。前端可读取 `GET /compute/orders/{id}/reservation`，并显示 `status` 和 `expires_at`。
3. 运营应在预留期限内核验外部付款。过期后可由运营通过 `POST /compute/orders/{id}/reservation` 重新申请同一时间窗，系统会再次校验容量。若外部款项已到账但容量无法恢复，需要人工处理款项和订单。
4. 核验付款后，预留转为已确认。运营按预留中的资源、时间窗和 GPU 张数记录开通。开通前完成全额退款会释放已确认的预留。

历史询价和继续使用 `quantity` 加 `requirements` 的请求保留固定规格流程。新时间窗功能以单个已核验资源池为单位计算容量。

### 配置授权文本 API

在服务启动前，通过环境变量或部署环境的密钥注入配置以下值。

| 变量 | 值 |
| --- | --- |
| `AIX_TOKEN_PRODUCT_ID` | 已审核并发布的文本商品 ID |
| `AIX_TOKEN_PROVIDER_URL` | 上游 HTTPS Chat Completions 完整地址 |
| `AIX_TOKEN_PROVIDER_KEY` | 已授权的供应商密钥 |
| `AIX_TOKEN_PROVIDER_MODEL` | 与审核资料一致的上游模型标识，响应的 `model` 也须匹配 |
| `AIX_TOKEN_PROVIDER_CURRENCY` | 上游成本币种，USD 或 CNY |
| `AIX_TOKEN_INPUT_RATE_PER_MILLION` / `AIX_TOKEN_OUTPUT_RATE_PER_MILLION` | 按每百万输入或输出 Token 计算的已确认费率 |

调用 `POST /api/v1/model-gateway/{modelAlias}/chat/completions`，使用买家身份和 `Idempotency-Key` UUID，并提交 `order_id`、`messages` 和可选的 `max_output_tokens`。每笔订单同时允许一个上游请求。重传同一键需通过用量接口查询原请求。超时或无法核验的响应进入人工对账，运营确认实际用量或确认上游未计费后才能继续。接口保存请求标识、统计用量、耗时和当次费率。上游成本按已配置的固定费率计算，分级、缓存折扣和不同币种的财务对账需另行核实。
