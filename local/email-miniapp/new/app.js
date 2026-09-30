(() => {
  'use strict';

  const tg = window.Telegram?.WebApp || null;
  tg?.ready();
  tg?.expand();
  try { tg?.setHeaderColor?.('secondary_bg_color'); } catch (_) {}
  try { tg?.setBackgroundColor?.('secondary_bg_color'); } catch (_) {}
  const match = location.pathname.match(/^\/email\/([A-Za-z0-9_]+)\/?/);
  const slug = (match?.[1] || 'main').toLowerCase();
  const state = {
    basePath: `/email/${slug}`,
    initData: tg?.initData || '',
    boot: null,
    activations: [],
    domains: [],
    site: '',
    sort: 'price',
    filter: 'all',
    activeScreen: 'purchase',
    detailId: null,
    confirmRequestId: null,
    reorderRequestIds: Object.create(null),
    yandexExpanded: false,
    pollTimer: null,
    toastTimer: null,
  };

  const telegramBotSlug = slug === 'main' ? 'MarketPlaceABot' : slug;
  function showTelegramRequiredPage() {
    document.body.innerHTML = `<main class="telegram-gate"><div class="telegram-gate-icon">✈</div><h1>Откройте почту в Telegram</h1><p>Этот раздел работает только внутри Telegram Mini App.</p><a href="https://t.me/${encodeURIComponent(telegramBotSlug)}?start=email" target="_blank" rel="noopener">Открыть в Telegram</a></main>`;
  }

  if (!state.initData) {
    showTelegramRequiredPage();
    return;
  }

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const screenMap = {
    purchase: $('#screen-purchase'),
    activations: $('#screen-activations'),
    profile: $('#screen-profile'),
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function formatPrice(value) {
    const number = Number(value || 0);
    return `${number.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 6 })} $`;
  }

  function emailProvider(domain) {
    const value = String(domain || '').toLowerCase();
    if (value === 'gmail.com') return 'gmail';
    if (value === 'icloud.com') return 'icloud';
    if (value === 'outlook.com') return 'outlook';
    if (value === 'hotmail.com') return 'hotmail';
    if (value.startsWith('gmx.')) return 'gmx';
    if (value === 'mail.ru') return 'mailru';
    if (value === 'rambler.ru') return 'rambler';
    if (value === 'ya.ru' || value.startsWith('yandex.')) return 'yandex';
    return 'mail';
  }

  function emailProviderIcon(domain) {
    const provider = emailProvider(domain);
    const icons = {
      gmail: '<svg viewBox="0 0 48 48"><path fill="#4285f4" d="M7 38V15l8 6v17z"/><path fill="#34a853" d="M33 38V21l8-6v23z"/><path fill="#fbbc04" d="M7 15l5-4 12 9 12-9 5 4-17 13z"/><path fill="#ea4335" d="M12 11l12 9 12-9 5 4-17 13L7 15z"/></svg>',
      icloud: '<svg viewBox="0 0 48 48"><path fill="white" d="M15 35h20a8 8 0 0 0 1-16 12 12 0 0 0-23-2 9 9 0 0 0 2 18z"/></svg>',
      outlook: '<svg viewBox="0 0 48 48"><path fill="#fff" opacity=".96" d="M8 13h18v23H8z"/><path fill="#1174d3" d="M22 17h19v18H22z"/><path fill="#36a9e8" d="M22 18l9.5 8L41 18v-3H22z"/><path fill="#0b5cab" d="M5 10l22-4v36L5 38z"/><path fill="#fff" d="M12 18c5-6 12-1 12 6s-7 12-12 6c-3-4-3-8 0-12zm3 3c-2 2-2 6 0 7 3 2 5-1 5-4s-2-5-5-3z"/></svg>',
      hotmail: '<svg viewBox="0 0 48 48"><path fill="#fff" d="M7 14h34v24H7z"/><path fill="#ffb020" d="M7 14l17 13 17-13z"/><path fill="#f04c34" d="M7 38V14l17 18 17-18v24z"/><path fill="#fff" opacity=".92" d="M12 20h4v5h6v-5h4v15h-4v-6h-6v6h-4z"/></svg>',
      gmx: '<svg viewBox="0 0 48 48"><path fill="#ffcf21" d="M7 13h34v23H7z"/><path fill="#fff" d="M9 16l15 11 15-11v-3H9z"/><path fill="#8dcc28" d="M7 36l13-12 4 3 4-3 13 12z"/></svg>',
      mailru: '<svg viewBox="0 0 48 48"><path fill="white" d="M24 9a15 15 0 1 0 8 28l-3-4a10 10 0 1 1 5-9v3c0 3-4 3-4 0V16h-5v2c-8-5-14 8-7 13 3 2 6 1 8-1 2 5 13 4 13-3v-3A15 15 0 0 0 24 9zm0 18c-4 0-4-7 0-7s4 7 0 7z"/></svg>',
      rambler: '<svg viewBox="0 0 48 48"><path fill="white" d="M13 9h13c10 0 14 11 8 17l6 13h-9l-5-11h-5v11h-8zm8 7v6h5c4 0 4-6 0-6z"/></svg>',
      yandex: '<svg viewBox="0 0 48 48"><path fill="white" d="M27 40h-7l4-12c-6-2-9-6-9-11 0-7 5-10 13-10h9v33h-7V29h-1zm3-27h-2c-4 0-6 2-6 5s2 5 6 5h2z"/></svg>',
      mail: '<svg viewBox="0 0 48 48"><path fill="none" stroke="currentColor" stroke-width="4" d="M7 12h34v25H7z"/><path fill="none" stroke="currentColor" stroke-width="4" d="M8 14l16 13 16-13"/></svg>',
    };
    return `<span class="domain-avatar provider-icon provider-${provider}" aria-hidden="true">${icons[provider]}</span>`;
  }

  async function api(path, options = {}) {
    const response = await fetch(`${state.basePath}${path.startsWith('/') ? path : `/${path}`}`, {
      method: options.method || 'GET',
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(state.initData ? { 'X-Telegram-Init-Data': state.initData } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || 'Не удалось выполнить запрос.');
      error.code = payload.code || '';
      error.domain = payload.domain || '';
      throw error;
    }
    return payload;
  }

  function removeUnavailableDomain(error, fallbackDomain = '') {
    if (error?.code !== 'domain_unavailable') return false;
    const domain = String(error.domain || fallbackDomain || '').trim().toLowerCase();
    if (domain) state.domains = state.domains.filter(item => String(item.domain || '').toLowerCase() !== domain);
    closeSheet();
    renderDomains();
    toast(domain ? `@${domain} удалён — адресов нет` : 'Недоступный домен удалён');
    return true;
  }

  function haptic(type = 'light') { try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) {} }
  function toast(message) {
    const node = $('#toast');
    clearTimeout(state.toastTimer);
    node.textContent = message;
    node.classList.remove('hidden');
    state.toastTimer = setTimeout(() => node.classList.add('hidden'), 2400);
  }
  function setStateCard(node, mode, title, text = '') {
    node.className = `state-card${mode === 'error' ? ' error' : ''}`;
    node.innerHTML = `${mode === 'loading' ? '<div class="spinner"></div>' : ''}<strong>${escapeHtml(title)}</strong>${text ? escapeHtml(text) : ''}`;
  }
  function clearStateCard(node) { node.classList.add('hidden'); node.innerHTML = ''; }

  function showTelegramRequired(node) {
    node.className = 'state-card error telegram-required';
    node.innerHTML = `<strong>Откройте почту в Telegram</strong><p>Этот раздел работает только внутри Telegram Mini App.</p><a href="https://t.me/${encodeURIComponent(slug)}?start=email" target="_blank" rel="noopener">Открыть в Telegram</a>`;
  }

  function switchScreen(name) {
    state.activeScreen = name;
    Object.entries(screenMap).forEach(([key, node]) => node.classList.toggle('active', key === name));
    $$('.nav-button').forEach(button => button.classList.toggle('active', button.dataset.screen === name));
    closeSheet();
    if (name === 'activations') loadActivations();
    if (name === 'profile') renderProfile();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function syncProfile(profile) {
    if (!profile) return;
    state.boot = state.boot || {};
    state.boot.profile = { ...(state.boot.profile || {}), ...profile };
    renderHeader();
    renderProfile();
  }

  function renderHeader() {
    if (!state.boot) return;
    const brandName = state.boot.brand?.name || 'Email Activation';
    const avatarUrl = String(state.boot.brand?.avatar_url || '').trim();
    const avatar = $('#brand-avatar');
    const fallback = $('#brand-fallback');
    $('#brand-name').textContent = brandName;
    fallback.textContent = String(brandName).trim().charAt(0).toUpperCase() || 'E';
    if (avatarUrl) {
      if (avatar.dataset.src !== avatarUrl) {
        avatar.classList.add('hidden');
        fallback.classList.remove('hidden');
        avatar.dataset.src = avatarUrl;
        avatar.onload = () => { avatar.classList.remove('hidden'); fallback.classList.add('hidden'); };
        avatar.onerror = () => { avatar.classList.add('hidden'); fallback.classList.remove('hidden'); };
        avatar.src = avatarUrl;
      }
    } else {
      avatar.removeAttribute('src');
      delete avatar.dataset.src;
      avatar.classList.add('hidden');
      fallback.classList.remove('hidden');
    }
    $('#balance-value').textContent = formatPrice(state.boot.profile?.balance);
    const active = state.activations.filter(item => ['ordering', 'waiting'].includes(item.status)).length;
    const badge = $('#active-count');
    badge.textContent = String(active);
    badge.classList.toggle('hidden', !active);
  }

  function renderProfile() {
    if (!state.boot) return;
    const profile = state.boot.profile || {};
    $('#profile-balance-value').textContent = formatPrice(profile.balance);
    $('#profile-activations').textContent = String(profile.email_activations_count ?? state.activations.length);
    $('#profile-user-id').textContent = String(profile.user_id || state.boot.user?.id || '—');
  }

  function renderDomains() {
    const list = $('#domains-list');
    const items = state.domains.filter(item => item?.available && Number(item.count) > 0).sort((a, b) => state.sort === 'price'
      ? Number(a.price) - Number(b.price)
      : Number(b.count) - Number(a.count));
    $('#domains-summary').textContent = `${items.length} вариантов для ${state.site}`;
    if (!items.length) {
      list.innerHTML = '<div class="state-card"><strong>Адресов пока нет</strong>Попробуйте другой сайт или повторите поиск позже.</div>';
      return;
    }
    const isYandexDomain = item => item.domain === 'ya.ru' || String(item.domain || '').startsWith('yandex.');
    const yandexItems = items.filter(isYandexDomain);
    const regularItems = items.filter(item => !isYandexDomain(item));
    const domainCard = item => `
      <button class="domain-card" type="button" data-domain="${escapeHtml(item.domain)}">
        ${emailProviderIcon(item.domain)}
        <span class="domain-info"><strong>@${escapeHtml(item.domain)}</strong><span>Одноразовый адрес</span></span>
        <span class="domain-price"><strong>${escapeHtml(formatPrice(item.price))}</strong><span>${escapeHtml(item.count)} шт.</span></span>
      </button>`;
    const yandexGroup = yandexItems.length ? `
      <article class="domain-group yandex-group${state.yandexExpanded ? ' open' : ''}">
        <button id="yandex-group-toggle" class="domain-group-toggle" type="button" aria-expanded="${state.yandexExpanded ? 'true' : 'false'}">
          ${emailProviderIcon('yandex.ru')}
          <span class="domain-info"><strong>Yandex</strong><span>${yandexItems.length} доменов · ${escapeHtml(yandexItems.reduce((sum, item) => sum + Number(item.count || 0), 0).toLocaleString('ru-RU'))} адресов</span></span>
          <span class="domain-group-side"><strong>от ${escapeHtml(formatPrice(Math.min(...yandexItems.map(item => Number(item.price || 0)))))}</strong><i aria-hidden="true">⌄</i></span>
        </button>
        <div class="domain-group-items">${yandexItems.map(domainCard).join('')}</div>
      </article>` : '';
    list.innerHTML = yandexGroup + regularItems.map(domainCard).join('');
    $('#yandex-group-toggle', list)?.addEventListener('click', event => {
      state.yandexExpanded = !state.yandexExpanded;
      const group = event.currentTarget.closest('.domain-group');
      group?.classList.toggle('open', state.yandexExpanded);
      event.currentTarget.setAttribute('aria-expanded', state.yandexExpanded ? 'true' : 'false');
    });
    $$('.domain-card', list).forEach(button => button.addEventListener('click', () => {
      const item = items.find(row => row.domain === button.dataset.domain);
      if (item) openConfirm(item);
    }));
  }

  async function searchDomains(event) {
    event?.preventDefault();
    const site = $('#site-input').value.trim();
    if (!site) return toast('Введите адрес сайта');
    const status = $('#purchase-status');
    const section = $('#domains-section');
    section.classList.add('hidden');
    setStateCard(status, 'loading', 'Проверяем доступные адреса', 'Обычно это занимает несколько секунд.');
    $('#search-button').disabled = true;
    try {
      const payload = await api(`/api/domains?site=${encodeURIComponent(site)}`);
      state.site = payload.site || site;
      state.yandexExpanded = false;
      state.domains = Array.isArray(payload.items)
        ? payload.items.filter(item => item?.available && Number(item.count) > 0)
        : [];
      clearStateCard(status);
      section.classList.remove('hidden');
      renderDomains();
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      setStateCard(status, 'error', 'Не удалось найти адреса', error.message);
    } finally { $('#search-button').disabled = false; }
  }

  function openSheet(html) {
    clearPolling();
    $('#sheet').innerHTML = html;
    $('#overlay').classList.remove('hidden');
    $('#overlay').setAttribute('aria-hidden', 'false');
    try { tg?.BackButton?.show?.(); } catch (_) {}
  }
  function closeSheet() {
    clearPolling();
    state.detailId = null;
    $('#overlay').classList.add('hidden');
    $('#overlay').setAttribute('aria-hidden', 'true');
    $('#sheet').innerHTML = '';
    try { tg?.BackButton?.hide?.(); } catch (_) {}
  }
  try { tg?.BackButton?.onClick?.(() => { if (!$('#overlay').classList.contains('hidden')) closeSheet(); }); } catch (_) {}
  function clearPolling() { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = null; }

  function openConfirm(item) {
    haptic();
    state.confirmRequestId = requestId();
    openSheet(`
      <span class="sheet-kicker">Подтверждение</span><h2>Купить адрес?</h2>
      <p class="sheet-subtitle">После оплаты вы получите почту для одного входящего письма.</p>
      <div class="confirm-summary">
        <div class="confirm-row"><span>Сайт</span><strong>${escapeHtml(state.site)}</strong></div>
        <div class="confirm-row"><span>Домен почты</span><strong>@${escapeHtml(item.domain)}</strong></div>
        <div class="confirm-row price"><span>К оплате</span><strong>${escapeHtml(formatPrice(item.price))}</strong></div>
      </div>
      <button id="confirm-order" class="primary-button" type="button">Оплатить с баланса</button>
      <button class="secondary-button" type="button" data-close>Вернуться</button>`);
    $('#confirm-order').addEventListener('click', () => createOrder(item));
    $('[data-close]', $('#sheet')).addEventListener('click', closeSheet);
  }

  function requestId() {
    return (crypto.randomUUID?.() || `${Date.now()}_${Math.random().toString(36).slice(2)}`).replace(/-/g, '_').slice(0, 80);
  }

  async function createOrder(item) {
    const button = $('#confirm-order');
    button.disabled = true; button.textContent = 'Создаём адрес…';
    try {
      const payload = await api('/api/order', { method: 'POST', body: { site: state.site, domain: item.domain, request_id: state.confirmRequestId || requestId() } });
      syncProfile(payload.profile);
      upsertActivation(payload.activation);
      haptic('medium');
      openActivation(payload.activation);
    } catch (error) {
      if (removeUnavailableDomain(error, item.domain)) return;
      button.disabled = false; button.textContent = 'Оплатить с баланса';
      toast(error.message);
    }
  }

  function upsertActivation(item) {
    if (!item) return;
    const index = state.activations.findIndex(row => row.id === item.id);
    if (index >= 0) state.activations[index] = item; else state.activations.unshift(item);
    renderHeader();
    renderActivations();
  }

  function activationVisible(item) {
    if (state.filter === 'active') return ['ordering', 'waiting'].includes(item.status);
    if (state.filter === 'done') return item.status === 'received';
    return true;
  }

  function renderActivations() {
    const list = $('#activations-list');
    const items = state.activations.filter(activationVisible);
    if (!items.length) {
      list.innerHTML = '<div class="state-card"><strong>Здесь пока пусто</strong>Новая активация появится сразу после покупки.</div>';
      return;
    }
    list.innerHTML = items.map(item => `
      <button class="activation-card" type="button" data-id="${item.id}">
        <span class="activation-head"><span class="site-avatar">${escapeHtml(String(item.site || '?')[0])}</span><span class="activation-title"><strong>${escapeHtml(item.site || 'Активация')}</strong><span>${escapeHtml(item.domain || '')}</span></span><span class="status-pill ${escapeHtml(item.status)}">${escapeHtml(item.status_label || item.status)}</span></span>
        <span class="activation-foot"><span class="activation-email">${escapeHtml(item.email || 'Адрес создаётся…')}</span><span class="activation-price">${escapeHtml(formatPrice(item.price))}</span></span>
      </button>`).join('');
    $$('.activation-card', list).forEach(button => button.addEventListener('click', () => {
      const item = state.activations.find(row => String(row.id) === button.dataset.id);
      if (item) openActivation(item);
    }));
  }

  async function loadActivations(silent = false) {
    const status = $('#activations-status');
    if (!silent) setStateCard(status, 'loading', 'Загружаем активации');
    try {
      const payload = await api('/api/activations');
      state.activations = Array.isArray(payload.items) ? payload.items : [];
      syncProfile(payload.profile);
      clearStateCard(status); renderActivations(); renderHeader();
    } catch (error) {
      setStateCard(status, 'error', 'Не удалось загрузить активации', error.message);
    }
  }

  function detailMarkup(item) {
    const active = ['ordering', 'waiting'].includes(item.status);
    const received = item.status === 'received';
    const canReorder = Boolean(item.can_reorder);
    const messageLinks = Array.isArray(item.links) ? item.links.filter(url => /^https?:\/\//i.test(String(url || ''))) : [];
    const linksMarkup = messageLinks.map((url, index) => `<button class="secondary-button email-message-link" type="button" data-email-link="${escapeHtml(url)}">Открыть ссылку${messageLinks.length > 1 ? ` ${index + 1}` : ''}</button>`).join('');
    return `
      <span class="sheet-kicker">Активация #${item.id}</span><h2>${escapeHtml(item.site || 'Детали')}</h2>
      <p class="sheet-subtitle"><span class="status-pill ${escapeHtml(item.status)}">${escapeHtml(item.status_label || item.status)}</span></p>
      ${item.email ? `<div class="detail-email"><code>${escapeHtml(item.email)}</code><button class="copy-button" type="button" data-copy="${escapeHtml(item.email)}">Копировать</button></div>` : ''}
      ${received ? `<div class="message-box"><span>Полученное письмо</span>${item.code ? `<div class="message-code"><strong>${escapeHtml(item.code)}</strong><button class="copy-button" type="button" data-copy="${escapeHtml(item.code)}">Копировать</button></div>` : ''}${item.message ? `<div class="message-text">${escapeHtml(item.message)}</div>` : ''}${linksMarkup}</div>` : ''}
      ${active ? '<div class="waiting-panel"><div class="waiting-pulse">✉</div><strong>Ждём входящее письмо</strong><p>Проверяем автоматически каждые 8 секунд. Можно проверить сейчас.</p></div><button id="check-activation" class="primary-button" type="button">Проверить письмо</button>' : ''}
      ${item.error ? `<div class="state-card error"><strong>Активация завершена</strong>${escapeHtml(item.error)}${item.refunded ? '<br>Средства возвращены на баланс.' : ''}</div>` : ''}
      ${canReorder ? '<button id="reorder-activation" class="primary-button" type="button">Заказать ещё раз</button>' : ''}
      <button class="secondary-button" type="button" data-close>Закрыть</button>`;
  }

  function openActivation(item) {
    state.detailId = item.id;
    openSheet(detailMarkup(item));
    state.detailId = item.id;
    bindDetail(item);
    if (['ordering', 'waiting'].includes(item.status)) schedulePoll(item.id);
  }

  function bindDetail(item) {
    $$('[data-copy]', $('#sheet')).forEach(button => button.addEventListener('click', () => copyText(button.dataset.copy)));
    $$('[data-email-link]', $('#sheet')).forEach(button => button.addEventListener('click', () => {
      const url = button.dataset.emailLink;
      if (!/^https?:\/\//i.test(String(url || ''))) return;
      if (tg?.openLink) tg.openLink(url); else window.open(url, '_blank', 'noopener');
    }));
    $('[data-close]', $('#sheet'))?.addEventListener('click', closeSheet);
    $('#check-activation')?.addEventListener('click', () => checkActivation(item.id, false));
    $('#reorder-activation')?.addEventListener('click', () => reorderActivation(item));
  }

  function reorderRequestId(id) {
    const key = String(id);
    if (!state.reorderRequestIds[key]) state.reorderRequestIds[key] = requestId();
    return state.reorderRequestIds[key];
  }

  async function reorderActivation(item) {
    const button = $('#reorder-activation');
    if (!button || button.disabled) return;
    button.disabled = true;
    button.textContent = 'Создаём адрес…';
    try {
      const payload = await api(`/api/activations/${encodeURIComponent(item.id)}/reorder`, {
        method: 'POST',
        body: { request_id: reorderRequestId(item.id) },
      });
      syncProfile(payload.profile);
      upsertActivation(payload.activation);
      delete state.reorderRequestIds[String(item.id)];
      haptic('medium');
      openActivation(payload.activation);
    } catch (error) {
      if (removeUnavailableDomain(error, item.domain)) return;
      button.disabled = false;
      button.textContent = 'Заказать ещё раз';
      toast(error.message);
    }
  }

  function schedulePoll(id) {
    clearPolling();
    state.pollTimer = setTimeout(() => checkActivation(id, true), 8000);
  }

  async function checkActivation(id, silent) {
    if (!silent) { const button = $('#check-activation'); if (button) { button.disabled = true; button.textContent = 'Проверяем…'; } }
    try {
      const payload = await api(`/api/activations/${encodeURIComponent(id)}/check`, { method: 'POST' });
      syncProfile(payload.profile); upsertActivation(payload.activation);
      if (state.detailId === id) openActivation(payload.activation);
      if (!silent && payload.activation.status !== 'received') toast('Письмо пока не пришло');
    } catch (error) {
      if (!silent) toast(error.message);
      if (state.detailId === id) schedulePoll(id);
    }
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else { const area = document.createElement('textarea'); area.value = text; document.body.append(area); area.select(); document.execCommand('copy'); area.remove(); }
      haptic(); toast('Скопировано');
    } catch (_) { toast('Не удалось скопировать'); }
  }

  function openTopup() {
    openSheet(`
      <span class="sheet-kicker">Пополнение</span><h2>Добавить средства</h2><p class="sheet-subtitle">Укажите сумму в долларах и выберите удобный способ.</p>
      <form id="topup-form" class="topup-form"><label for="topup-amount">Сумма, $</label><input id="topup-amount" class="topup-input" type="number" min="1" max="100000" step="0.01" inputmode="decimal" placeholder="10.00" required />
      <div class="provider-grid"><button class="provider-button active" type="button" data-provider="xrocket">XRocket</button><button class="provider-button" type="button" data-provider="lolz">LOLZ</button><button class="provider-button" type="button" data-provider="heleket">Heleket</button><button class="provider-button" type="button" data-provider="crystalpay">CryptoBot</button></div>
      <button id="topup-submit" class="primary-button" type="submit">Перейти к оплате</button></form><button class="secondary-button" type="button" data-close>Закрыть</button>`);
    let provider = 'xrocket';
    $$('.provider-button', $('#sheet')).forEach(button => button.addEventListener('click', () => { provider = button.dataset.provider; $$('.provider-button', $('#sheet')).forEach(node => node.classList.toggle('active', node === button)); }));
    $('[data-close]', $('#sheet')).addEventListener('click', closeSheet);
    $('#topup-form').addEventListener('submit', async event => {
      event.preventDefault(); const amount = Number($('#topup-amount').value); if (!(amount > 0)) return toast('Введите сумму пополнения');
      const button = $('#topup-submit'); button.disabled = true; button.textContent = 'Создаём счёт…';
      try { const payload = await api('/api/topup', { method: 'POST', body: { amount, provider } }); if (!payload.pay_url) throw new Error('Ссылка оплаты не получена.'); if (tg?.openLink) tg.openLink(payload.pay_url); else window.open(payload.pay_url, '_blank', 'noopener'); closeSheet(); toast('Счёт создан'); }
      catch (error) { button.disabled = false; button.textContent = 'Перейти к оплате'; toast(error.message); }
    });
  }

  async function bootstrap() {
    try {
      const payload = await api('/api/bootstrap');
      state.boot = payload;
      state.activations = Array.isArray(payload.activations) ? payload.activations : [];
      renderHeader(); renderProfile(); renderActivations();
    } catch (error) {
      const message = String(error?.message || '').toLowerCase();
      if (message.includes('initdata') || message.includes('telegram')) showTelegramRequiredPage();
      else setStateCard($('#purchase-status'), 'error', 'Mini App не загрузился', error.message);
    }
  }

  $('#site-form').addEventListener('submit', searchDomains);
  $('#sort-button').addEventListener('click', event => { state.sort = state.sort === 'price' ? 'stock' : 'price'; event.currentTarget.textContent = state.sort === 'price' ? 'Цена ↑' : 'Остаток ↓'; renderDomains(); });
  $$('.nav-button').forEach(button => button.addEventListener('click', () => switchScreen(button.dataset.screen)));
  $$('.filter-tab').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; $$('.filter-tab').forEach(node => node.classList.toggle('active', node === button)); renderActivations(); }));
  $('#refresh-activations').addEventListener('click', () => loadActivations());
  $('#balance-button').addEventListener('click', openTopup);
  $('#profile-topup-button').addEventListener('click', openTopup);
  $('[data-close]', $('#overlay')).addEventListener('click', closeSheet);
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearPolling(); else if (state.detailId) schedulePoll(state.detailId); });
  bootstrap();
})();
