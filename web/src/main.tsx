import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './core/index.css';
import App from './core/App';
import { applyTheme } from './core/theme';

// Before the first render, so nothing ever paints in the wrong palette.
applyTheme();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
