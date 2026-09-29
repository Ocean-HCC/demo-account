# demo-account：A 股模拟券商柜台

一个独立运行的 A 股模拟账户服务。策略程序或 AI Agent 通过 HTTP 接口开户、下单、查询持仓与净值、接收成交通知；你通过网页控制台查看账户、手动下单、按时间线复盘每一笔决策。撮合用真实行情，按沪深交易所规则执行 T+1、申报数量、涨跌停、佣金与印花税、除权除息，全程不涉及真实资金。

项目按蓝图协作方式开发，`docs/` 下的四层文档是唯一的规范来源，本文件只讲怎么用：

| 文档 | 内容 |
|---|---|
| [docs/intent.md](docs/intent.md) | 为什么做、交付什么、边界 |
| [docs/requirements.md](docs/requirements.md) | 角色与场景、交易所与税务规定、第一版范围 |
| [docs/solution.md](docs/solution.md) | 订单类型、撮合、结算、留痕与复盘的业务机制 |
| [docs/implementation.md](docs/implementation.md) | 架构、数据模型、接口、数据源、配置与工程约定 |

目录

1. 运行
2. 连接 tick-stock-panel
3. 给策略程序用的接口
4. 日常运维
5. 开发

---

## 1. 运行

```bash
git clone git@github.com:Ocean-HCC/demo-account.git
```

