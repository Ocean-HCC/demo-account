// 与后端接口一一对应的类型（实现 6）。金额、价格、比率一律为字符串。
export type Decimal = string;
export type AccountStatus = 'active' | 'frozen' | 'archived';
export type Side = 'buy' | 'sell';
export type OrderType = 'market' | 'limit' | 'open' | 'close';
export type OrderStatus = 'pending' | 'filled' | 'rejected' | 'cancelled' | 'expired';
export type FillKind = 'trade' | 'corporate_action' | 'reversal';
export type EventType =
  | 'order_submitted'
  | 'order_rejected'
  | 'order_filled'
  | 'order_cancelled'
  | 'order_expired'
  | 'settlement_done'
  | 'corporate_action'
  | 'reversal'
  | 'account_created'
  | 'account_frozen'
  | 'account_unfrozen'
  | 'account_archived'
  | 'fee_params_changed';

export const EVENT_TYPES: EventType[] = [
  'order_submitted',
  'order_rejected',
  'order_filled',
  'order_cancelled',
  'order_expired',
  'settlement_done',
  'corporate_action',
  'reversal',
  'account_created',
  'account_frozen',
  'account_unfrozen',
  'account_archived',
  'fee_params_changed',
];

export interface Account {
  id: string;
  name: string;
  note: string;
  initial_cash: Decimal;
  commission_rate: Decimal;
  min_commission: Decimal;
  slippage_rate: Decimal;
  block_st: boolean;
  status: AccountStatus;
  webhook_url: string | null;
  webhook_configured: boolean;
  created_at: string;
  updated_at: string;
}

export interface Overview {
  account: Account;
  cash: Decimal;
  frozen_cash: Decimal;
  available_cash: Decimal;
  market_value: Decimal;
  total_assets: Decimal;
  nav: Decimal;
  day_pnl: Decimal;
  estimated: boolean;
  positions_count: number;
  as_of: string;
}

export interface Position {
  symbol: string;
  name: string | null;
  qty: number;
  sellable_qty: number;
  today_bought_qty: number;
  frozen_qty: number;
  cost_total: Decimal;
  avg_cost: Decimal | null;
  last_price: Decimal | null;
  price_source: 'snapshot' | 'close' | 'prev_close';
  market_value: Decimal;
  unrealized_pnl: Decimal;
  weight: Decimal | null;
}

export interface Order {
  id: string;
  account_id: string;
  symbol: string;
  side: Side;
  order_type: OrderType;
  qty: number;
  amount: Decimal | null;
  limit_price: Decimal | null;
  protect_price: Decimal | null;
  idempotency_key: string | null;
  note: string;
  tags: string[];
  trade_date: string;
  defer_count: number;
  status: OrderStatus;
  reason_code: string | null;
  reason: string | null;
  frozen_cash: Decimal;
  frozen_qty: number;
  created_at: string;
  finished_at: string | null;
}

export interface Fill {
  seq: number;
  account_id: string;
  order_id: string | null;
  symbol: string;
  kind: FillKind;
  side: Side;
  qty: number;
  price: Decimal;
  gross_amount: Decimal;
  commission: Decimal;
  stamp_tax: Decimal;
  transfer_fee: Decimal;
  cash_delta: Decimal;
  trade_date: string;
  occurred_at: string;
  note: string;
}

export interface NavRow {
  trade_date: string;
  cash: Decimal;
  market_value: Decimal;
  total_assets: Decimal;
  nav: Decimal;
  day_pnl: Decimal;
  benchmark_nav: Decimal | null;
  finalized_at: string;
}

export interface RoundDetail {
  symbol: string;
  open_date: string;
  close_date: string;
  qty: number;
  cost: Decimal;
  proceeds: Decimal;
  pnl: Decimal;
  holding_days: number;
}

export interface Stats {
  rounds: number;
  win_rate: Decimal | null;
  profit_factor: Decimal | null;
  avg_holding_days: Decimal | null;
  max_drawdown: Decimal | null;
  cumulative_return: Decimal | null;
  annualized_return: Decimal | null;
  round_details: RoundDetail[];
}

export interface AccountEvent {
  event_id: string;
  account_id: string;
  seq: number;
  type: EventType;
  occurred_at: string;
  order_id: string | null;
  fill_seq: number | null;
  trade_date: string | null;
  symbol: string | null;
  basis: Record<string, unknown>;
  summary: string;
  data: Record<string, unknown>;
}

export interface SourceHealth {
  name: string;
  available?: boolean;
  consecutive_failures: number;
  last_ok_at?: string | null;
  last_error: string | null;
}

export interface MarketStatus {
  now: string;
  date: string;
  is_trading_day: boolean | null;
  session: string | null;
  next_open?: string;
  next_close?: string;
  calendar_available: boolean;
  calendar_error?: string;
  quote_source: SourceHealth;
  reference_source: SourceHealth;
  last_snapshot_at: string | null;
  last_settlement_date: string | null;
  unresolved_alerts: number;
}

export interface Snapshot {
  symbol: string;
  ts: string;
  last: Decimal;
  open: Decimal;
  prev_close: Decimal;
  halted: boolean;
  source: string;
}

export interface Instrument {
  symbol: string;
  name: string;
  board: string;
  exchange: string;
  list_date: string | null;
  is_st: boolean;
}

export interface InstrumentDetail extends Instrument {
  prev_close?: Decimal | null;
  limits_date?: string;
  up_limit?: Decimal | null;
  down_limit?: Decimal | null;
  suspended?: boolean;
  reference_error?: string;
  snapshot: Snapshot | null;
}

export interface Preview {
  ok: boolean;
  reason_code: string | null;
  reason: string | null;
  qty: number;
  trade_date: string;
  freeze_price: Decimal | null;
  frozen_cash: Decimal;
  frozen_qty: number;
  estimated_fees: Decimal;
  basis: Record<string, unknown>;
}

export interface Alert {
  id: number;
  level: string;
  code: string;
  message: string;
  trade_date: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface OrderInput {
  symbol: string;
  side: Side;
  order_type: OrderType;
  qty?: number;
  amount?: string;
  limit_price?: string;
  protect_price?: string;
  idempotency_key?: string;
  note?: string;
  tags?: string[];
}

export interface AccountInput {
  name?: string;
  note?: string;
  initial_cash?: string;
  commission_rate?: string;
  min_commission?: string;
  slippage_rate?: string;
  block_st?: boolean;
}
