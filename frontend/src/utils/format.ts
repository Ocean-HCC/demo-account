// 展示格式与中文标签。金额计算不在前端做，这里只做显示换算。
import dayjs from 'dayjs';
import type { AccountStatus, EventType, FillKind, OrderStatus, OrderType, Side } from '../api/types';

type Num = string | number | null | undefined;

const toNum = (v: Num): number | null => {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export const fmtMoney = (v: Num, digits = 2): string => {
  const n = toNum(v);
  return n === null ? '-' : n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

export const fmtSigned = (v: Num, digits = 2): string => {
  const n = toNum(v);
  if (n === null) return '-';
  return `${n > 0 ? '+' : ''}${fmtMoney(n, digits)}`;
};

export const fmtPct = (v: Num, digits = 2): string => {
  const n = toNum(v);
  return n === null ? '-' : `${(n * 100).toFixed(digits)}%`;
};

export const fmtNavReturn = (nav: Num): string => {
  const n = toNum(nav);
  return n === null ? '-' : `${n >= 1 ? '+' : ''}${((n - 1) * 100).toFixed(2)}%`;
};

export const fmtNum = (v: Num, digits = 4): string => {
  const n = toNum(v);
  return n === null ? '-' : n.toFixed(digits);
};

export const fmtTime = (iso: string | null | undefined): string => (iso ? dayjs(iso).format('YYYY-MM-DD HH:mm:ss') : '-');
export const fmtClock = (iso: string | null | undefined): string => (iso ? dayjs(iso).format('HH:mm:ss') : '-');

// A 股习惯：涨红跌绿
export const UP = '#cf1322';
export const DOWN = '#389e0d';
export const pnlColor = (v: Num): string | undefined => {
  const n = toNum(v);
  if (n === null || n === 0) return undefined;
  return n > 0 ? UP : DOWN;
};

// 万分之 ↔ 小数
export const toWan = (rate: string): number => Number((Number(rate) * 10000).toPrecision(10));
export const fromWan = (wan: number): string => String(Number((wan / 10000).toPrecision(10)));

export const SIDE_LABEL: Record<Side, string> = { buy: '买入', sell: '卖出' };
export const ORDER_TYPE_LABEL: Record<OrderType, string> = {
  market: '即时市价',
  limit: '限价',
  open: '开盘单',
  close: '收盘单',
};
export const ORDER_STATUS: Record<OrderStatus, { text: string; color: string }> = {
  pending: { text: '等待中', color: 'processing' },
  filled: { text: '已成交', color: 'success' },
  rejected: { text: '已拒绝', color: 'error' },
  cancelled: { text: '已撤销', color: 'default' },
  expired: { text: '已失效', color: 'warning' },
};
export const ACCOUNT_STATUS: Record<AccountStatus, { text: string; color: string }> = {
  active: { text: '正常', color: 'success' },
  frozen: { text: '冻结', color: 'warning' },
  archived: { text: '归档', color: 'default' },
};
export const FILL_KIND_LABEL: Record<FillKind, string> = {
  trade: '交易',
  corporate_action: '公司行动',
  reversal: '冲正',
};
export const EVENT_LABEL: Record<EventType, { text: string; color: string }> = {
  order_submitted: { text: '订单提交', color: 'blue' },
  order_rejected: { text: '订单拒绝', color: 'red' },
  order_filled: { text: '订单成交', color: 'green' },
  order_cancelled: { text: '订单撤销', color: 'default' },
  order_expired: { text: '订单失效', color: 'orange' },
  settlement_done: { text: '结算完成', color: 'purple' },
  corporate_action: { text: '公司行动', color: 'gold' },
  reversal: { text: '冲正', color: 'magenta' },
  account_created: { text: '开户', color: 'cyan' },
  account_frozen: { text: '冻结', color: 'orange' },
  account_unfrozen: { text: '恢复', color: 'cyan' },
  account_archived: { text: '归档', color: 'default' },
  fee_params_changed: { text: '参数变更', color: 'geekblue' },
};
export const SESSION_LABEL: Record<string, string> = {
  pre_open: '开盘前',
  opening_auction: '开盘集合竞价',
  continuous: '连续竞价',
  lunch: '午间休市',
  closing_auction: '收盘集合竞价',
  after_hours: '盘后固定价格交易',
  closed: '休市',
};
export const PRICE_SOURCE_LABEL: Record<string, string> = {
  snapshot: '实时',
  close: '收盘价',
  prev_close: '前收',
};
