// 持仓明细（方案 3.4、3.6）：行内可直接卖出。
import { Button, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { usePositions } from '../api/queries';
import type { Position } from '../api/types';
import { PRICE_SOURCE_LABEL, fmtMoney, fmtPct, pnlColor } from '../utils/format';

interface Props {
  accountId: string;
  canTrade: boolean;
  onSell?: (p: Position) => void;
}

export function PositionsTable({ accountId, canTrade, onSell }: Props) {
  const { data, isLoading } = usePositions(accountId);
  const columns: ColumnsType<Position> = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left', width: 110 },
    { title: '名称', dataIndex: 'name', width: 120 },
    { title: '数量', dataIndex: 'qty', align: 'right' },
    {
      title: '可卖',
      dataIndex: 'sellable_qty',
      align: 'right',
      render: (v: number, p) => (
        <Tooltip title={p.today_bought_qty ? `今日买入 ${p.today_bought_qty} 股，次日可卖` : undefined}>{v}</Tooltip>
      ),
    },
    { title: '成本价', dataIndex: 'avg_cost', align: 'right', render: (v: string | null) => v ?? '-' },
    {
      title: '最新价',
      dataIndex: 'last_price',
      align: 'right',
      render: (v: string | null, p) => (
        <span>
          {v ?? '-'}
          {p.price_source !== 'snapshot' && (
            <Tag style={{ marginLeft: 6 }} bordered={false}>
              {PRICE_SOURCE_LABEL[p.price_source] ?? p.price_source}
            </Tag>
          )}
        </span>
      ),
    },
    { title: '市值', dataIndex: 'market_value', align: 'right', render: (v: string) => fmtMoney(v) },
    {
      title: '浮动盈亏',
      dataIndex: 'unrealized_pnl',
      align: 'right',
      render: (v: string) => <span style={{ color: pnlColor(v) }}>{fmtMoney(v)}</span>,
    },
    { title: '占比', dataIndex: 'weight', align: 'right', render: (v: string | null) => fmtPct(v) },
    {
      title: '操作',
      key: 'op',
      fixed: 'right',
      width: 80,
      render: (_: unknown, p) => (
        <Button size="small" danger disabled={!canTrade || p.sellable_qty <= 0} onClick={() => onSell?.(p)}>
          卖出
        </Button>
      ),
    },
  ];
  return (
    <Table
      rowKey="symbol"
      size="small"
      loading={isLoading}
      dataSource={data ?? []}
      columns={columns}
      pagination={false}
      scroll={{ x: 1000 }}
      locale={{ emptyText: '暂无持仓' }}
    />
  );
}
