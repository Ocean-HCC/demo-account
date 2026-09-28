// 回合统计（方案 6.9）：只用定版净值与已完成回合。
import { Card, Col, Row, Statistic, Table, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useStats } from '../api/queries';
import type { RoundDetail } from '../api/types';
import { fmtMoney, fmtNum, fmtPct, pnlColor } from '../utils/format';

export function StatsPanel({ accountId }: { accountId: string }) {
  const { data, isLoading } = useStats(accountId);
  const s = data;
  const cards = [
    { title: '回合数', value: s ? String(s.rounds) : '-' },
    { title: '胜率', value: fmtPct(s?.win_rate) },
    { title: '盈亏比', value: fmtNum(s?.profit_factor, 2) },
    { title: '平均持有天数', value: fmtNum(s?.avg_holding_days, 1) },
    { title: '最大回撤', value: fmtPct(s?.max_drawdown) },
    { title: '累计收益', value: fmtPct(s?.cumulative_return), color: pnlColor(s?.cumulative_return) },
    {
      title: '年化收益',
      value: s?.annualized_return == null ? '账龄不足 30 天' : fmtPct(s.annualized_return),
      color: pnlColor(s?.annualized_return),
    },
  ];
  const columns: ColumnsType<RoundDetail> = [
    { title: '代码', dataIndex: 'symbol' },
    { title: '开仓日', dataIndex: 'open_date' },
    { title: '平仓日', dataIndex: 'close_date' },
    { title: '数量', dataIndex: 'qty', align: 'right' },
    { title: '买入成本', dataIndex: 'cost', align: 'right', render: (v: string) => fmtMoney(v) },
    { title: '卖出净额', dataIndex: 'proceeds', align: 'right', render: (v: string) => fmtMoney(v) },
    { title: '收益', dataIndex: 'pnl', align: 'right', render: (v: string) => <span style={{ color: pnlColor(v) }}>{fmtMoney(v)}</span> },
    { title: '持有交易日', dataIndex: 'holding_days', align: 'right' },
  ];
  return (
    <>
      <Row gutter={[12, 12]}>
        {cards.map((c) => (
          <Col key={c.title} flex="1 1 140px">
            <Card size="small" loading={isLoading}>
              <Statistic title={c.title} value={c.value} valueStyle={{ fontSize: 18, color: c.color }} />
            </Card>
          </Col>
        ))}
      </Row>
      <Typography.Paragraph type="secondary" style={{ margin: '12px 0 8px' }}>
        回合按先进先出配对；回撤按每个交易日收盘后定版的净值计算，盘中估算值不参与。
      </Typography.Paragraph>
      <Table
        rowKey={(r) => `${r.symbol}-${r.open_date}-${r.close_date}-${r.qty}-${r.cost}`}
        size="small"
        dataSource={s?.round_details ?? []}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: '暂无已完成回合' }}
      />
    </>
  );
}
