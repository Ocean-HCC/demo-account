// 下单面板（方案 3.2、3.6）：选标的、类型、方向、数量或金额、限价或保护限价，填备注和标签；
// 提交前调用预估接口显示冻结金额、预估费用与可能的拒绝原因。
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ApiError } from '../api/client';
import { endpoints } from '../api/endpoints';
import { ACCOUNT_SCOPED_KEYS, useInstrument } from '../api/queries';
import type { Order, OrderInput, OrderType, Preview, Side } from '../api/types';
import { useDebouncedCallback } from '../hooks/useDebouncedCallback';
import { errText } from '../utils/errors';
import { ORDER_TYPE_LABEL, SIDE_LABEL, fmtClock, fmtMoney } from '../utils/format';

const SYMBOL_RE = /^\d{6}\.(SH|SZ)$/;

const TYPE_HELP: Record<OrderType, string> = {
  market: '只在连续竞价时段提交，按下一笔有效行情的最新价加不利滑点成交，180 秒内无行情则失效',
  limit: '随时提交，所属交易日连续竞价时段内触及限价即按限价成交，收盘未成交失效',
  open: '在最近一次开盘按开盘价加滑点成交，9:25 前提交算当日；停牌顺延，最多 3 个交易日',
  close: '在最近一次收盘按收盘价成交（盘后固定价格交易），15:30 前提交算当日；可设保护限价',
};

export interface OrderPreset {
  key: number;
  symbol?: string;
  side?: Side;
  order_type?: OrderType;
  qty?: number;
  limit_price?: number;
}

interface FormValues {
  symbol?: string;
  side: Side;
  order_type: OrderType;
  by: 'qty' | 'amount';
  qty?: number;
  amount?: number;
  limit_price?: number;
  protect_price?: number;
  note?: string;
  tags?: string[];
}

const DEFAULTS: FormValues = { side: 'buy', order_type: 'limit', by: 'qty', note: '', tags: [] };

function toBody(v: FormValues | undefined): OrderInput | null {
  if (!v?.symbol || !SYMBOL_RE.test(v.symbol)) return null;
  const byAmount = v.by === 'amount' && v.side === 'buy';
  if (byAmount ? !v.amount : !v.qty) return null;
  if (v.order_type === 'limit' && !v.limit_price) return null;
  return {
    symbol: v.symbol,
    side: v.side,
    order_type: v.order_type,
    qty: byAmount ? undefined : v.qty,
    amount: byAmount ? String(v.amount) : undefined,
    limit_price: v.order_type === 'limit' && v.limit_price ? String(v.limit_price) : undefined,
    protect_price: v.order_type === 'close' && v.protect_price ? String(v.protect_price) : undefined,
    note: v.note?.trim() || undefined,
    tags: v.tags?.length ? v.tags : undefined,
  };
}

interface Props {
  accountId: string;
  disabled?: boolean;
  preset?: OrderPreset | null;
}

