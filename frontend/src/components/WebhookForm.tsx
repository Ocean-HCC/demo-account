// 回调地址设置：成交、拒绝、失效、撤销与结算完成事件会推送到这里（方案 3.5）。
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Form, Input, Modal, Typography } from 'antd';
import { useEffect } from 'react';
import { endpoints } from '../api/endpoints';
import type { Account } from '../api/types';
import { errText } from '../utils/errors';

interface Props {
  open: boolean;
  account: Account;
  onClose: () => void;
}

export function WebhookForm({ open, account, onClose }: Props) {
  const [form] = Form.useForm<{ url?: string; secret?: string }>();
  const qc = useQueryClient();
  const { message } = App.useApp();
  useEffect(() => {
    if (open) form.setFieldsValue({ url: account.webhook_url ?? '', secret: '' });
  }, [open, account, form]);
  const mut = useMutation({
    mutationFn: (v: { url?: string; secret?: string }) =>
      endpoints.setWebhook(account.id, { url: v.url?.trim() || null, secret: v.secret?.trim() || null }),
    onSuccess: () => {
      message.success('回调设置已保存');
      void qc.invalidateQueries({ queryKey: ['account', account.id] });
      onClose();
    },
    onError: (e) => message.error(errText(e)),
  });
  return (
    <Modal open={open} title="回调设置" onCancel={onClose} onOk={() => form.submit()} confirmLoading={mut.isPending}>
      <Form form={form} layout="vertical" onFinish={(v) => mut.mutate(v)}>
        <Form.Item name="url" label="回调地址" extra="留空表示不推送，只保留事件供拉取">
          <Input placeholder="http://127.0.0.1:9000/hook" />
        </Form.Item>
        <Form.Item name="secret" label="签名密钥" extra="填写后请求头带 X-Demo-Account-Signature: sha256=<HMAC>">
          <Input.Password placeholder="留空则不签名" />
        </Form.Item>
      </Form>
      <Typography.Text type="secondary">投递至少一次，失败按 10 秒、30 秒、2 分钟、10 分钟、30 分钟重试。</Typography.Text>
    </Modal>
  );
}
