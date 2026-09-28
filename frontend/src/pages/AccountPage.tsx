// 账户页（方案 3.6）：概览、持仓（行内卖出）、订单与成交、净值与基准、统计，右侧下单面板；账户管理入口。
import { HistoryOutlined } from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Card, Col, Flex, Popconfirm, Result, Row, Space, Spin, Tabs, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import { ACCOUNT_SCOPED_KEYS, useAccount, useNav } from '../api/queries';
import { AccountForm } from '../components/AccountForm';
import { AccountSummary } from '../components/AccountSummary';
import { FillsTable } from '../components/FillsTable';
import { NavChart, type NavSeries } from '../components/NavChart';
import { OrderForm, type OrderPreset } from '../components/OrderForm';
import { OrdersTable } from '../components/OrdersTable';
import { PositionsTable } from '../components/PositionsTable';
import { StatsPanel } from '../components/StatsPanel';
import { WebhookForm } from '../components/WebhookForm';
import { errText } from '../utils/errors';
import { ACCOUNT_STATUS, fmtMoney, fmtTime, toWan } from '../utils/format';

type Action = 'freeze' | 'unfreeze' | 'archive';

export function AccountPage() {
  const { id = '' } = useParams();
  const { data: o, isLoading, error } = useAccount(id);
  const nav = useNav(id);
  const [editing, setEditing] = useState(false);
  const [webhook, setWebhook] = useState(false);
  const [preset, setPreset] = useState<OrderPreset | null>(null);
  const [tab, setTab] = useState('positions');
  const qc = useQueryClient();
  const { message } = App.useApp();
  const act = useMutation({
    mutationFn: (kind: Action) => endpoints[kind](id),
    onSuccess: (a) => {
      message.success(`账户已${ACCOUNT_STATUS[a.status].text}`);
      for (const k of ACCOUNT_SCOPED_KEYS) void qc.invalidateQueries({ queryKey: [k, id] });
      void qc.invalidateQueries({ queryKey: ['accounts'] });
    },
    onError: (e) => message.error(errText(e)),
  });
  const series = useMemo<NavSeries[]>(() => {
    const rows = nav.data ?? [];
    const out: NavSeries[] = [{ name: '净值', points: rows.map((r) => [r.trade_date, Number(r.nav)]) }];
    const bench = rows.filter((r) => r.benchmark_nav);
    if (bench.length) out.push({ name: '沪深 300', dashed: true, points: bench.map((r) => [r.trade_date, Number(r.benchmark_nav)]) });
    return out;
  }, [nav.data]);

  if (isLoading) return <Spin style={{ display: 'block', margin: '80px auto' }} />;
  if (error || !o) {
    return <Result status="404" title="账户不存在" subTitle={errText(error)} extra={<Link to="/">返回总览</Link>} />;
  }
  const a = o.account;
  const st = ACCOUNT_STATUS[a.status];
  const canTrade = a.status === 'active';
  const readOnly = a.status === 'archived';

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Flex justify="space-between" align="flex-start" wrap gap={12}>
        <Space direction="vertical" size={2}>
          <Space wrap>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {a.name}
            </Typography.Title>
            <Tag color={st.color}>{st.text}</Tag>
            {a.webhook_configured && <Tag>已设回调</Tag>}
          </Space>
          <Typography.Text type="secondary">
            {a.id} · 开户于 {fmtTime(a.created_at).slice(0, 10)} · 初始资金 {fmtMoney(a.initial_cash)} · 佣金万分之{' '}
            {toWan(a.commission_rate)}（最低 {a.min_commission} 元）· 滑点万分之 {toWan(a.slippage_rate)}
            {a.block_st ? ' · 禁买风险警示股' : ''}
          </Typography.Text>
          {a.note && <Typography.Text>{a.note}</Typography.Text>}
        </Space>
        <Space wrap>
          <Link to={`/accounts/${id}/replay`}>
            <Button icon={<HistoryOutlined />}>复盘</Button>
          </Link>
          <Button disabled={readOnly} onClick={() => setEditing(true)}>
            编辑
          </Button>
          <Button disabled={readOnly} onClick={() => setWebhook(true)}>
            回调
          </Button>
          {a.status === 'active' && (
            <Popconfirm title="冻结后撤销全部等待中订单并拒绝新订单，确定？" onConfirm={() => act.mutate('freeze')}>
              <Button loading={act.isPending}>冻结</Button>
            </Popconfirm>
          )}
          {a.status === 'frozen' && (
            <Button loading={act.isPending} onClick={() => act.mutate('unfreeze')}>
              恢复
            </Button>
          )}
          {!readOnly && (
            <Popconfirm title="归档不可逆，账户将永久只读，确定？" onConfirm={() => act.mutate('archive')}>
              <Button danger>归档</Button>
            </Popconfirm>
          )}
        </Space>
      </Flex>
      {a.status !== 'active' && (
        <Alert
          type="warning"
          showIcon
          message={a.status === 'frozen' ? '账户已冻结：拒绝一切新订单，持仓和记录只读，可恢复' : '账户已归档：永久只读'}
        />
      )}
      <AccountSummary o={o} />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card>
            <Tabs
              activeKey={tab}
              onChange={setTab}
              items={[
                {
                  key: 'positions',
                  label: '持仓',
                  children: (
                    <PositionsTable
                      accountId={id}
                      canTrade={canTrade}
                      onSell={(p) =>
                        setPreset({
                          key: Date.now(),
                          symbol: p.symbol,
                          side: 'sell',
                          order_type: 'limit',
                          qty: p.sellable_qty,
                          limit_price: p.last_price ? Number(p.last_price) : undefined,
                        })
                      }
                    />
                  ),
                },
                { key: 'orders', label: '订单', children: <OrdersTable accountId={id} /> },
                { key: 'fills', label: '成交', children: <FillsTable accountId={id} /> },
                { key: 'nav', label: '净值', children: <NavChart series={series} /> },
                { key: 'stats', label: '统计', children: <StatsPanel accountId={id} /> },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="下单">
            {canTrade ? (
              <OrderForm accountId={id} preset={preset} />
            ) : (
              <Typography.Text type="secondary">账户当前不可下单</Typography.Text>
            )}
          </Card>
        </Col>
      </Row>
      <AccountForm open={editing} account={a} onClose={() => setEditing(false)} />
      <WebhookForm open={webhook} account={a} onClose={() => setWebhook(false)} />
    </Space>
  );
}
