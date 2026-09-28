// 账户概览数字（方案 3.4）。盘中数值为估算，定版以收盘结算为准。
import { Card, Col, Row, Statistic, Tooltip } from 'antd';
import type { Overview } from '../api/types';
import { fmtMoney, fmtNavReturn, pnlColor } from '../utils/format';

export function AccountSummary({ o }: { o: Overview }) {
  const items: { title: string; value: string; color?: string; tip?: string }[] = [
    { title: '总资产', value: fmtMoney(o.total_assets), tip: '现金 + 持仓市值（盘中按最新价估算）' },
    { title: '当日盈亏', value: fmtMoney(o.day_pnl), color: pnlColor(o.day_pnl), tip: '相对上一个定版总资产' },
    { title: '累计收益', value: fmtNavReturn(o.nav), color: pnlColor(Number(o.nav) - 1), tip: `净值 ${o.nav}` },
    { title: '现金', value: fmtMoney(o.cash) },
    { title: '可用现金', value: fmtMoney(o.available_cash) },
    { title: '冻结资金', value: fmtMoney(o.frozen_cash) },
    { title: '持仓市值', value: fmtMoney(o.market_value) },
  ];
  return (
    <Row gutter={[12, 12]}>
      {items.map((it) => (
        <Col key={it.title} flex="1 1 140px">
          <Card size="small">
            <Tooltip title={it.tip}>
              <Statistic title={it.title} value={it.value} valueStyle={{ fontSize: 20, color: it.color }} />
            </Tooltip>
          </Card>
        </Col>
      ))}
    </Row>
  );
}
