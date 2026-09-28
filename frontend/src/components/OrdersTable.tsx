// 订单流水（方案 3.4、3.6）：按状态、标的、时间筛选；等待中的订单可撤销。
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Button, DatePicker, Input, Popconfirm, Select, Space, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';
import { endpoints } from '../api/endpoints';
import { ACCOUNT_SCOPED_KEYS, useOrders } from '../api/queries';
import type { Order, OrderStatus } from '../api/types';
import { errText } from '../utils/errors';
import { ORDER_STATUS, ORDER_TYPE_LABEL, SIDE_LABEL, fmtMoney, fmtTime } from '../utils/format';

export const dayRange = (r: [Dayjs | null, Dayjs | null] | null) => ({
  from: r?.[0] ? `${r[0].format('YYYY-MM-DD')}T00:00:00+08:00` : undefined,
  to: r?.[1] ? `${r[1].format('YYYY-MM-DD')}T23:59:59+08:00` : undefined,
});

export function OrdersTable({ accountId }: { accountId: string }) {
  const [status, setStatus] = useState<OrderStatus | undefined>();
  const [symbol, setSymbol] = useState('');
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const { data, isFetching } = useOrders(accountId, { status, symbol: symbol.trim() || undefined, ...dayRange(range), limit: 500 });
  const qc = useQueryClient();
  const { message } = App.useApp();
  const cancel = useMutation({
    mutationFn: (o: Order) => endpoints.cancelOrder(accountId, o.id),
    onSuccess: () => {
      message.success('已撤单');
      for (const k of ACCOUNT_SCOPED_KEYS) void qc.invalidateQueries({ queryKey: [k, accountId] });
    },
    onError: (e) => message.error(errText(e)),
  });
  const columns: ColumnsType<Order> = [
    { title: '提交时间', dataIndex: 'created_at', width: 170, render: fmtTime },
    { title: '代码', dataIndex: 'symbol', width: 110 },
    {
      title: '方向',
      dataIndex: 'side',
      width: 64,
      render: (v: Order['side']) => <Tag color={v === 'buy' ? 'red' : 'green'}>{SIDE_LABEL[v]}</Tag>,
    },
    { title: '类型', dataIndex: 'order_type', width: 90, render: (v: Order['order_type']) => ORDER_TYPE_LABEL[v] },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (v: OrderStatus, o) => (
        <Tooltip title={o.reason ? `${o.reason_code}：${o.reason}` : undefined}>
          <Tag color={ORDER_STATUS[v].color}>{ORDER_STATUS[v].text}</Tag>
        </Tooltip>
      ),
    },
    {
      title: '数量',
      dataIndex: 'qty',
      align: 'right',
      render: (v: number, o) => (o.amount ? <Tooltip title={`按金额 ${fmtMoney(o.amount)} 元折算`}>{v}</Tooltip> : v),
    },
    {
      title: '限价',
      key: 'price',
      align: 'right',
      render: (_: unknown, o) => o.limit_price ?? (o.protect_price ? `保护 ${o.protect_price}` : '-'),
    },
    {
      title: '所属交易日',
      dataIndex: 'trade_date',
      width: 110,
      render: (v: string, o) => (o.defer_count ? <Tooltip title={`已顺延 ${o.defer_count} 次`}>{v}*</Tooltip> : v),
    },
    { title: '冻结资金', dataIndex: 'frozen_cash', align: 'right', render: (v: string) => (Number(v) ? fmtMoney(v) : '-') },
    {
      title: '备注与标签',
      key: 'note',
      render: (_: unknown, o) => (
        <Space size={4} wrap>
          {o.note && <span>{o.note}</span>}
          {o.tags.map((t) => (
            <Tag key={t} bordered={false}>
              {t}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'op',
      fixed: 'right',
      width: 72,
      render: (_: unknown, o) =>
        o.status === 'pending' ? (
          <Popconfirm title="撤销这笔订单？" onConfirm={() => cancel.mutate(o)}>
            <Button size="small" type="link">
              撤单
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space wrap>
        <Select
          allowClear
          placeholder="全部状态"
          style={{ width: 120 }}
          value={status}
          onChange={setStatus}
          options={Object.entries(ORDER_STATUS).map(([value, s]) => ({ value, label: s.text }))}
        />
        <Input allowClear placeholder="代码" style={{ width: 140 }} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        <DatePicker.RangePicker value={range} onChange={(r) => setRange(r as [Dayjs | null, Dayjs | null] | null)} />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isFetching}
        dataSource={data ?? []}
        columns={columns}
        scroll={{ x: 1200 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: '暂无订单' }}
      />
    </Space>
  );
}
