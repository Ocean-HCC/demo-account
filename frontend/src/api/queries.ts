// 查询钩子与查询键。SSE 到达时按账户使相关查询失效（见 hooks/useEventStream）。
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { Query } from './client';
import { endpoints } from './endpoints';

export const ACCOUNT_SCOPED_KEYS = ['account', 'positions', 'orders', 'fills', 'nav', 'stats', 'events'] as const;
const POLL = 15_000; // SSE 断开时的兜底刷新

export const useAccounts = (includeArchived: boolean) =>
  useQuery({
    queryKey: ['accounts', includeArchived],
    queryFn: () => endpoints.listAccounts(includeArchived),
    refetchInterval: POLL,
  });

export const useAccount = (id: string) =>
  useQuery({ queryKey: ['account', id], queryFn: () => endpoints.getAccount(id), refetchInterval: POLL });

export const usePositions = (id: string) =>
  useQuery({ queryKey: ['positions', id], queryFn: () => endpoints.positions(id), refetchInterval: POLL });

export const useOrders = (id: string, q: Query) =>
  useQuery({
    queryKey: ['orders', id, q],
    queryFn: () => endpoints.orders(id, q),
    placeholderData: keepPreviousData,
  });

export const useFills = (id: string, q: Query) =>
  useQuery({ queryKey: ['fills', id, q], queryFn: () => endpoints.fills(id, q), placeholderData: keepPreviousData });

export const useNav = (id: string) => useQuery({ queryKey: ['nav', id], queryFn: () => endpoints.nav(id) });

export const useStats = (id: string) => useQuery({ queryKey: ['stats', id], queryFn: () => endpoints.stats(id) });

export const useEvents = (id: string, q: Query) =>
  useQuery({
    queryKey: ['events', id, q],
    queryFn: () => endpoints.events(id, q),
    placeholderData: keepPreviousData,
  });

export const useMarketStatus = () =>
  useQuery({ queryKey: ['market-status'], queryFn: endpoints.marketStatus, refetchInterval: 30_000 });

export const useAlerts = () => useQuery({ queryKey: ['alerts'], queryFn: () => endpoints.alerts(false) });

export const useInstrument = (symbol: string | undefined) =>
  useQuery({
    queryKey: ['instrument', symbol],
    queryFn: () => endpoints.instrument(symbol as string),
    enabled: !!symbol,
    refetchInterval: 30_000,
  });
