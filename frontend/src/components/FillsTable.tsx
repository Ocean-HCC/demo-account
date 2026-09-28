// 成交台账（方案 3.4、5.3）：交易、公司行动与冲正按序号倒序。
import { DatePicker, Input, Space, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import { useState } from 'react';
import { useFills } from '../api/queries';
import type { Fill } from '../api/types';
import { FILL_KIND_LABEL, SIDE_LABEL, fmtMoney, fmtTime, pnlColor } from '../utils/format';
import { dayRange } from './OrdersTable';

export function FillsTable({ accountId }: { accountId: string }) {
  const [symbol, setSymbol] = useState('');
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const { data, isFetching } = useFills(accountId, { symbol: symbol.trim() || undefined, ...dayRange(range), limit: 1000 });
  const columns: ColumnsType<Fill> = [
    { title: '序号', dataIndex: 'seq', width: 70 },
    { title: '时间', dataIndex: 'occurred_at', width: 170, render: fmtTime },
    { title: '代码', dataIndex: 'symbol', width: 110 },
    { title: '类型', dataIndex: 'kind', width: 90, render: (v: Fill['kind']) => FILL_KIND_LABEL[v] },
    {
      title: '方向',
      dataIndex: 'side',
      width: 64,
      render: (v: Fill['side'], f) =>
        f.kind === 'trade' ? <Tag color={v === 'buy' ? 'red' : 'green'}>{SIDE_LABEL[v]}</Tag> : f.qty ? (v === 'buy' ? '增加' : '减少') : '-',
    },
    { title: '数量', dataIndex: 'qty', align: 'right' },
    { title: '价格', dataIndex: 'price', align: 'right', render: (v: string, f) => (f.kind === 'trade' ? v : '-') },
    { title: '成交金额', dataIndex: 'gross_amount', align: 'right', render: (v: string) => fmtMoney(v) },
    { title: '佣金', dataIndex: 'commission', align: 'right', render: (v: string) => fmtMoney(v) },
    { title: '印花税', dataIndex: 'stamp_tax', align: 'right', render: (v: string) => fmtMoney(v) },
    { title: '过户费', dataIndex: 'transfer_fee', align: 'right', render: (v: string) => fmtMoney(v) },
    {
      title: '现金变动',
      dataIndex: 'cash_delta',
      align: 'right',
      render: (v: string) => <span style={{ color: pnlColor(v) }}>{fmtMoney(v)}</span>,
    },
    { title: '备注', dataIndex: 'note', ellipsis: true },
  ];
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space wrap>
        <Input allowClear placeholder="代码" style={{ width: 140 }} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        <DatePicker.RangePicker value={range} onChange={(r) => setRange(r as [Dayjs | null, Dayjs | null] | null)} />
      </Space>
      <Table
        rowKey="seq"
        size="small"
        loading={isFetching}
        dataSource={data ?? []}
        columns={columns}
        scroll={{ x: 1300 }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: '暂无成交' }}
      />
    </Space>
  );
}
