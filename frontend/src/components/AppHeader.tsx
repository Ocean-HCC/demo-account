// 页头：市场状态、数据源健康与告警入口。
import { BellOutlined } from '@ant-design/icons';
import { Badge, Button, Layout, Space, Tag, Tooltip, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMarketStatus } from '../api/queries';
import { SESSION_LABEL, fmtTime } from '../utils/format';
import { AlertsDrawer } from './AlertsDrawer';

export function AppHeader() {
  const { data: s } = useMarketStatus();
  const [open, setOpen] = useState(false);
  const quoteOk = s?.quote_source?.available !== false;
  return (
    <Layout.Header style={{ display: 'flex', alignItems: 'center', gap: 16, background: '#fff', borderBottom: '1px solid #f0f0f0' }}>
      <Link to="/" style={{ color: 'inherit' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          模拟券商柜台
        </Typography.Title>
      </Link>
      <Space size={8} wrap style={{ flex: 1 }}>
        {s && s.calendar_available === false && <Tag color="red">交易日历不可用</Tag>}
        {s?.is_trading_day === true && <Tag color="blue">交易日</Tag>}
        {s?.is_trading_day === false && <Tag>非交易日</Tag>}
        {s?.session && <Tag color={s.session === 'continuous' ? 'green' : 'default'}>{SESSION_LABEL[s.session] ?? s.session}</Tag>}
        {s?.next_open && (
          <Typography.Text type="secondary">下次开盘 {fmtTime(s.next_open).slice(0, 16)}</Typography.Text>
        )}
        <Tooltip
          title={
            s
              ? `行情源 ${s.quote_source.name}：${quoteOk ? '可用' : '不可用'}${s.quote_source.last_error ? `，最近错误：${s.quote_source.last_error}` : ''}；最近快照 ${fmtTime(s.last_snapshot_at)}`
              : undefined
          }
        >
          <Tag color={quoteOk ? 'success' : 'error'}>行情 {s?.quote_source.name ?? '-'}</Tag>
        </Tooltip>
        {s?.last_settlement_date && <Typography.Text type="secondary">最近结算 {s.last_settlement_date}</Typography.Text>}
      </Space>
      <Badge count={s?.unresolved_alerts ?? 0} size="small">
        <Button icon={<BellOutlined />} onClick={() => setOpen(true)}>
          告警
        </Button>
      </Badge>
      <AlertsDrawer open={open} onClose={() => setOpen(false)} />
    </Layout.Header>
  );
}
