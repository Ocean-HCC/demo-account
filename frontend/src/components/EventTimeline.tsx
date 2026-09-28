// 事件时间线（方案 3.6 复盘页、5.6 账户事件序列）：每条可展开当时依据，订单事件显示备注与标签。
import { Collapse, Descriptions, Empty, List, Space, Tag, Typography } from 'antd';
import type { AccountEvent, Order } from '../api/types';
import { EVENT_LABEL, fmtTime } from '../utils/format';

const BASIS_LABEL: Record<string, string> = {
  checked_at: '校验时间',
  session: '时段',
  trade_date: '所属交易日',
  prev_close: '前收盘价',
  up_limit: '涨停价',
  down_limit: '跌停价',
  suspended: '停牌',
  is_st: '风险警示股',
  snapshot: '行情快照',
  freeze_price: '冻结价',
  available_cash: '可用现金',
  need: '需冻结',
  sellable_qty: '可卖数量',
  fill_price: '成交价',
  fees: '费用',
  commission: '佣金',
  stamp_tax: '印花税',
  transfer_fee: '过户费',
  reason_code: '原因码',
  reason: '原因',
  close: '收盘价',
  protect_price: '保护限价',
  limit_price: '限价',
  slippage_rate: '滑点',
  last: '最新价',
  open: '开盘价',
  ts: '快照时间',
  halted: '停牌',
  source: '来源',
  symbol: '代码',
  entitled_qty: '应得数量',
  record_date: '登记日',
  ex_date: '除权日',
  pay_date: '派息日',
  cash_per_share: '每股派现',
  bonus_per_share: '每股送股',
  transfer_per_share: '每股转股',
  factor: '因子',
  fraction_cash: '零股折现',
  benchmark_close: '基准收盘',
  benchmark_base: '基准基数',
  before: '变更前',
  after: '变更后',
  initial_cash: '初始资金',
  fee_params: '费用参数',
  block_st: '禁买风险警示股',
  cancelled_orders: '撤销订单数',
  commission_rate: '佣金率',
  min_commission: '最低佣金',
  cash: '现金',
  other_frozen: '其他冻结',
};

const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function renderScalar(v: unknown): React.ReactNode {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'string') return ISO_RE.test(v) ? fmtTime(v) : v;
  if (typeof v === 'number') return String(v);
  if (Array.isArray(v)) return v.map(String).join('，');
  return JSON.stringify(v);
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export function BasisView({ basis }: { basis: Record<string, unknown> }) {
  const entries = Object.entries(basis ?? {});
  if (!entries.length) return <Typography.Text type="secondary">无</Typography.Text>;
  const scalars = entries.filter(([, v]) => !isObject(v));
  const objects = entries.filter(([, v]) => isObject(v)) as [string, Record<string, unknown>][];
  const table = (items: [string, unknown][]) =>
    items.map(([k, v]) => ({ key: k, label: BASIS_LABEL[k] ?? k, children: renderScalar(v) }));
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      {scalars.length > 0 && (
        <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 3 }} labelStyle={{ whiteSpace: 'nowrap' }} items={table(scalars)} />
      )}
      {objects.map(([k, obj]) => (
        <Descriptions
          key={k}
          size="small"
          bordered
          title={<Typography.Text type="secondary">{BASIS_LABEL[k] ?? k}</Typography.Text>}
          column={{ xs: 1, sm: 2, lg: 3 }}
          labelStyle={{ whiteSpace: 'nowrap' }}
          items={table(Object.entries(obj))}
        />
      ))}
    </Space>
  );
}

interface Props {
  events: AccountEvent[];
  loading?: boolean;
  highlightDate?: string | null;
}

export function EventTimeline({ events, loading, highlightDate }: Props) {
  if (!loading && !events.length) return <Empty description="没有符合条件的事件" />;
  return (
    <List
      loading={loading}
      dataSource={events}
      renderItem={(e) => {
        const order = (e.data?.order ?? null) as Order | null;
        const label = EVENT_LABEL[e.type] ?? { text: e.type, color: 'default' };
        const highlighted = !!highlightDate && (e.trade_date === highlightDate || e.occurred_at.startsWith(highlightDate));
        return (
          <List.Item
            key={e.event_id}
            id={`ev-${e.seq}`}
            style={{ background: highlighted ? 'rgba(22,119,255,0.06)' : undefined, paddingInline: 8 }}
          >
            <Space direction="vertical" size={2} style={{ width: '100%' }}>
              <Space wrap size={8}>
                <Typography.Text type="secondary" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  #{e.seq} {fmtTime(e.occurred_at)}
                </Typography.Text>
                <Tag color={label.color}>{label.text}</Tag>
                {e.symbol && <Tag bordered={false}>{e.symbol}</Tag>}
                <Typography.Text>{e.summary}</Typography.Text>
              </Space>
              {order && (order.note || order.tags?.length) ? (
                <Space size={4} wrap>
                  {order.note && <Typography.Text type="secondary">备注：{order.note}</Typography.Text>}
                  {order.tags?.map((t) => (
                    <Tag key={t} color="blue" bordered={false}>
                      {t}
                    </Tag>
                  ))}
                </Space>
              ) : null}
              <Collapse
                size="small"
                ghost
                items={[{ key: 'basis', label: '当时依据', children: <BasisView basis={e.basis} /> }]}
              />
            </Space>
          </List.Item>
        );
      }}
    />
  );
}
