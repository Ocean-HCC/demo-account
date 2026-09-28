// 接口清单（实现 6.2）。控制台只通过这里访问后端。
import { api, withQuery, type Query } from './client';
import type {
  Account,
  AccountEvent,
  AccountInput,
  Alert,
  Fill,
  Instrument,
  InstrumentDetail,
  MarketStatus,
  NavRow,
  Order,
  OrderInput,
  Overview,
  Position,
  Preview,
  Snapshot,
  Stats,
} from './types';

const acc = (id: string) => `/api/accounts/${encodeURIComponent(id)}`;

export const endpoints = {
  listAccounts: (includeArchived = false) =>
    api<Overview[]>(withQuery('/api/accounts', { include_archived: includeArchived })),
  getAccount: (id: string) => api<Overview>(acc(id)),
  createAccount: (body: AccountInput) => api<Account>('/api/accounts', { method: 'POST', body }),
  updateAccount: (id: string, body: AccountInput) => api<Account>(acc(id), { method: 'PATCH', body }),
  freeze: (id: string) => api<Account>(`${acc(id)}/freeze`, { method: 'POST' }),
  unfreeze: (id: string) => api<Account>(`${acc(id)}/unfreeze`, { method: 'POST' }),
  archive: (id: string) => api<Account>(`${acc(id)}/archive`, { method: 'POST' }),
  setWebhook: (id: string, body: { url: string | null; secret: string | null }) =>
    api<Account>(`${acc(id)}/webhook`, { method: 'PUT', body }),
  positions: (id: string) => api<Position[]>(`${acc(id)}/positions`),
  orders: (id: string, q?: Query) => api<Order[]>(withQuery(`${acc(id)}/orders`, q)),
  submitOrder: (id: string, body: OrderInput) => api<Order>(`${acc(id)}/orders`, { method: 'POST', body }),
  previewOrder: (id: string, body: OrderInput) =>
    api<Preview>(`${acc(id)}/orders/preview`, { method: 'POST', body }),
  cancelOrder: (id: string, orderId: string) =>
    api<Order>(`${acc(id)}/orders/${encodeURIComponent(orderId)}`, { method: 'DELETE' }),
  fills: (id: string, q?: Query) => api<Fill[]>(withQuery(`${acc(id)}/fills`, q)),
  nav: (id: string) => api<NavRow[]>(`${acc(id)}/nav`),
  stats: (id: string) => api<Stats>(`${acc(id)}/stats`),
  events: (id: string, q?: Query) => api<AccountEvent[]>(withQuery(`${acc(id)}/events`, q)),
  marketStatus: () => api<MarketStatus>('/api/market/status'),
  searchInstruments: (q: string) => api<Instrument[]>(withQuery('/api/market/instruments', { q, limit: 20 })),
  instrument: (symbol: string) => api<InstrumentDetail>(`/api/market/instruments/${encodeURIComponent(symbol)}`),
  quotes: (symbols: string[]) => api<Record<string, Snapshot>>(withQuery('/api/market/quotes', { symbols })),
  alerts: (includeResolved = false) =>
    api<Alert[]>(withQuery('/api/admin/alerts', { include_resolved: includeResolved })),
  resolveAlert: (alertId: number) => api<{ id: number }>(`/api/admin/alerts/${alertId}/resolve`, { method: 'POST' }),
};
