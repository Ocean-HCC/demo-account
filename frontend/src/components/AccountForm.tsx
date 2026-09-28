// 新建与编辑账户（方案 3.1、3.6 账户管理）。佣金率与滑点按万分之填写。
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Col, Form, Input, InputNumber, Modal, Row, Switch, Typography } from 'antd';
import { useEffect } from 'react';
import { endpoints } from '../api/endpoints';
import type { Account, AccountInput } from '../api/types';
import { errText } from '../utils/errors';
import { fromWan, toWan } from '../utils/format';

interface FormValues {
  name: string;
  note?: string;
  initial_cash?: number;
  commission_wan: number;
  min_commission: number;
  slippage_wan: number;
  block_st: boolean;
}

interface Props {
  open: boolean;
  account?: Account;
  onClose: () => void;
  onSaved?: (a: Account) => void;
}

export function AccountForm({ open, account, onClose, onSaved }: Props) {
  const [form] = Form.useForm<FormValues>();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const editing = !!account;

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue(
      account
        ? {
            name: account.name,
            note: account.note,
            commission_wan: toWan(account.commission_rate),
            min_commission: Number(account.min_commission),
            slippage_wan: toWan(account.slippage_rate),
            block_st: account.block_st,
          }
        : { name: '', note: '', initial_cash: 1_000_000, commission_wan: 1.3, min_commission: 5, slippage_wan: 5, block_st: true },
    );
  }, [open, account, form]);

  const mut = useMutation({
    mutationFn: (v: FormValues) => {
      const body: AccountInput = {
        name: v.name,
        note: v.note ?? '',
        commission_rate: fromWan(v.commission_wan),
        min_commission: String(v.min_commission),
        slippage_rate: fromWan(v.slippage_wan),
        block_st: v.block_st,
      };
      if (account) return endpoints.updateAccount(account.id, body);
      return endpoints.createAccount({ ...body, initial_cash: String(v.initial_cash) });
    },
    onSuccess: (a) => {
      message.success(editing ? '已保存' : `已开户：${a.name}`);
      void qc.invalidateQueries({ queryKey: ['accounts'] });
      void qc.invalidateQueries({ queryKey: ['account', a.id] });
      void qc.invalidateQueries({ queryKey: ['events', a.id] });
      onSaved?.(a);
      onClose();
    },
    onError: (e) => message.error(errText(e)),
  });

  return (
    <Modal
      open={open}
      title={editing ? '编辑账户' : '新建账户'}
      okText={editing ? '保存' : '开户'}
      onCancel={onClose}
      onOk={() => form.submit()}
      confirmLoading={mut.isPending}
    >
      <Form form={form} layout="vertical" onFinish={(v) => mut.mutate(v)}>
        <Form.Item name="name" label="名称" rules={[{ required: true, whitespace: true, message: '请填写名称' }]}>
          <Input maxLength={100} placeholder="例如：均线策略 A" />
        </Form.Item>
        <Form.Item name="note" label="备注">
          <Input.TextArea rows={2} maxLength={1000} placeholder="属于哪个策略、参数是什么" />
        </Form.Item>
        {!editing && (
          <Form.Item
            name="initial_cash"
            label="初始资金（元）"
            rules={[{ required: true, message: '请填写初始资金' }]}
            extra="资金只在开户时一次注入，之后不能追加或提取"
          >
            <InputNumber min={10000} step={100000} style={{ width: '100%' }} />
          </Form.Item>
        )}
        <Row gutter={12}>
          <Col span={8}>
            <Form.Item name="commission_wan" label="佣金率（万分之）">
              <InputNumber min={0} max={30} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="min_commission" label="最低佣金（元）">
              <InputNumber min={0} step={1} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="slippage_wan" label="滑点（万分之）">
              <InputNumber min={0} max={500} step={1} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="block_st" label="禁止买入风险警示股" valuePropName="checked">
          <Switch />
        </Form.Item>
        {editing && (
          <Typography.Text type="secondary">费用参数修改只影响之后的成交，并会记入事件序列。</Typography.Text>
        )}
      </Form>
    </Modal>
  );
}