export function OrderForm({ accountId, disabled, preset }: Props) {
  const [form] = Form.useForm<FormValues>();
  const values = Form.useWatch([], form) as FormValues | undefined;
  const symbol = values?.symbol && SYMBOL_RE.test(values.symbol) ? values.symbol : undefined;
  const inst = useInstrument(symbol);
  const [options, setOptions] = useState<{ value: string; label: ReactNode }[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const { message } = App.useApp();

  useEffect(() => {
    if (preset) form.setFieldsValue({ ...DEFAULTS, ...preset, by: 'qty' });
  }, [preset, form]);

  const search = useDebouncedCallback((q: string) => {
    if (!q.trim()) {
      setOptions([]);
      return;
    }
    endpoints
      .searchInstruments(q.trim())
      .then((list) =>
        setOptions(
          list.map((i) => ({
            value: i.symbol,
            label: (
              <Space size={4}>
                <span>{i.symbol}</span>
                <span>{i.name}</span>
                {i.is_st && <Tag color="red">ST</Tag>}
              </Space>
            ),
          })),
        ),
      )
      .catch(() => setOptions([]));
  }, 300);

  const body = useMemo(() => toBody(values), [values]);
  const bodyKey = body ? JSON.stringify(body) : '';
  useEffect(() => {
    if (!body) {
      setPreview(null);
      setPreviewErr(null);
      return;
    }
    let cancelled = false;
    const t = setTimeout(() => {
      endpoints
        .previewOrder(accountId, body)
        .then((p) => {
          if (!cancelled) {
            setPreview(p);
            setPreviewErr(null);
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) {
            setPreview(null);
            setPreviewErr(errText(e));
          }
        });
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
    // bodyKey 代表 body 的内容，避免对象引用变化导致重复预估
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId, bodyKey]);

  const invalidate = () => {
    for (const k of ACCOUNT_SCOPED_KEYS) void qc.invalidateQueries({ queryKey: [k, accountId] });
    void qc.invalidateQueries({ queryKey: ['accounts'] });
  };

  const submit = useMutation({
    mutationFn: (b: OrderInput) => endpoints.submitOrder(accountId, b),
    onSuccess: (o) => {
      message.success(
        `已提交：${ORDER_TYPE_LABEL[o.order_type]}${SIDE_LABEL[o.side]} ${o.symbol} ${o.qty} 股，所属交易日 ${o.trade_date}`,
      );
      form.setFieldsValue({ qty: undefined, amount: undefined, note: '', tags: [] });
      invalidate();
    },
    onError: (e) => {
      const recorded = e instanceof ApiError && (e.details.order as Order | undefined);
      message.error(`${errText(e)}${recorded ? '（已记入订单与事件序列）' : ''}`);
      invalidate();
    },
  });

  const side = values?.side ?? 'buy';
  const orderType = values?.order_type ?? 'limit';
  const d = inst.data;
  const tick = 0.01; // 股票申报价格最小变动单位

  return (
    <Form<FormValues>
      form={form}
      layout="vertical"
      initialValues={DEFAULTS}
      disabled={disabled}
      onFinish={(v) => {
        const b = toBody(v);
        if (b) submit.mutate(b);
      }}
    >
      <Form.Item name="symbol" label="标的" rules={[{ required: true, pattern: SYMBOL_RE, message: '选择或输入如 600000.SH 的代码' }]}>
        <AutoComplete options={options} onSearch={search} placeholder="代码或名称，如 600000" allowClear />
      </Form.Item>
      {d && (
        <Descriptions
          size="small"
          column={2}
          style={{ marginBottom: 12 }}
          items={[
            { key: 'n', label: '名称', children: <Space size={4}>{d.name}{d.is_st && <Tag color="red">ST</Tag>}</Space> },
            { key: 'p', label: '前收', children: d.prev_close ?? '-' },
            { key: 'u', label: `涨停（${d.limits_date?.slice(5) ?? '-'}）`, children: d.up_limit ?? '不设' },
            { key: 'l', label: `跌停（${d.limits_date?.slice(5) ?? '-'}）`, children: d.down_limit ?? '不设' },
            {
              key: 's',
              label: '最新',
              children: d.snapshot ? `${d.snapshot.last}（${fmtClock(d.snapshot.ts)}）` : '暂无实时行情',
            },
            { key: 'h', label: '停牌', children: d.suspended ? <Tag color="orange">停牌</Tag> : '否' },
          ]}
        />
      )}
      <Space size={16} wrap>
        <Form.Item name="side" label="方向">
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={[
              { value: 'buy', label: '买入' },
              { value: 'sell', label: '卖出' },
            ]}
          />
        </Form.Item>
        <Form.Item name="order_type" label="类型">
          <Radio.Group
            optionType="button"
            options={(Object.keys(ORDER_TYPE_LABEL) as OrderType[]).map((t) => ({ value: t, label: ORDER_TYPE_LABEL[t] }))}
          />
        </Form.Item>
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        {TYPE_HELP[orderType]}
      </Typography.Paragraph>
      {side === 'buy' && (
        <Form.Item name="by" label="按">
          <Radio.Group
            options={[
              { value: 'qty', label: '数量' },
              { value: 'amount', label: '金额' },
            ]}
          />
        </Form.Item>
      )}
      {side === 'buy' && values?.by === 'amount' ? (
        <Form.Item name="amount" label="金额（元）" rules={[{ required: true, message: '请填写金额' }]}>
          <InputNumber min={1} step={10000} style={{ width: '100%' }} />
        </Form.Item>
      ) : (
        <Form.Item name="qty" label="数量（股）" rules={[{ required: true, message: '请填写数量' }]}>
          <InputNumber min={1} step={100} style={{ width: '100%' }} />
        </Form.Item>
      )}
      {orderType === 'limit' && (
        <Form.Item name="limit_price" label="限价" rules={[{ required: true, message: '请填写限价' }]}>
          <InputNumber min={0} step={tick} style={{ width: '100%' }} />
        </Form.Item>
      )}
      {orderType === 'close' && (
        <Form.Item name="protect_price" label="保护限价（可选）" extra="买入时收盘价高于它、卖出时低于它，订单失效">
          <InputNumber min={0} step={tick} style={{ width: '100%' }} />
        </Form.Item>
      )}
      <Form.Item name="note" label="备注（下单理由）">
        <Input maxLength={500} placeholder="例如：20 日均线上穿 60 日均线" />
      </Form.Item>
      <Form.Item name="tags" label="标签">
        <Select mode="tags" placeholder="例如：ma-cross" tokenSeparators={[',', ' ']} open={false} />
      </Form.Item>
      {preview && (
        <Alert
          style={{ marginBottom: 12 }}
          type={preview.ok ? 'info' : 'warning'}
          showIcon
          message={preview.ok ? '预估可以下单' : `预计会被拒绝：${preview.reason}`}
          description={
            <Space direction="vertical" size={0}>
              <span>所属交易日：{preview.trade_date}</span>
              <span>
                当日涨跌停：{String(preview.basis.down_limit ?? '不设')} ~ {String(preview.basis.up_limit ?? '不设')}
              </span>
              <span>数量：{preview.qty} 股</span>
              {preview.freeze_price && <span>冻结价：{preview.freeze_price}</span>}
              {side === 'buy' ? (
                <span>冻结资金：{fmtMoney(preview.frozen_cash)} 元（含预估费用 {fmtMoney(preview.estimated_fees)} 元）</span>
              ) : (
                <span>冻结股份：{preview.frozen_qty} 股，预估费用 {fmtMoney(preview.estimated_fees)} 元</span>
              )}
            </Space>
          }
        />
      )}
      {previewErr && <Alert style={{ marginBottom: 12 }} type="error" showIcon message={previewErr} />}
      <Button type="primary" danger={side === 'sell'} htmlType="submit" block loading={submit.isPending} disabled={disabled || !body}>
        {SIDE_LABEL[side]}
      </Button>
    </Form>
  );
}
