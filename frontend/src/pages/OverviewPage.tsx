// 总览页（方案 3.6）：所有账户的卡片与多账户净值对比，可叠加基准指数。
import { PlusOutlined } from '@ant-design/icons';
import { useQueries } from '@tanstack/react-query';
import { Button, Card, Col, Empty, Flex, Row, Space, Statistic, Switch, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { endpoints } from '../api/endpoints';
import { useAccounts } from '../api/queries';
import type { NavRow } from '../api/types';
import { AccountForm } from '../components/AccountForm';
import { NavChart, type NavSeries } from '../components/NavChart';
import { ACCOUNT_STATUS, fmtMoney, fmtNavReturn, pnlColor } from '../utils/format';

export function OverviewPage() {
  const [showArchived, setShowArchived] = useState(false);
  const [withBenchmark, setWithBenchmark] = useState(true);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const { data: accounts, isLoading } = useAccounts(showArchived);
  const navs = useQueries({
    queries: (accounts ?? []).map((o) => ({
      queryKey: ['nav', o.account.id],
      queryFn: () => endpoints.nav(o.account.id),
    })),
  });
  const navData = navs.map((q) => q.data);

  const series = useMemo<NavSeries[]>(() => {
    const out: NavSeries[] = [];
    let bench: NavRow[] = [];
    const list = accounts ?? [];
    for (let i = 0; i < list.length; i++) {
      const rows = navData[i] ?? [];
      out.push({ name: list[i].account.name, points: rows.map((r) => [r.trade_date, Number(r.nav)]) });
      const withB = rows.filter((r) => r.benchmark_nav);
      if (withB.length > bench.length) bench = withB;
    }
    if (withBenchmark && bench.length) {
      out.push({ name: '沪深 300', dashed: true, points: bench.map((r) => [r.trade_date, Number(r.benchmark_nav)]) });
    }
    return out;
    // navData 每次渲染都是新数组，按内容比较即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accounts, JSON.stringify(navData.map((d) => d?.length ?? 0)), withBenchmark]);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          总览
        </Typography.Title>
        <Space wrap>
          <Space size={6}>
            <Switch size="small" checked={showArchived} onChange={setShowArchived} />
            <Typography.Text>显示已归档</Typography.Text>
          </Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
            新建账户
          </Button>
        </Space>
      </Flex>
      {!isLoading && !accounts?.length ? (
        <Card>
          <Empty description="还没有模拟账户">
            <Button type="primary" onClick={() => setCreating(true)}>
              新建第一个账户
            </Button>
          </Empty>
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {(accounts ?? []).map((o) => {
            const st = ACCOUNT_STATUS[o.account.status];
            return (
              <Col key={o.account.id} xs={24} sm={12} lg={8} xxl={6}>
                <Card
                  hoverable
                  loading={isLoading}
                  onClick={() => navigate(`/accounts/${o.account.id}`)}
                  title={
                    <Space>
                      {o.account.name}
                      <Tag color={st.color}>{st.text}</Tag>
                    </Space>
                  }
                  extra={<Typography.Text type="secondary">{o.positions_count} 只持仓</Typography.Text>}
                >
                  <Statistic title="总资产" value={fmtMoney(o.total_assets)} />
                  <Space size={32} style={{ marginTop: 8 }}>
                    <Statistic
                      title="当日盈亏"
                      value={fmtMoney(o.day_pnl)}
                      valueStyle={{ fontSize: 16, color: pnlColor(o.day_pnl) }}
                    />
                    <Statistic
                      title="累计收益"
                      value={fmtNavReturn(o.nav)}
                      valueStyle={{ fontSize: 16, color: pnlColor(Number(o.nav) - 1) }}
                    />
                  </Space>
                  {o.account.note && (
                    <Typography.Paragraph type="secondary" ellipsis={{ rows: 1 }} style={{ margin: '8px 0 0' }}>
                      {o.account.note}
                    </Typography.Paragraph>
                  )}
                </Card>
              </Col>
            );
          })}
        </Row>
      )}
      {!!accounts?.length && (
        <Card
          title="净值对比"
          extra={
            <Space size={6}>
              <Switch size="small" checked={withBenchmark} onChange={setWithBenchmark} />
              <Typography.Text>叠加沪深 300</Typography.Text>
            </Space>
          }
        >
          <NavChart series={series} height={360} />
        </Card>
      )}
      <AccountForm open={creating} onClose={() => setCreating(false)} onSaved={(a) => navigate(`/accounts/${a.id}`)} />
    </Space>
  );
}
