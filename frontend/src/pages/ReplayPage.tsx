// 复盘页（方案 3.6）：净值曲线标出每日买卖点，点击定位到当天的事件；时间线按标的、类型、时间筛选。
import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button, Card, DatePicker, Flex, Input, Select, Space, Tag, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAccount, useEvents, useFills, useNav } from '../api/queries';
import { EVENT_TYPES, type EventType } from '../api/types';
import { EventTimeline } from '../components/EventTimeline';
import { NavChart, type NavMarker, type NavSeries } from '../components/NavChart';
import { dayRange } from '../components/OrdersTable';
import { EVENT_LABEL } from '../utils/format';

export function ReplayPage() {
  const { id = '' } = useParams();
  const { data: o } = useAccount(id);
  const nav = useNav(id);
  const fills = useFills(id, { limit: 1000 });
  const [symbol, setSymbol] = useState('');
  const [types, setTypes] = useState<EventType[]>([]);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const window = day ? { from: `${day}T00:00:00+08:00`, to: `${day}T23:59:59+08:00` } : dayRange(range);
  const events = useEvents(id, { symbol: symbol.trim() || undefined, types, ...window, limit: 1000 });

  const series = useMemo<NavSeries[]>(() => {
    const rows = nav.data ?? [];
    const out: NavSeries[] = [{ name: '净值', points: rows.map((r) => [r.trade_date, Number(r.nav)]) }];
    const bench = rows.filter((r) => r.benchmark_nav);
    if (bench.length) out.push({ name: '沪深 300', dashed: true, points: bench.map((r) => [r.trade_date, Number(r.benchmark_nav)]) });
    return out;
  }, [nav.data]);

  const markers = useMemo<NavMarker[]>(() => {
    const agg = new Map<string, { buy: number; sell: number }>();
    for (const f of fills.data ?? []) {
      if (f.kind !== 'trade') continue;
      const e = agg.get(f.trade_date) ?? { buy: 0, sell: 0 };
      e[f.side] += 1;
      agg.set(f.trade_date, e);
    }
    return Array.from(agg.entries()).map(([date, e]) => ({
      date,
      side: e.buy && e.sell ? 'both' : e.buy ? 'buy' : 'sell',
      label: [e.buy ? `买入 ${e.buy} 笔` : '', e.sell ? `卖出 ${e.sell} 笔` : ''].filter(Boolean).join('，'),
    }));
  }, [fills.data]);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Flex align="center" gap={12} wrap>
        <Link to={`/accounts/${id}`}>
          <Button icon={<ArrowLeftOutlined />}>返回账户</Button>
        </Link>
        <Typography.Title level={3} style={{ margin: 0 }}>
          复盘：{o?.account.name ?? id}
        </Typography.Title>
      </Flex>
      <Card
        title="净值与买卖点"
        extra={<Typography.Text type="secondary">买卖点按成交日聚合，点击查看当天的事件</Typography.Text>}
      >
        <NavChart
          series={series}
          markers={markers}
          height={340}
          onMarkerClick={(d) => {
            setDay(d);
            setRange(null);
          }}
        />
      </Card>
      <Card
        title="事件时间线"
        extra={
          day && (
            <Tag color="blue" closable onClose={() => setDay(null)}>
              {day}
            </Tag>
          )
        }
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            allowClear
            placeholder="代码"
            style={{ width: 140 }}
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          />
          <Select<EventType[]>
            mode="multiple"
            allowClear
            placeholder="全部事件类型"
            style={{ minWidth: 280 }}
            value={types}
            onChange={setTypes}
            options={EVENT_TYPES.map((t) => ({ value: t, label: EVENT_LABEL[t].text }))}
          />
          <DatePicker.RangePicker
            value={range}
            onChange={(r) => {
              setRange(r as [Dayjs | null, Dayjs | null] | null);
              setDay(null);
            }}
          />
        </Space>
        <EventTimeline events={events.data ?? []} loading={events.isFetching} highlightDate={day} />
      </Card>
    </Space>
  );
}
