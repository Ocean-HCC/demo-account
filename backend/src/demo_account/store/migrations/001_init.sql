CREATE TABLE accounts (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  initial_cash TEXT NOT NULL,
  commission_rate TEXT NOT NULL,
  min_commission TEXT NOT NULL,
  slippage_rate TEXT NOT NULL,
  block_st INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  webhook_url TEXT,
  webhook_secret TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  order_type TEXT NOT NULL,
  qty INTEGER NOT NULL,
  amount TEXT,
  limit_price TEXT,
  protect_price TEXT,
  idempotency_key TEXT,
  note TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  trade_date TEXT NOT NULL,
  defer_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  reason_code TEXT,
  reason TEXT,
  frozen_cash TEXT NOT NULL DEFAULT '0',
  frozen_qty INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE UNIQUE INDEX ux_orders_idem ON orders(account_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_orders_account_created ON orders(account_id, created_at);
CREATE INDEX ix_orders_status ON orders(status, order_type, trade_date);

CREATE TABLE fills (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  order_id TEXT,
  symbol TEXT NOT NULL,
  kind TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  price TEXT NOT NULL,
  gross_amount TEXT NOT NULL,
  commission TEXT NOT NULL,
  stamp_tax TEXT NOT NULL,
  transfer_fee TEXT NOT NULL,
  cash_delta TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_fills_account ON fills(account_id, seq);
CREATE INDEX ix_fills_account_symbol_date ON fills(account_id, symbol, trade_date);

CREATE TABLE positions (
  account_id TEXT NOT NULL REFERENCES accounts(id),
  symbol TEXT NOT NULL,
  qty INTEGER NOT NULL,
  today_bought_qty INTEGER NOT NULL,
  cost_total TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (account_id, symbol)
);

CREATE TABLE nav_daily (
  account_id TEXT NOT NULL REFERENCES accounts(id),
  trade_date TEXT NOT NULL,
  cash TEXT NOT NULL,
  market_value TEXT NOT NULL,
  total_assets TEXT NOT NULL,
  nav TEXT NOT NULL,
  day_pnl TEXT NOT NULL,
  benchmark_nav TEXT,
  finalized_at TEXT NOT NULL,
  PRIMARY KEY (account_id, trade_date)
);

CREATE TABLE events (
  account_id TEXT NOT NULL REFERENCES accounts(id),
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  order_id TEXT,
  fill_seq INTEGER,
  trade_date TEXT,
  symbol TEXT,
  basis TEXT NOT NULL DEFAULT '{}',
  summary TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  notify INTEGER NOT NULL DEFAULT 0,
  delivered_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  PRIMARY KEY (account_id, seq)
);
CREATE INDEX ix_events_delivery ON events(notify, delivered_at, next_attempt_at);

CREATE TABLE settlement_runs (
  trade_date TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT
);

CREATE TABLE instruments (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  board TEXT NOT NULL,
  exchange TEXT NOT NULL,
  list_date TEXT,
  is_st INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE trading_calendar (
  cal_date TEXT PRIMARY KEY,
  is_open INTEGER NOT NULL,
  source TEXT NOT NULL
);

CREATE TABLE daily_bars (
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open TEXT,
  high TEXT,
  low TEXT,
  close TEXT NOT NULL,
  adj_close TEXT,
  prev_close TEXT,
  volume INTEGER,
  up_limit TEXT,
  down_limit TEXT,
  PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE suspensions (
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE corporate_actions (
  symbol TEXT NOT NULL,
  ex_date TEXT NOT NULL,
  record_date TEXT,
  pay_date TEXT,
  bonus_per_share TEXT NOT NULL DEFAULT '0',
  transfer_per_share TEXT NOT NULL DEFAULT '0',
  cash_per_share TEXT NOT NULL DEFAULT '0',
  factor TEXT,
  source TEXT NOT NULL,
  applied_at TEXT,
  PRIMARY KEY (symbol, ex_date)
);

CREATE TABLE benchmark_daily (
  index_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close TEXT NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (index_code, trade_date)
);

CREATE TABLE alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  trade_date TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
