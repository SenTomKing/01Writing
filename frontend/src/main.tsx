import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HashRouter } from 'react-router-dom';
import App from './App';
import './style.css';

export const queries = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: 2000}}});
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={queries}><HashRouter><App/></HashRouter></QueryClientProvider></React.StrictMode>,
);