需要 Python 3.12 与 [uv](https://docs.astral.sh/uv/)；构建控制台需要 Node 与 pnpm。服务默认监听 http://127.0.0.1:8770 ，同时提供接口和控制台。数据库路径的相对路径按仓库根目录解析。

### 1.1 用 Mock 行情体验

Mock 行情由固定种子生成，不依赖任何外部服务，适合先熟悉控制台和接口。

```bash
cd frontend && pnpm install && pnpm build
cd ../backend && uv sync
uv run python scripts/seed_mock.py        # 可选：生成两个账户近一个月的演示历史，到昨天为止
DEMO_ACCOUNT_MARKET=mock DEMO_ACCOUNT_DB_PATH=data/mock.sqlite3 uv run demo-account serve
```

盘中价格在当日开盘价与收盘价之间插值。即时市价单只在连续竞价时段（9:30 至 11:30、13:00 至 14:57）可以提交，其余时间请用限价单、开盘单或收盘单。

### 1.2 用真实行情运行

```bash
cp .env.example .env                      # 按需修改，见第 2 节
cd frontend && pnpm install && pnpm build
cd ../backend && uv sync && uv run demo-account serve
```

启动后先看 http://127.0.0.1:8770/api/health ：`quote_source` 与 `reference_source` 显示数据源是否可用，`clock_skew` 显示本机时钟是否与服务器时间一致。

---

## 2. 连接 tick-stock-panel

行情快照、日线、指数与标的信息来自你本机运行的 tick-stock-panel（下称 TSP，默认 http://127.0.0.1:3018 ）；交易日历来自深交所官网，停复牌与股票分红送转来自东方财富数据中心，沪深 300 在 TSP 缺数据时改用新浪。TSP 需要满足：

- **实时行情**：TSP 已配置能提供实时行情的数据源（TickFlow 付费 key，或 fuyao、stock-sdk 插件）。没有实时行情时即时单与限价单无法成交，健康检查会显示"TSP 未开启实时行情"。
- **ETF**：要交易 ETF 时，在 TSP 开启 ETF 的实时与日线拉取（偏好 `realtime_pull_etf`、`pipeline_pull_etf`）。不开实时拉取时盘中取不到 ETF 行情，ETF 订单无法成交；ETF 的除权除息依赖 TSP 的复权数据。
- **指数**：TSP 已同步指数列表。
- **密码**：TSP 设了访问密码时，把密码写进 `.env` 的 `DEMO_ACCOUNT_TSP_PASSWORD`。

demo-account 只读 TSP 的本地缓存接口，不调用会让 TSP 向其上游拉数的分时接口，不占用 TSP 的行情额度。

接入前在 TSP 所在机器上跑两次验证：交易时段内一次，验证即时单成交；交易日 16:00 以后一次，验证当日日线与当日结算。每次都会逐项检查连通、行情、日线、指数、公开接口与本机时钟，并用临时库跑通开户、下单与结算。结果写进 `backend/verify-output/`：`report.md` 给人看，`report.json` 给程序看，`samples/` 是原始响应样本（不含密码），该目录随仓库提交、每次验证覆盖，契约测试用到的样本另外固定在 `backend/tests/fixtures/`，可对照 [docs/verification.md](docs/verification.md) 里已入库的一次真实运行：

```bash
cd backend && uv run demo-account verify-tsp
```本机系统代理会拦截部分行情主机，HTTP 客户端默认不读系统代理（`DEMO_ACCOUNT_HTTP_TRUST_ENV=false`）。

---

## 3. 给策略程序用的接口

接口前缀 `/api`，JSON 请求与响应；金额、价格、比率一律是字符串，时间是带 `+08:00` 的 ISO 8601。完整清单与错误码见 [docs/implementation.md](docs/implementation.md) 第 6 章，业务规则见 [docs/solution.md](docs/solution.md) 第 3、4 章。

### 3.1 常用流程

```bash
# 开户：资金只在开户时注入一次
curl -s -X POST http://127.0.0.1:8770/api/accounts -H 'Content-Type: application/json' \
  -d '{"name": "均线策略 A", "initial_cash": "1000000", "note": "20/60 日均线"}'

# 下单前预估：冻结金额、费用、所属交易日与可能的拒绝原因
curl -s -X POST http://127.0.0.1:8770/api/accounts/<账户号>/orders/preview -H 'Content-Type: application/json' \
  -d '{"symbol": "600000.SH", "side": "buy", "order_type": "limit", "qty": 1000, "limit_price": "8.95"}'

# 下单：带幂等键防止重试时重复下单；备注与标签会记入订单和事件序列，供复盘
curl -s -X POST http://127.0.0.1:8770/api/accounts/<账户号>/orders -H 'Content-Type: application/json' \
  -d '{"symbol": "600000.SH", "side": "buy", "order_type": "limit", "qty": 1000, "limit_price": "8.95",
       "idempotency_key": "ma-20260928-001", "note": "20 日均线上穿 60 日均线", "tags": ["ma-cross"]}'

# 查询：概览、持仓、订单、成交、每日净值、回合统计、事件序列
curl -s http://127.0.0.1:8770/api/accounts/<账户号>
curl -s http://127.0.0.1:8770/api/accounts/<账户号>/positions
curl -s "http://127.0.0.1:8770/api/accounts/<账户号>/events?after=0&types=order_filled,order_rejected"
```

### 3.2 四种订单

| 类型 | `order_type` | 何时可提交 | 何时成交 | 成交价 |
|---|---|---|---|---|
| 即时市价单 | `market` | 连续竞价时段 | 之后第一笔有效快照，180 秒内无行情则失效 | 最新价加不利滑点 |
| 限价单 | `limit` | 随时 | 所属交易日连续竞价时段触及限价，收盘未成交失效 | 限价 |
| 开盘单 | `open` | 随时，9:25 前算当日 | 最近一次开盘，停牌顺延，最多 3 个交易日 | 开盘价加不利滑点 |
| 收盘单 | `close` | 随时，15:30 前算当日 | 最近一次收盘，可带保护限价 `protect_price` | 收盘价 |

买入可以用 `amount`（元）代替 `qty`，系统按冻结价折算成合规的最大数量。

### 3.3 结果与通知

- **校验失败**：订单照样落库留痕，接口返回 HTTP 400（账户冻结或归档返回 409），`error.code` 即拒绝原因，`error.details.order` 是这笔被拒订单。
- **撮合与结算阶段的结果**：写在订单的 `status` 与 `reason_code`，并进入账户事件序列。
- **主动通知**：用 `PUT /api/accounts/<账户号>/webhook` 设置回调地址与签名密钥后，成交、拒绝、失效、撤销和结算完成事件会推送过来。签名头是 `X-Demo-Account-Signature: sha256=<HMAC>`；投递至少一次，请按 `event_id` 去重，漏掉的用 `GET .../events?after=<序号>` 补拉。
- **控制台**：订阅 `GET /api/events/stream`（SSE）实时刷新。

---

## 4. 日常运维

- **自动任务**：8:30 刷新参考数据；连续竞价时段按快照周期撮合；16:00 日终结算，失败每 10 分钟重试直到完成，不设截止，非交易日也重试；服务启动时补跑错过的交易日。
- **结算未完成时暂停交易**：只要有已过 16:00 却还没结算完的交易日，就不接受新订单（`SETTLEMENT_CATCHUP`）、不撮合，直到按日期顺序补齐；撤单与查询不受影响。`GET /api/health` 的 `trading_paused` 与 `pending_settlement_date` 显示当前状态。
- **手动命令**：在 `backend/` 下执行 `uv run demo-account settle --date 2026-09-28` 补结算某天（更早的交易日还没结算时会拒绝，需按日期顺序），`uv run demo-account reconcile <账户号>` 用台账重放核对持仓。
- **告警**：结算数据缺失、数据源连续失败、台账不一致、时钟偏差等写入告警，控制台页头显示未解决数量，也可用 `GET /api/admin/alerts` 查看。
- **备份**：复制 `data/` 下的 SQLite 文件即可；运行中备份请先停服，或连同 `-wal` 文件一起复制。
- **日志**：JSON 行输出到标准错误。

---

## 5. 开发

```bash
cd backend
uv run pytest -q                                                   # core、服务、接口、适配器测试
DEMO_ACCOUNT_LIVE_TESTS=1 uv run pytest tests/test_live_public.py  # 真实公开接口，需要网络
uv run ruff check . && uv run ruff format --check . && uv run mypy
cd ../frontend && pnpm dev                                         # Vite 开发服务器，/api 代理到 8770
```

修改业务规则前先改蓝图：规则在需求与方案里定义，实现文档说明怎么落地，代码只按图施工。
