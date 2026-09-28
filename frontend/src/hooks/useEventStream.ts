// 订阅 /api/events/stream：账户事件使该账户的查询失效，告警弹出提示（实现 7）。
import { useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import { useEffect } from 'react';
import { ACCOUNT_SCOPED_KEYS } from '../api/queries';
import { EVENT_TYPES } from '../api/types';

interface StreamMessage {
  kind: 'event' | 'alert';
  account_id?: string;
  code?: string;
  message?: string;
  level?: string;
  summary?: string;
}

export function useEventStream(): void {
  const qc = useQueryClient();
  const { notification } = App.useApp();
  useEffect(() => {
    const es = new EventSource('/api/events/stream');
    const onMessage = (ev: MessageEvent<string>) => {
      let msg: StreamMessage;
      try {
        msg = JSON.parse(ev.data) as StreamMessage;
      } catch {
        return;
      }
      if (msg.kind === 'alert') {
        void qc.invalidateQueries({ queryKey: ['alerts'] });
        void qc.invalidateQueries({ queryKey: ['market-status'] });
        notification.warning({ message: `告警 ${msg.code ?? ''}`, description: msg.message, placement: 'bottomRight' });
        return;
      }
      if (msg.account_id) {
        for (const key of ACCOUNT_SCOPED_KEYS) void qc.invalidateQueries({ queryKey: [key, msg.account_id] });
      }
      void qc.invalidateQueries({ queryKey: ['accounts'] });
    };
    for (const t of [...EVENT_TYPES, 'alert']) es.addEventListener(t, onMessage as EventListener);
    return () => es.close();
  }, [qc, notification]);
}
