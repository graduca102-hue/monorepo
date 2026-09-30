(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const DEFAULT_SUPPORT_URL = 'https://t.me/UniversallSupportBot?start=market';
  const PROXY_BRAND_NAME = 'SOUS';

  function parseProxyContext() {
    const match = window.location.pathname.match(/^\/proxy\/([A-Za-z0-9_]+)\//);
    const botSlug = match && match[1] ? match[1].toLowerCase() : 'main';
    return {
      botSlug,
      basePath: `/proxy/${botSlug}`,
    };
  }

  const pathContext = parseProxyContext();
  const state = {
    botSlug: pathContext.botSlug,
    basePath: pathContext.basePath,
    initData: tg ? tg.initData || '' : '',
    boot: null,
    catalog: null,
    activeScreen: 'purchase',
    selectedTypeId: null,
    selectedCountryId: null,
    balanceCheckoutInFlight: false,
    catalogRecoveryTimerId: null,
    orderWaitPoll: null,
    orderWaitPollId: 0,
  };
  const ORDER_WAIT_POLL_INTERVAL_MS = 1200;

  const navButtons = Array.from(document.querySelectorAll('.proxy-nav-button'));
  const screenPurchase = document.getElementById('proxy-screen-purchase');
  const screenOrders = document.getElementById('proxy-screen-orders');
  const screenProfile = document.getElementById('proxy-screen-profile');
  const brandBadge = document.getElementById('proxy-brand-badge');
  const brandName = document.getElementById('proxy-brand-name');
  const brandSubtitle = document.getElementById('proxy-brand-subtitle');
  const balanceValue = document.getElementById('proxy-balance-value');
  const typeGrid = document.getElementById('proxy-type-grid');
  const countryGrid = document.getElementById('proxy-country-grid');
  const unitPriceChip = document.getElementById('proxy-unit-price');
  const ordersStatus = document.getElementById('proxy-orders-status');
  const ordersList = document.getElementById('proxy-orders-list');
  const profilePanel = document.getElementById('proxy-profile-panel');
  const overlay = document.getElementById('proxy-overlay');
  const overlayContent = document.getElementById('proxy-overlay-content');

  function switchScreen(screenId) {
    state.activeScreen = screenId;
    screenPurchase.classList.toggle('hidden', screenId !== 'purchase');
    screenOrders.classList.toggle('hidden', screenId !== 'orders');
    screenProfile.classList.toggle('hidden', screenId !== 'profile');
    navButtons.forEach((button) => {
      button.classList.toggle('proxy-nav-button-active', button.dataset.screen === screenId);
    });
    if (screenId === 'orders') {
      loadOrdersScreen();
    }
    if (screenId === 'profile') {
      renderProfileScreen();
    }
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function isLocalHostname(hostname) {
    const normalized = String(hostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
    return normalized === 'localhost' || normalized === '127.0.0.1' || normalized === '::1';
  }

  function normalizeExternalUrl(url, options = {}) {
    const cleanUrl = String(url || '').trim();
    if (!cleanUrl) {
      return null;
    }
    const allowTelegramScheme = Boolean(options.allowTelegramScheme);
    if (allowTelegramScheme && /^tg:\/\//i.test(cleanUrl)) {
      return cleanUrl;
    }
    if (!/^https?:\/\//i.test(cleanUrl)) {
      return null;
    }
    try {
      const parsedUrl = new URL(cleanUrl);
      if (!['https:', 'http:'].includes(parsedUrl.protocol)) {
        return null;
      }
      if (parsedUrl.protocol === 'http:' && !isLocalHostname(parsedUrl.hostname)) {
        return null;
      }
      return parsedUrl.toString();
    } catch (_error) {
      return null;
    }
  }

  function openExternalUrl(url, options = {}) {
    const cleanUrl = normalizeExternalUrl(url, options);
    if (!cleanUrl) {
      return false;
    }
    if (tg && typeof tg.openTelegramLink === 'function' && /^https?:\/\/t\.me\//i.test(cleanUrl)) {
      tg.openTelegramLink(cleanUrl);
      return true;
    }
    if (tg && typeof tg.openLink === 'function' && /^https?:\/\//i.test(cleanUrl)) {
      tg.openLink(cleanUrl);
      return true;
    }
    if (options.allowTelegramScheme && /^tg:\/\//i.test(cleanUrl)) {
      window.location.href = cleanUrl;
      return true;
    }
    if (/^https?:\/\//i.test(cleanUrl)) {
      window.open(cleanUrl, '_blank', 'noopener');
      return true;
    }
    return false;
  }

  async function api(path, options = {}) {
    const requestPath = path.startsWith('/') ? path : `/${path}`;
    const response = await fetch(`${state.basePath}${requestPath}`, {
      method: options.method || 'GET',
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(state.initData ? { 'X-Telegram-Init-Data': state.initData } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || 'Request failed');
    }
    return payload;
  }

  function openOverlay(html) {
    overlayContent.innerHTML = html;
    overlay.classList.remove('hidden');
  }

  function closeOverlay() {
    clearOrderWaitPoll();
    overlay.classList.add('hidden');
    overlayContent.innerHTML = '';
  }

  function clearOrderWaitPoll() {
    if (state.orderWaitPoll) {
      window.clearTimeout(state.orderWaitPoll);
      state.orderWaitPoll = null;
    }
    state.orderWaitPollId += 1;
  }

  function formatMoney(value) {
    return `${Number(value || 0).toFixed(2)} $`;
  }

  function triggerTextDownload(filename, text) {
    const blob = new Blob([String(text || '')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function copyToClipboard(text) {
    const value = String(text || '');
    if (!value) {
      return false;
    }
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(value);
      return true;
    }
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', 'readonly');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    return copied;
  }

  function getOrderStatusChipClass(order) {
    if (order && order.status === 'delivered') {
      return 'order-status-chip-success';
    }
    if (order && order.status === 'credited') {
      return 'order-status-chip-warning';
    }
    return 'order-status-chip-muted';
  }

  function isAwaitingDelivery(order) {
    return Boolean(order && !order.can_download && ['paid', 'delivery_pending'].includes(order.status));
  }

  function buildWaitingDeliveryMarkup(orderId, compact = false) {
    return `
      <div class="delivery-waiting-card ${compact ? 'delivery-waiting-card-compact' : ''}" data-order-waiting="${escapeHtml(orderId)}">
        <div class="delivery-waiting-clock" aria-hidden="true">
          <span class="delivery-waiting-clock-face">
            <span class="delivery-waiting-hand delivery-waiting-hand-hour"></span>
            <span class="delivery-waiting-hand delivery-waiting-hand-minute"></span>
          </span>
        </div>
        <div class="delivery-waiting-copy">
          <strong>Ожидаем выдачу</strong>
          <p>Как только прокси будут готовы, сразу покажем данные.</p>
        </div>
      </div>
    `;
  }

  function startOrderWaitPoll(orderId, onReady) {
    clearOrderWaitPoll();
    const pollId = state.orderWaitPollId;

    const tick = async () => {
      if (pollId !== state.orderWaitPollId || overlay.classList.contains('hidden')) {
        return;
      }

      const waitingNode = overlayContent.querySelector(`[data-order-waiting="${CSS.escape(String(orderId))}"]`);
      if (!waitingNode) {
        return;
      }

      try {
        const payload = await api(`/api/orders/${encodeURIComponent(orderId)}/refresh`, { method: 'POST' });
        if (payload && payload.profile && state.boot) {
          state.boot.profile = payload.profile;
          renderBrand();
        }
        const order = payload && payload.order ? payload.order : null;
        if (order && !isAwaitingDelivery(order)) {
          clearOrderWaitPoll();
          await bootstrap();
          onReady(order);
          return;
        }
      } catch (_error) {
        // Retry silently while waiting.
      }

      if (pollId !== state.orderWaitPollId || overlay.classList.contains('hidden')) {
        return;
      }
      state.orderWaitPoll = window.setTimeout(tick, ORDER_WAIT_POLL_INTERVAL_MS);
    };

    state.orderWaitPoll = window.setTimeout(tick, 700);
  }

  function openDeliveryPreview(order) {
    openOverlay(`
      <h3 class="sheet-title">Данные заказа</h3>
      <p class="sheet-subtitle">${escapeHtml(order.title || 'Прокси')} • Заказ #${escapeHtml(order.id)}</p>
      <div class="sheet-card">
        <pre class="delivery-preview-block">${escapeHtml(order.delivery_text || '')}</pre>
      </div>
    `);
  }

  function renderOrderDetailOverlay(order, headingText) {
    const statusChipClass = getOrderStatusChipClass(order);
    const deliveryReady = Boolean(order.can_download && order.delivery_text);
    const isWaitingDelivery = isAwaitingDelivery(order);
    openOverlay(`
      <div class="checkout-result-shell">
        <div class="checkout-result-hero">
          <div class="checkout-result-icon">${order.status === 'delivered' ? '✓' : '◌'}</div>
          <div>
            <h3 class="sheet-title checkout-result-title">${escapeHtml(headingText || (order.status === 'delivered' ? 'Заказ выполнен' : 'Статус заказа'))}</h3>
            <p class="sheet-subtitle">${escapeHtml(order.category_name || 'Прокси')}</p>
          </div>
        </div>
        <div class="sheet-card checkout-product-card">
          <div class="checkout-product-head">
            <div>
              <strong>${escapeHtml(order.title || 'Прокси')}</strong>
              <p>${escapeHtml(order.category_name || '')}</p>
            </div>
            <span class="order-status-chip ${statusChipClass}">${escapeHtml(order.status_label || '')}</span>
          </div>
          <div class="checkout-product-meta">
            <span>Заказ #${escapeHtml(order.id)}</span>
            <span>${formatMoney(order.total_price)}</span>
            <span>${escapeHtml(order.created_at || '')}</span>
          </div>
          ${isWaitingDelivery ? buildWaitingDeliveryMarkup(order.id) : ''}
          <div class="checkout-action-grid">
            ${deliveryReady ? '<button class="checkout-action-button" type="button" id="preview-order-data">Быстрый просмотр</button>' : ''}
            ${deliveryReady ? '<button class="checkout-action-button" type="button" id="download-order-data">Скачать данные файлом</button>' : ''}
          </div>
        </div>
      </div>
    `);

    document.getElementById('preview-order-data')?.addEventListener('click', () => openDeliveryPreview(order));
    document.getElementById('download-order-data')?.addEventListener('click', () => {
      triggerTextDownload(`proxy-order-${order.id}.txt`, order.delivery_text || '');
    });

    if (isWaitingDelivery) {
      startOrderWaitPoll(order.id, (updatedOrder) => {
        renderOrderDetailOverlay(
          updatedOrder,
          updatedOrder.status === 'delivered' ? 'Заказ выполнен' : 'Статус заказа',
        );
      });
    }
  }

  function renderCheckoutResultOverlay(orders) {
    if (!Array.isArray(orders) || !orders.length) {
      openOverlay(`
        <h3 class="sheet-title">Заказ оформлен</h3>
        <p class="sheet-subtitle">Покупка создана, но данные заказа пока не вернулись.</p>
      `);
      return;
    }

    const title = orders.length > 1
      ? 'Заказы оформлены'
      : (orders[0].status === 'delivered' ? 'Заказ выполнен' : 'Заказ оформлен');
    const cardsMarkup = orders.map((order) => `
      <div class="sheet-card checkout-product-card">
        <div class="checkout-product-head">
          <div>
            <strong>${escapeHtml(order.title || 'Прокси')}</strong>
            <p>${escapeHtml(order.category_name || '')}</p>
          </div>
          <span class="order-status-chip ${getOrderStatusChipClass(order)}">${escapeHtml(order.status_label || '')}</span>
        </div>
        <div class="checkout-product-meta">
          <span>Заказ #${escapeHtml(order.id)}</span>
          <span>${formatMoney(order.total_price)}</span>
          <span>${escapeHtml(order.created_at || '')}</span>
        </div>
        ${isAwaitingDelivery(order) ? buildWaitingDeliveryMarkup(order.id, true) : ''}
        <div class="checkout-action-grid">
          ${order.can_download && order.delivery_text ? `<button class="checkout-action-button" type="button" data-preview-order="${escapeHtml(order.id)}">Быстрый просмотр</button>` : ''}
          ${order.can_download && order.delivery_text ? `<button class="checkout-action-button" type="button" data-download-order="${escapeHtml(order.id)}">Скачать данные файлом</button>` : ''}
        </div>
      </div>
    `).join('');

    openOverlay(`
      <div class="checkout-result-shell">
        <div class="checkout-result-hero">
          <div class="checkout-result-icon">${orders.every((order) => order.status === 'delivered') ? '✓' : '◌'}</div>
          <div>
            <h3 class="sheet-title checkout-result-title">${escapeHtml(title)}</h3>
            <p class="sheet-subtitle">Данные по прокси готовы сразу после покупки.</p>
          </div>
        </div>
        ${cardsMarkup}
      </div>
    `);

    overlayContent.querySelectorAll('[data-preview-order]').forEach((button) => {
      button.addEventListener('click', () => {
        const orderId = Number(button.getAttribute('data-preview-order') || 0);
        const order = orders.find((row) => Number(row.id) === orderId);
        if (order) {
          openDeliveryPreview(order);
        }
      });
    });
    overlayContent.querySelectorAll('[data-download-order]').forEach((button) => {
      button.addEventListener('click', () => {
        const orderId = Number(button.getAttribute('data-download-order') || 0);
        const order = orders.find((row) => Number(row.id) === orderId);
        if (order) {
          triggerTextDownload(`proxy-order-${order.id}.txt`, order.delivery_text || '');
        }
      });
    });
    const waitingOrder = orders.find((order) => isAwaitingDelivery(order));
    if (waitingOrder) {
      startOrderWaitPoll(waitingOrder.id, (updatedOrder) => {
        renderOrderDetailOverlay(
          updatedOrder,
          updatedOrder.status === 'delivered' ? 'Заказ выполнен' : 'Статус заказа',
        );
      });
    }
  }

  function bindOrdersActions(items) {
    ordersList.querySelectorAll('[data-copy-proxy]').forEach((button) => {
      button.addEventListener('click', async () => {
        const itemId = String(button.getAttribute('data-copy-proxy') || '');
        const item = items.find((row) => String(row.id) === itemId);
        if (!item || !item.endpoint) {
          return;
        }
        const originalText = button.textContent;
        try {
          await copyToClipboard(item.endpoint);
          button.textContent = 'Скопировано';
        } catch (_error) {
          button.textContent = 'Не удалось скопировать';
        }
        window.setTimeout(() => {
          button.textContent = originalText;
        }, 1200);
      });
    });

    ordersList.querySelectorAll('[data-download-proxy]').forEach((button) => {
      button.addEventListener('click', () => {
        const itemId = String(button.getAttribute('data-download-proxy') || '');
        const item = items.find((row) => String(row.id) === itemId);
        if (!item || !item.endpoint) {
          return;
        }
        triggerTextDownload(`proxy-${item.order_id}.txt`, item.endpoint);
      });
    });
  }

  async function loadOrdersScreen() {
    ordersStatus.textContent = 'Загрузка заказов…';
    ordersList.innerHTML = '';
    try {
      const payload = await api('/api/manager');
      const items = Array.isArray(payload && payload.items) ? payload.items : [];
      if (!items.length) {
        ordersStatus.textContent = 'У вас пока нет выданных прокси.';
        return;
      }

      ordersStatus.textContent = '';
      ordersList.innerHTML = items.map((item) => `
        <article class="sheet-card manager-proxy-card">
          <div class="manager-proxy-head">
            <div>
              <strong>${escapeHtml(item.country_name || 'Прокси')} • ${escapeHtml(item.protocol || 'HTTP')}</strong>
              <p>Действует до ${escapeHtml(item.expires_at || '—')}</p>
            </div>
            <span class="order-status-chip ${getOrderStatusChipClass(item)}">${escapeHtml(item.status_label || '')}</span>
          </div>
          <div class="manager-proxy-meta">
            <span>Заказ #${escapeHtml(item.order_id)}</span>
            <span>${escapeHtml(item.duration_days)} дн.</span>
            <span>${escapeHtml(item.created_at || '')}</span>
          </div>
          <pre class="manager-proxy-preview">${escapeHtml(item.endpoint || 'Данные ещё подгружаются')}</pre>
          <div class="manager-proxy-actions">
            <button class="checkout-action-button" type="button" data-copy-proxy="${escapeHtml(item.id)}">Скопировать прокси</button>
            <button class="manager-action-secondary" type="button" data-download-proxy="${escapeHtml(item.id)}">Скачать строку</button>
          </div>
        </article>
      `).join('');
      bindOrdersActions(items);
    } catch (error) {
      ordersStatus.textContent = error.message || 'Не удалось загрузить список прокси.';
    }
  }

  function renderProfileScreen() {
    if (!state.boot) {
      profilePanel.innerHTML = '';
      return;
    }

    const profile = state.boot.profile || {};
    const user = state.boot.user || {};
    const brand = state.boot.brand || {};
    const username = user.username ? `@${user.username}` : brand.subtitle || '';
    const discountPercent = Number(profile.discount_percent || 0);
    const discountCode = String(profile.discount_code || '').trim();
    const hasActiveDiscount = discountPercent > 0 && discountCode;
    profilePanel.innerHTML = `
      <article class="sheet-card profile-card">
        <div class="profile-card-head">
          <div>
            <strong>${escapeHtml(PROXY_BRAND_NAME)}</strong>
            <p>${escapeHtml(username)}</p>
          </div>
          <span class="price-chip">${Number(profile.balance || 0).toFixed(2)} $</span>
        </div>
        <div class="profile-stat-grid">
          <div class="profile-stat-card">
            <span>Заказов</span>
            <strong>${escapeHtml(state.boot.orders_count || 0)}</strong>
          </div>
          <div class="profile-stat-card">
            <span>Баланс</span>
            <strong>${Number(profile.balance || 0).toFixed(2)} $</strong>
          </div>
        </div>
        <div class="promo-status-card ${hasActiveDiscount ? 'promo-status-card-active' : ''}">
          <strong>${hasActiveDiscount ? `Активна скидка ${discountPercent.toFixed(2)}%` : 'Активной скидки пока нет'}</strong>
          <p>${hasActiveDiscount ? `Код: ${escapeHtml(discountCode)}` : 'Можно активировать промокод на баланс или скидку.'}</p>
        </div>
        <div class="profile-action-grid">
          <button id="profile-topup-button" class="checkout-action-button" type="button">Пополнить баланс</button>
          <button id="profile-promocode-button" class="checkout-action-button" type="button">Активировать промокод</button>
          <button id="profile-support-button" class="manager-action-secondary" type="button">Поддержка</button>
        </div>
      </article>
    `;

    document.getElementById('profile-topup-button')?.addEventListener('click', openTopupSheet);
    document.getElementById('profile-promocode-button')?.addEventListener('click', openPromoCodeSheet);
    document.getElementById('profile-support-button')?.addEventListener('click', openSupport);
  }

  async function openProxyManager() {
    openOverlay('<h3 class="sheet-title">Заказы</h3><p class="sheet-subtitle">Загрузка списка прокси…</p>');
    try {
      const payload = await api('/api/manager');
      const items = Array.isArray(payload && payload.items) ? payload.items : [];
      if (!items.length) {
        openOverlay(`
          <h3 class="sheet-title">Заказы</h3>
          <p class="sheet-subtitle">У вас пока нет выданных прокси.</p>
        `);
        return;
      }

      const cardsMarkup = items.map((item) => `
        <div class="sheet-card manager-proxy-card">
          <div class="manager-proxy-head">
            <div>
              <strong>${escapeHtml(item.country_name || 'Прокси')} • ${escapeHtml(item.protocol || 'HTTP')}</strong>
              <p>Действует до ${escapeHtml(item.expires_at || '—')}</p>
            </div>
            <span class="order-status-chip ${getOrderStatusChipClass(item)}">${escapeHtml(item.status_label || '')}</span>
          </div>
          <div class="manager-proxy-meta">
            <span>Заказ #${escapeHtml(item.order_id)}</span>
            <span>${escapeHtml(item.duration_days)} дн.</span>
            <span>${escapeHtml(item.created_at || '')}</span>
          </div>
          <pre class="manager-proxy-preview">${escapeHtml(item.endpoint || 'Данные ещё подгружаются')}</pre>
          <div class="manager-proxy-actions">
            <button class="checkout-action-button" type="button" data-copy-proxy="${escapeHtml(item.id)}">Скопировать прокси</button>
            <button class="manager-action-secondary" type="button" data-download-proxy="${escapeHtml(item.id)}">Скачать строку</button>
          </div>
        </div>
      `).join('');

      openOverlay(`
        <h3 class="sheet-title">Заказы</h3>
        <p class="sheet-subtitle">Все выданные прокси и срок их действия.</p>
        <div class="manager-list">${cardsMarkup}</div>
      `);

      overlayContent.querySelectorAll('[data-copy-proxy]').forEach((button) => {
        button.addEventListener('click', async () => {
          const itemId = String(button.getAttribute('data-copy-proxy') || '');
          const item = items.find((row) => String(row.id) === itemId);
          if (!item || !item.endpoint) {
            return;
          }
          const originalText = button.textContent;
          try {
            await copyToClipboard(item.endpoint);
            button.textContent = 'Скопировано';
          } catch (_error) {
            button.textContent = 'Не удалось скопировать';
          }
          window.setTimeout(() => {
            button.textContent = originalText;
          }, 1200);
        });
      });

      overlayContent.querySelectorAll('[data-download-proxy]').forEach((button) => {
        button.addEventListener('click', () => {
          const itemId = String(button.getAttribute('data-download-proxy') || '');
          const item = items.find((row) => String(row.id) === itemId);
          if (!item || !item.endpoint) {
            return;
          }
          triggerTextDownload(`proxy-${item.order_id}.txt`, item.endpoint);
        });
      });
    } catch (error) {
      openOverlay(`
        <h3 class="sheet-title">Заказы</h3>
        <p class="sheet-subtitle">${escapeHtml(error.message || 'Не удалось загрузить список прокси.')}</p>
      `);
    }
  }

  function stopCatalogRecoveryPolling() {
    if (state.catalogRecoveryTimerId) {
      window.clearTimeout(state.catalogRecoveryTimerId);
      state.catalogRecoveryTimerId = null;
    }
  }

  function scheduleCatalogRecoveryPolling() {
    if (state.catalogRecoveryTimerId) {
      return;
    }
    state.catalogRecoveryTimerId = window.setTimeout(async () => {
      state.catalogRecoveryTimerId = null;
      try {
        const catalog = await api('/api/catalog');
        state.catalog = catalog;
        const catalogTypes = Array.isArray(catalog && catalog.types) ? catalog.types : [];
        if (!catalogTypes.some((type) => type.id === state.selectedTypeId)) {
          state.selectedTypeId = catalogTypes.length ? catalogTypes[0].id : null;
        }
        renderTypes();
        renderCountries();
        if (!catalog || catalog.catalog_available === false) {
          scheduleCatalogRecoveryPolling();
        }
      } catch (error) {
        scheduleCatalogRecoveryPolling();
      }
    }, 2500);
  }

  function renderBrand() {
    if (!state.boot) return;
    const brand = state.boot.brand || {};
    const profile = state.boot.profile || {};
    brandName.textContent = PROXY_BRAND_NAME;
    brandSubtitle.textContent = brand.subtitle || '';
    brandBadge.innerHTML = `<span class="sous-logo-word">${escapeHtml(PROXY_BRAND_NAME)}</span>`;
    balanceValue.textContent = `${Number(profile.balance || 0).toFixed(2)} $`;
    if (state.activeScreen === 'profile') {
      renderProfileScreen();
    }
  }

  function getTypeIcon(accent) {
    const icons = {
      diamond: '<svg viewBox="0 0 24 24"><path d="M7.3 4.5h9.4l4.1 4.9L12 19.5 3.2 9.4l4.1-4.9Z" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9.2 4.5 12 9.4l2.8-4.9M6.2 9.4h11.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.8 12h16.4M12 3.5c2.3 2.3 3.6 5.3 3.6 8.5 0 3.2-1.3 6.2-3.6 8.5-2.3-2.3-3.6-5.3-3.6-8.5 0-3.2 1.3-6.2 3.6-8.5Z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
      shield: '<svg viewBox="0 0 24 24"><path d="M12 3.8 18.5 6v5.4c0 4-2.4 7.4-6.5 8.8-4.1-1.4-6.5-4.8-6.5-8.8V6L12 3.8Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="m9.4 11.9 1.6 1.7 3.6-3.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      chain: '<svg viewBox="0 0 24 24"><path d="M9.1 14.9 7 17a3.2 3.2 0 1 0 4.5 4.5l2.1-2.1M14.9 9.1 17 7a3.2 3.2 0 1 0-4.5-4.5L10.4 4.6M8.2 15.8l7.6-7.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    };
    return icons[accent] || icons.globe;
  }

  function renderTypes() {
    typeGrid.innerHTML = '';
    const types = (state.catalog && state.catalog.types) || [];
    types.forEach((type) => {
      const relatedCountries = ((state.catalog && state.catalog.countries) || []).filter(
        (country) => String(country.type_id || '').toLowerCase() === String(type.id || '').toLowerCase(),
      );
      const priceCandidates = relatedCountries
        .map((country) => (country && country.sale_unit_price != null ? Number(country.sale_unit_price) : null))
        .filter((value) => Number.isFinite(value));
      const minPrice = priceCandidates.length ? Math.min(...priceCandidates) : null;
      const badgeLabel = type.badge || (type.id === 'basic' ? 'Популярный' : '');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `type-card${type.available ? '' : ' type-card-disabled'}${state.selectedTypeId === type.id ? ' type-card-selected' : ''}`;
      button.innerHTML = `
        <div class="type-icon">${getTypeIcon(type.accent)}</div>
        <div class="type-copy">
          <strong>${escapeHtml(type.title)} <span class="type-inline-label">${type.available ? 'прокси' : 'скоро'}</span></strong>
          <span>${escapeHtml(type.description || '')}</span>
        </div>
        <div class="type-side">
          ${minPrice != null && type.available ? `<span class="type-price">от ${minPrice.toFixed(2)} $</span>` : ''}
          ${badgeLabel ? `<span class="type-badge">${escapeHtml(badgeLabel)}</span>` : '<span class="type-arrow">›</span>'}
        </div>
      `;
      if (type.available) {
        button.addEventListener('click', () => {
          state.selectedTypeId = type.id;
          renderTypes();
          renderCountries();
        });
      } else {
        button.disabled = true;
      }
      typeGrid.appendChild(button);
    });
  }

  function renderCountries() {
    countryGrid.innerHTML = '';
    const catalogAvailable = Boolean(state.catalog && state.catalog.catalog_available !== false);
    const catalogMessage = String((state.catalog && state.catalog.catalog_message) || '').trim();
    if (catalogAvailable) {
      stopCatalogRecoveryPolling();
    } else {
      scheduleCatalogRecoveryPolling();
    }
    const countries = ((state.catalog && state.catalog.countries) || []).filter(
      (country) => String(country.type_id || '').toLowerCase() === String(state.selectedTypeId || '').toLowerCase(),
    );
    const priceCandidates = countries
      .map((country) => (country && country.sale_unit_price != null ? Number(country.sale_unit_price) : null))
      .filter((value) => Number.isFinite(value));
    const saleUnitPrice = priceCandidates.length ? Math.min(...priceCandidates) : null;
    unitPriceChip.textContent = saleUnitPrice != null && catalogAvailable ? `от ${saleUnitPrice.toFixed(2)} $` : 'временно недоступно';
    if (!countries.length) {
      const emptyMessage = catalogMessage || 'Для этого типа пока нет доступных стран.';
      countryGrid.innerHTML = `<article class="empty-card">${escapeHtml(emptyMessage)}</article>`;
      return;
    }

    countries.forEach((country) => {
      const protocols = Array.isArray(country.protocols) ? country.protocols : [];
      const button = document.createElement('button');
      button.type = 'button';
      const countryAvailable = Boolean(catalogAvailable && country.available);
      button.className = `country-card${countryAvailable ? '' : ' country-card-disabled'}`;
      button.innerHTML = `
        <div class="country-flag">${country.image ? `<img src="${escapeHtml(country.image)}" alt="${escapeHtml(country.country_name)}" />` : ''}</div>
        <div class="country-copy">
          <strong>${escapeHtml(country.country_name)}</strong>
          <span>${countryAvailable ? `${protocols.length} протокола • ${escapeHtml(country.type_title || country.quality || 'Basic')}` : 'Временно недоступно'}</span>
          <div class="country-meta">
            ${protocols.map((item) => `<span class="meta-pill">${escapeHtml(item.name)}</span>`).join('')}
          </div>
        </div>
        <div>
          <div class="country-price">${countryAvailable && country.sale_unit_price != null ? `${Number(country.sale_unit_price).toFixed(2)} $` : '—'}</div>
          <div class="country-arrow">›</div>
        </div>
      `;
      if (countryAvailable) {
        button.addEventListener('click', () => openPurchaseSheet(country));
      } else {
        button.disabled = true;
      }
      countryGrid.appendChild(button);
    });
  }

  function openPurchaseSheet(country) {
    const protocols = Array.isArray(country.protocols) ? country.protocols : [];
    if (!protocols.length) {
      openOverlay('<h3 class="sheet-title">Прокси</h3><p class="sheet-subtitle">Для этой страны пока нет доступных протоколов.</p>');
      return;
    }

    openOverlay(`
      <h3 class="sheet-title">${escapeHtml(country.country_name)}</h3>
      <p class="sheet-subtitle">Выберите протокол и количество прокси.</p>
      <div class="sheet-card">
        <div id="proxy-protocol-grid" class="protocol-grid"></div>
        <div class="qty-row">
          <div>
            <strong>Количество</strong>
            <div class="sheet-subtitle">Цена за 1 шт.: ${Number(country.sale_unit_price || 0).toFixed(2)} $</div>
          </div>
          <div class="qty-controls">
            <button id="proxy-qty-dec" class="qty-button" type="button">−</button>
            <strong id="proxy-qty-value">1</strong>
            <button id="proxy-qty-inc" class="qty-button" type="button">+</button>
          </div>
        </div>
        <button id="proxy-buy-button" class="primary-sheet-button" type="button">Купить • ${Number(country.sale_unit_price || 0).toFixed(2)} $</button>
        <button id="proxy-buy-crypto" class="secondary-sheet-button" type="button">Оплатить CryptoBot</button>
        <button id="proxy-buy-topup" class="secondary-sheet-button" type="button">Пополнить баланс</button>
      </div>
    `);

    let selectedProtocol = protocols[0];
    let quantity = 1;
    const protocolGrid = document.getElementById('proxy-protocol-grid');
    const qtyValue = document.getElementById('proxy-qty-value');
    const buyButton = document.getElementById('proxy-buy-button');
    const cryptoButton = document.getElementById('proxy-buy-crypto');
    const decButton = document.getElementById('proxy-qty-dec');
    const incButton = document.getElementById('proxy-qty-inc');

    function renderProtocols() {
      protocolGrid.innerHTML = '';
      protocols.forEach((protocol) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `protocol-button${selectedProtocol && selectedProtocol.id === protocol.id ? ' protocol-button-active' : ''}`;
        button.innerHTML = `<strong>${escapeHtml(protocol.name)}</strong>`;
        button.addEventListener('click', () => {
          selectedProtocol = protocol;
          quantity = Math.min(quantity, Math.max(Number(protocol.stock || 1), 1));
          syncUi();
          renderProtocols();
        });
        protocolGrid.appendChild(button);
      });
    }

    function syncUi() {
      qtyValue.textContent = String(quantity);
      buyButton.textContent = `Купить • ${(Number(country.sale_unit_price || 0) * quantity).toFixed(2)} $`;
      cryptoButton.textContent = `CryptoBot • ${(Number(country.sale_unit_price || 0) * quantity).toFixed(2)} $`;
      decButton.disabled = quantity <= 1;
      incButton.disabled = quantity >= Math.max(Number(selectedProtocol.stock || 1), 1);
    }

    decButton.addEventListener('click', () => {
      quantity = Math.max(1, quantity - 1);
      syncUi();
    });
    incButton.addEventListener('click', () => {
      quantity = Math.min(Math.max(Number(selectedProtocol.stock || 1), 1), quantity + 1);
      syncUi();
    });
    async function checkoutProxy(provider, button, busyText) {
      if (state.balanceCheckoutInFlight) {
        return;
      }
      state.balanceCheckoutInFlight = true;
      button.disabled = true;
      button.textContent = busyText;
      try {
        const payload = await api('/api/checkout/balance', {
          method: 'POST',
          body: {
            provider,
            items: [
              {
                category_id: country.id,
                item_id: selectedProtocol.id,
                quantity,
                category_name: country.country_name,
              },
            ],
          },
        });
        if (payload && payload.profile && state.boot) {
          state.boot.profile = payload.profile;
          renderBrand();
        }
        if (payload.pay_url) {
          const payUrl = normalizeExternalUrl(payload.pay_url || '', { allowTelegramScheme: true });
          if (!payUrl || !openExternalUrl(payUrl, { allowTelegramScheme: true })) {
            throw new Error('Провайдер вернул небезопасную ссылку на оплату');
          }
          renderCheckoutResultOverlay(payload.orders || []);
          return;
        }
        renderCheckoutResultOverlay(payload.orders || []);
        await bootstrap();
      } catch (error) {
        button.disabled = false;
        button.textContent = error.message || 'Ошибка покупки';
      } finally {
        state.balanceCheckoutInFlight = false;
      }
    }

    buyButton.addEventListener('click', async () => {
      await checkoutProxy('balance', buyButton, 'Оформляем...');
    });
    cryptoButton.addEventListener('click', async () => {
      await checkoutProxy('crystalpay', cryptoButton, 'Создаём счёт...');
    });
    document.getElementById('proxy-buy-topup').addEventListener('click', openTopupSheet);

    renderProtocols();
    syncUi();
  }

  function openTopupSheet() {
    openOverlay(`
      <h3 class="sheet-title">Пополнение</h3>
      <p class="sheet-subtitle">Введите сумму и выберите способ оплаты.</p>
      <div class="sheet-card">
        <label class="topup-field">
          <span>Сумма</span>
          <input id="proxy-topup-amount" type="text" inputmode="decimal" placeholder="5.00" />
        </label>
        <button id="proxy-topup-show" class="primary-sheet-button" type="button">Продолжить</button>
        <div id="proxy-topup-methods" class="topup-methods hidden">
          <button class="topup-method-button" type="button" data-provider="lolz">LOLZ</button>
          <button class="topup-method-button" type="button" data-provider="heleket">Heleket</button>
          <button class="topup-method-button" type="button" data-provider="xrocket">XRocket</button>
          <button class="topup-method-button" type="button" data-provider="crystalpay">CryptoBot</button>
        </div>
      </div>
    `);

    const amountInput = document.getElementById('proxy-topup-amount');
    const showButton = document.getElementById('proxy-topup-show');
    const methods = document.getElementById('proxy-topup-methods');

    showButton.addEventListener('click', () => {
      methods.classList.remove('hidden');
    });
    methods.querySelectorAll('.topup-method-button').forEach((button) => {
      button.addEventListener('click', async () => {
        const amount = Number(String(amountInput.value || '').replace(',', '.'));
        if (!Number.isFinite(amount) || amount <= 0) {
          amountInput.focus();
          return;
        }
        button.disabled = true;
        button.textContent = 'Создаём счёт...';
        try {
          const payload = await api('/api/topup', {
            method: 'POST',
            body: {
              amount,
              provider: button.dataset.provider,
            },
          });
          const payUrl = normalizeExternalUrl(payload.pay_url || '', { allowTelegramScheme: true });
          if (!payUrl || !openExternalUrl(payUrl, { allowTelegramScheme: true })) {
            throw new Error('Провайдер вернул небезопасную ссылку на оплату');
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = error.message || 'Ошибка';
        }
      });
    });
  }

  function openPromoCodeSheet(feedbackMessage = '', feedbackKind = '', initialCode = '') {
    const profile = (state.boot && state.boot.profile) || {};
    const discountPercent = Number(profile.discount_percent || 0);
    const discountCode = String(profile.discount_code || '').trim();
    const hasActiveDiscount = discountPercent > 0 && discountCode;
    openOverlay(`
      <h3 class="sheet-title">Промокод</h3>
      <p class="sheet-subtitle">Активируйте код и получите баланс или скидку на следующую покупку.</p>
      <div class="sheet-card">
        <div class="promo-status-card ${hasActiveDiscount ? 'promo-status-card-active' : ''}">
          <strong>${hasActiveDiscount ? `Активна скидка ${discountPercent.toFixed(2)}%` : 'Активной скидки пока нет'}</strong>
          <p>${hasActiveDiscount ? `Код: ${escapeHtml(discountCode)}` : 'Если код выдаёт скидку, она сохранится до следующего заказа.'}</p>
        </div>
        ${feedbackMessage ? `<div class="promo-feedback ${feedbackKind === 'success' ? 'promo-feedback-success' : 'promo-feedback-error'}">${escapeHtml(feedbackMessage)}</div>` : ''}
        <label class="topup-field">
          <span>Промокод</span>
          <input id="proxy-promocode-input" type="text" placeholder="Введите промокод" autocomplete="off" value="${escapeHtml(initialCode)}" />
        </label>
        <button id="proxy-promocode-apply" class="primary-sheet-button" type="button">Активировать</button>
      </div>
    `);

    const input = document.getElementById('proxy-promocode-input');
    const button = document.getElementById('proxy-promocode-apply');
    if (!input || !button) {
      return;
    }

    const submit = async () => {
      const code = String(input.value || '').trim();
      if (!code) {
        input.focus();
        return;
      }

      button.disabled = true;
      button.textContent = 'Активируем...';
      try {
        const payload = await api('/api/promocode/activate', {
          method: 'POST',
          body: { code },
        });
        if (payload && payload.profile && state.boot) {
          state.boot.profile = payload.profile;
          renderBrand();
        }
        openPromoCodeSheet(payload && payload.message ? payload.message : 'Промокод активирован.', 'success');
      } catch (error) {
        openPromoCodeSheet(error && error.message ? error.message : 'Не удалось активировать промокод.', 'error', code);
      }
    };

    button.addEventListener('click', submit);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        submit();
      }
    });
    window.setTimeout(() => input.focus(), 60);
  }

  function openSupport() {
    const supportUrl = (state.boot && state.boot.bot ? state.boot.bot.support_url : '') || DEFAULT_SUPPORT_URL;
    if (openExternalUrl(supportUrl)) {
      return;
    }
    openOverlay('<h3 class="sheet-title">Поддержка</h3><p class="sheet-subtitle">Ссылка на поддержку не настроена или имеет небезопасный формат.</p>');
  }

  async function bootstrap() {
    const [boot, catalog] = await Promise.all([
      api('/api/bootstrap'),
      api('/api/catalog'),
    ]);
    state.boot = boot;
    state.catalog = catalog;
    const catalogTypes = Array.isArray(catalog && catalog.types) ? catalog.types : [];
    if (!catalogTypes.some((type) => type.id === state.selectedTypeId)) {
      state.selectedTypeId = catalogTypes.length ? catalogTypes[0].id : null;
    }
    renderBrand();
    renderTypes();
    renderCountries();
    switchScreen(state.activeScreen);
  }

  document.getElementById('proxy-overlay-close').addEventListener('click', closeOverlay);
  overlay.querySelector('.proxy-overlay-backdrop').addEventListener('click', closeOverlay);
  document.getElementById('proxy-balance-button').addEventListener('click', openTopupSheet);
  navButtons.forEach((button) => {
    button.addEventListener('click', () => {
      switchScreen(button.dataset.screen || 'purchase');
    });
  });

  bootstrap().catch((error) => {
    scheduleCatalogRecoveryPolling();
    countryGrid.innerHTML = `<article class="empty-card">${escapeHtml(error.message || 'Не удалось загрузить proxy mini app.')}</article>`;
  });
})();
