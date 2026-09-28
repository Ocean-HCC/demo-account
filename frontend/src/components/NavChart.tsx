// 净值曲线：多条序列共用日期轴；第一条序列可叠加买卖点标记，点击标记回调所在日期（方案 3.6）。
import { Empty } from 'antd';
import type { EChartsOption } from 'echarts';
import { LineChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { useMemo } from 'react';
import { DOWN, UP } from '../utils/format';

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkPointComponent,
  CanvasRenderer,
]);

export interface NavSeries {
  name: string;
  points: [string, number][];
  dashed?: boolean;
}

export interface NavMarker {
  date: string;
  side: 'buy' | 'sell' | 'both';
  label: string;
}

interface Props {
  series: NavSeries[];
  markers?: NavMarker[];
  height?: number;
  onMarkerClick?: (date: string) => void;
}

export function NavChart({ series, markers = [], height = 320, onMarkerClick }: Props) {
  const option = useMemo<EChartsOption>(() => {
    const dates = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p[0])))).sort();
    const main = new Map(series[0]?.points ?? []);
    const markData = markers
      .filter((m) => main.has(m.date))
      .map((m) => ({
        name: m.label,
        coord: [m.date, main.get(m.date) as number],
        value: m.side === 'buy' ? '买' : m.side === 'sell' ? '卖' : '买卖',
        symbol: 'pin',
        symbolSize: 32,
        itemStyle: { color: m.side === 'buy' ? UP : m.side === 'sell' ? DOWN : '#722ed1' },
        label: { color: '#fff', fontSize: 11 },
        date: m.date,
      }));
    const opt = {
      animation: false,
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : '-'),
      },
      legend: { top: 0 },
      grid: { left: 56, right: 24, top: 36, bottom: 56 },
      xAxis: { type: 'category', data: dates, boundaryGap: false },
      yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => v.toFixed(3) } },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 12 }],
      series: series.map((s, i) => {
        const m = new Map(s.points);
        return {
          name: s.name,
          type: 'line',
          showSymbol: dates.length <= 3,
          connectNulls: true,
          data: dates.map((d) => m.get(d) ?? null),
          lineStyle: s.dashed ? { type: 'dashed', width: 1.5 } : { width: 2 },
          markPoint: i === 0 && markData.length ? { data: markData } : undefined,
        };
      }),
    };
    return opt as unknown as EChartsOption;
  }, [series, markers]);

  const onEvents = useMemo(
    () => ({
      click: (params: { componentType?: string; data?: { date?: string } }) => {
        if (params.componentType === 'markPoint' && params.data?.date) onMarkerClick?.(params.data.date);
      },
    }),
    [onMarkerClick],
  );

  if (!series.length || series.every((s) => s.points.length === 0)) {
    return <Empty description="暂无定版净值：每个交易日收盘结算后生成" />;
  }
  return <ReactEChartsCore echarts={echarts} option={option} notMerge style={{ height }} onEvents={onEvents} />;
}
