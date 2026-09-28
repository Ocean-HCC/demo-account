// 告警（方案 8.3、8.4）：结算暂停、数据源不可用、台账不一致等在控制台显著提示。
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Button, Drawer, Empty, List, Space, Tag, Typography } from 'antd';
import { endpoints } from '../api/endpoints';
import { useAlerts } from '../api/queries';
import { errText } from '../utils/errors';
import { fmtTime } from '../utils/format';

const LEVEL_COLOR: Record<string, string> = { error: 'red', warning: 'orange', info: 'blue' };

export function AlertsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading } = useAlerts();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const resolve = useMutation({
    mutationFn: (id: number) => endpoints.resolveAlert(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['alerts'] });
      void qc.invalidateQueries({ queryKey: ['market-status'] });
    },
    onError: (e) => message.error(errText(e)),
  });
  return (
    <Drawer open={open} onClose={onClose} title="未解决的告警" width={520}>
      {!isLoading && !data?.length ? (
        <Empty description="没有未解决的告警" />
      ) : (
        <List
          loading={isLoading}
          dataSource={data ?? []}
          renderItem={(a) => (
            <List.Item
              key={a.id}
              actions={[
                <Button key="r" size="small" onClick={() => resolve.mutate(a.id)}>
                  标记已解决
                </Button>,
              ]}
            >
              <Space direction="vertical" size={2}>
                <Space size={6}>
                  <Tag color={LEVEL_COLOR[a.level] ?? 'default'}>{a.level}</Tag>
                  <Typography.Text strong>{a.code}</Typography.Text>
                  <Typography.Text type="secondary">{fmtTime(a.created_at)}</Typography.Text>
                </Space>
                <Typography.Text>{a.message}</Typography.Text>
              </Space>
            </List.Item>
          )}
        />
      )}
    </Drawer>
  );
}
