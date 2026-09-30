import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Telegram UI kit styles (https://tgui.xelene.me)
import '@telegram-apps/telegram-ui/dist/styles.css'
// Font Awesome (config + icon packs)
import './lib/fontawesome'

import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
