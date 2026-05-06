import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

window.onerror = (_msg, _src, _line, _col, err) => {
  console.error('[Global error]', err?.message, err?.stack);
};

window.addEventListener('unhandledrejection', (e) => {
  console.error('[Unhandled rejection]', e.reason?.message || e.reason);
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
