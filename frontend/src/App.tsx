import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp, ConfigProvider, Layout } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AppHeader } from './components/AppHeader';
import { useEventStream } from './hooks/useEventStream';
import { AccountPage } from './pages/AccountPage';
import { OverviewPage } from './pages/OverviewPage';
import { ReplayPage } from './pages/ReplayPage';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 } },
});

function Shell() {
  useEventStream();
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <AppHeader />
      <Layout.Content style={{ padding: 24, maxWidth: 1600, width: '100%', margin: '0 auto' }}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/accounts/:id" element={<AccountPage />} />
          <Route path="/accounts/:id/replay" element={<ReplayPage />} />
          <Route path="*" element={<OverviewPage />} />
        </Routes>
      </Layout.Content>
    </Layout>
  );
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff', borderRadius: 6 } }}>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Shell />
          </BrowserRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  );
}
