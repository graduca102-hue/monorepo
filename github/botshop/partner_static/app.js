(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  const root = document.getElementById('app');
  const base = location.pathname.replace(/\/$/, '');
  let query = location.search;
  let data = null;
  let tab = 'overview';
  let period = 'today';
  const money = (value) => `${Number(value || 0).toFixed(2)} USDT`;
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
  const getTelegramInitData = () => {
    const sdkValue = String(window.Telegram?.WebApp?.initData || '').trim();
    if (sdkValue) return sdkValue;
    const hashValue = new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData');
    if (hashValue) return hashValue;
    const queryValue = new URLSearchParams(location.search).get('tgWebAppData');
    if (queryValue) return queryValue;
    try {
      return String(sessionStorage.getItem('tgWebAppData') || '').trim();
    } catch (_) {
      return '';
    }
  };
  const waitForTelegramInitData = async (timeoutMs = 1500) => {
    const startedAt = Date.now();
    let initData = getTelegramInitData();
    while (!initData && Date.now() - startedAt < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      initData = getTelegramInitData();
    }
    return initData;
  };
  const createBotForm = () => `<section class="card form create-bot-form"><div class="card-kicker">ПОДКЛЮЧЕНИЕ БОТА</div><label for="create-bot-token">Токен от @BotFather</label><input id="create-bot-token" type="password" autocomplete="off" placeholder="123456:ABC..."><small class="muted">Вставьте токен — бот проверится и сразу запустится.</small><button class="save full" data-submit-create-bot>Подключить бота</button><div class="hint create-bot-status" data-create-status aria-live="polite"></div></section>`;
  const createBotPage = () => `${header()}<button class="back-link" data-tab="bots">← Назад к ботам</button><section class="page-title"><div class="eyebrow">ПОДКЛЮЧЕНИЕ</div><h1>Создать бота</h1><p>Введите токен от @BotFather — после проверки бот сразу активируется.</p></section>${createBotForm()}`;
  const productPreviewNames = ['ref-05.png', 'ref-04.png', 'ref-01.png', 'ref-02.png', 'ref-03.png'];
  const productCarousel = () => `<section class="product-carousel" data-product-carousel><button class="carousel-arrow" data-carousel-prev aria-label="Предыдущее изображение">‹</button><div class="product-stage">${productPreviewNames.map((name, i) => `<img class="product-preview" data-product-card src="${base}/reference/bots/${name}" alt="Скриншот магазина ${i + 1}"${i ? ' hidden' : ''}>`).join('')}</div><button class="carousel-arrow" data-carousel-next aria-label="Следующее изображение">›</button><div class="product-dots" data-carousel-dots>${productPreviewNames.map((_, i) => `<i${i ? '' : ' class="active"'}></i>`).join('')}</div></section>`;
  const api = async (path, options = {}) => {
    const initData = await waitForTelegramInitData();
    const response = await fetch(`${base}${path}${query}`, {...options, headers: {'Content-Type':'application/json','X-Telegram-Init-Data':initData, ...(options.headers || {})}});
    const responseText = await response.text();
    let payload = {};
    try { payload = responseText ? JSON.parse(responseText) : {}; } catch (_) {}
    if (!response.ok) {
      const error = Error(payload.error || `Ошибка запроса (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  };
  const header = () => `<header class="top"><div class="brand"><div class="logo">P</div><div><div class="name">Партнёрский кабинет</div><div class="eyebrow">${data.bot?.username ? `@${esc(data.bot.username)}` : 'Бот не создан'}</div></div></div><button class="balance" data-tab="${data.has_bot === false ? 'bots' : 'withdraw'}"><small>${data.has_bot === false ? 'подключение' : 'доступно'}</small><b>${data.has_bot === false ? 'Создать' : `${money(data.available_balance)} <span>→</span>`}</b></button></header>`;
  const nav = () => `<nav class="bottom">${[['overview','🏠','Главная'],['bots','🤖','Боты'],['profile','👤','Профиль']].map(([key,icon,label]) => `<button class="${tab === key ? 'active' : ''}" data-tab="${key}"><strong>${icon}</strong><span>${label}</span></button>`).join('')}</nav>`;
  const overview = () => `${header()}<section class="page-title"><div class="eyebrow">ПАРТНЁРСКАЯ ПРОГРАММА</div><h1>Кабинет</h1><p>Управляйте подключением и следите за результатами.</p></section><div class="card connection"><div class="card-kicker">ПОДКЛЮЧЁННЫЙ БОТ</div><div class="connection-row"><div><b>@${esc(data.bot.username)}</b><span class="muted">Ваш партнёрский бот</span></div><span class="status"><i></i>${data.bot.active ? 'Активен' : 'Приостановлен'}</span></div></div><div class="card balance-card"><div class="card-kicker">ДОСТУПНО К ВЫВОДУ</div><b class="balance-big">${money(data.available_balance)}</b><button class="save full" data-tab="withdraw">Вывести средства</button></div><div class="card chart-card"><div class="chart-head"><span>Заработок по дням</span><button data-tab="stats">Подробнее →</button></div><div class="chart-empty"><span>Пока нет данных</span></div></div><div class="metrics"><div class="card metric"><span>Заработано</span><b>${money(data.earned)}</b></div><div class="card metric"><span>Переходов</span><b>${data.stats.total_users || 0}</b></div><div class="card metric"><span>Оборот</span><b>${money(data.stats.delivered_revenue)}</b></div><div class="card metric"><span>К выводу</span><b>${money(data.available_balance)}</b></div></div><h2>Разделы кабинета</h2><div class="actions"><button class="action" data-tab="referral"><span class="action-icon">↗</span><span class="action-copy"><b>Реферальная ссылка</b><small>Приглашайте участников</small></span><span class="chevron">›</span></button><button class="action" data-tab="markup"><span class="action-icon">%</span><span class="action-copy"><b>Наценка</b><small>Сколько добавляет к цене</small></span><span class="chevron">›</span></button><button class="action" data-tab="broadcast"><span class="action-icon">✈</span><span class="action-copy"><b>Рассылка</b><small>Сообщение участникам</small></span><span class="chevron">›</span></button><button class="action" data-tab="subscription"><span class="action-icon">◉</span><span class="action-copy"><b>Канал подписки</b><small>Настройка доступа</small></span><span class="chevron">›</span></button><button class="action" data-tab="stats"><span class="action-icon">▥</span><span class="action-copy"><b>Статистика</b><small>Переходы и начисления</small></span><span class="chevron">›</span></button><button class="action" data-tab="withdraw"><span class="action-icon">$</span><span class="action-copy"><b>Вывод средств</b><small>Заявка на выплату</small></span><span class="chevron">›</span></button></div><div class="card bot-state"><div><b>Состояние бота</b><small>${data.bot.active ? 'Подключение активно' : 'Подключение приостановлено'}</small></div><button class="toggle-button ${data.bot.active ? 'on' : ''}" data-action="toggle" aria-label="Изменить состояние"><i></i></button></div>`;
  const emptyOverview = () => `${header()}<section class="empty-bot-screen"><div class="empty-bot-icon">🤖</div><div class="eyebrow">ПАРТНЁРСКИЙ КАБИНЕТ</div><h1>У вас не создан бот</h1><p>Создайте партнёрского бота, чтобы открыть каталог, принимать клиентов и управлять настройками.</p><button class="save full create-bot-button" data-open-bots>Создать</button><button class="ghost-button" data-tab="bots">Посмотреть решения</button></section>`;
  const bots = () => { const items = Array.isArray(data.bots) ? data.bots : []; return `${header()}<section class="page-title"><div class="eyebrow">ПОДКЛЮЧЕНИЯ</div><h1>Боты</h1></section><h2>Мои боты</h2><div class="bot-list">${items.length ? items.map((item) => { const username = String(item.username || ''); const letter = esc(username.replace(/^@/, '').slice(0, 1).toUpperCase() || 'B'); return `<button class="bot-card ${item.selected ? 'selected' : ''}" data-open-bot="${esc(username)}"><span class="bot-avatar">${letter}</span><span class="bot-copy"><b>${esc(username ? `@${username}` : 'Без имени')}</b><small>${item.users || 0} пользователей · ${money(item.earned || 0)}</small></span><span class="bot-status ${item.active ? 'active' : ''}">${item.active ? 'Активен' : 'Пауза'}</span><span class="chevron">›</span></button>`; }).join('') : '<div class="empty-state"><b>Пока нет подключённых ботов</b><span>Выберите готовое решение ниже.</span></div>'}</div><h2>Магазины</h2><div class="solution-carousel"><button class="solution-card" data-tab="universal_store"><span class="solution-icon">🛍️</span><span class="solution-copy"><b>Универсальный магазин</b><small>Магазин цифровых товаров с автовыдачей</small></span><span class="chevron">›</span></button><button class="solution-card create-solution" data-create-bot><span class="solution-copy"><b>Создать бота</b><small>Подключить новый бот к партнёрской программе</small></span><span class="chevron">›</span></button></div>`; };
  const universalStore = () => `${header()}<button class="back-link" data-tab="bots">← Назад к ботам</button><div class="store-switcher solution-carousel"><button class="solution-card store-current" data-tab="universal_store"><span class="solution-icon">🛍️</span><span class="solution-copy"><b>Универсальный магазин</b><small>Цифровые товары с автовыдачей</small></span><span class="chevron">›</span></button><button class="solution-card create-solution" data-create-bot><span class="solution-copy"><b>Создать бота</b><small>Подключить новый магазин</small></span><span class="chevron">›</span></button></div><section class="solution-hero"><div class="solution-hero-icon">🛍️</div><div class="eyebrow">ГОТОВОЕ РЕШЕНИЕ</div><h1>Универсальный магазин</h1><p>Telegram-бот для продажи цифровых товаров с автоматической выдачей после оплаты.</p></section><div class="card feature-list"><div class="feature-item"><span>⚡</span><div><b>Автоматическая выдача</b><small>Покупатель получает товар сразу после успешной оплаты.</small></div></div><div class="feature-item"><span>▤</span><div><b>Готовый каталог</b><small>Аккаунты, почта, VPN, прокси, SMS и другие цифровые товары.</small></div></div><div class="feature-item"><span>₽</span><div><b>Собственная наценка</b><small>Вы самостоятельно задаёте дополнительный процент к цене.</small></div></div><div class="feature-item"><span>◉</span><div><b>Управление из кабинета</b><small>Статистика, рассылки, подписка и состояние бота находятся в Mini App.</small></div></div></div><div class="card setup-steps"><h3>Как подключить</h3><div><b>1</b><span>Создайте нового бота через @BotFather.</span></div><div><b>2</b><span>Скопируйте токен и отправьте его в SousPartnersBot.</span></div><div><b>3</b><span>Настройте наценку — бот готов принимать клиентов.</span></div></div><button class="save full create-bot-button" data-create-bot>Создать бота</button>`;
  const stats = () => { const summary = data.stats || {}; return `${header()}<section class="page-title"><div class="eyebrow">АНАЛИТИКА</div><h1>Статистика</h1><p>Результаты подключённого бота.</p></section><div class="period">${[['today','Сегодня'],['7d','7 дней'],['30d','30 дней']].map(([key,label]) => `<button class="${period === key ? 'active' : ''}" data-period="${key}">${label}</button>`).join('')}</div><div class="stats-list"><div class="row"><span>Начислено</span><b>${money(summary.profit)}</b></div><div class="row"><span>Переходов</span><b>${summary.total_users || 0}</b></div><div class="row"><span>Оборот</span><b>${money(summary.delivered_revenue)}</b></div><div class="row"><span>Подключение</span><b>${data.bot.active ? 'Активно' : 'Пауза'}</b></div></div>`; };
  const settings = () => { const referral = data.referral || {}; const subscription = data.subscription || {}; const franchise = data.franchise || {}; const buttonVisible = franchise.create_button_visible !== false; return `${header()}<section class="page-title"><div class="eyebrow">НАСТРОЙКИ</div><h1>Партнёрская программа</h1><p>Настройте процент начисления и доступ участников.</p></section><div class="card form"><label>Кнопка приглашения в боте</label><p class="muted">Показывает пользователям переход в SousPartners для создания своего партнёрского бота.</p><button class="save" data-config="franchise_hide_create" data-value="${buttonVisible ? 0 : 1}">${buttonVisible ? 'Скрыть кнопку' : 'Показать кнопку'}</button></div><div class="card form"><label>Процент партнёра</label><input id="ref-percent" type="number" min="0" max="100" value="${Number(referral.percent || 0)}"><button class="save" data-config="referral_percent">Сохранить процент</button></div><div class="card form"><label>Канал подписки</label><input id="sub-url" value="${esc(subscription.channel_url || '')}" placeholder="https://t.me/channel"><button class="save" data-config="subscription_channel_url">Сохранить</button></div><div class="card form"><label>Сообщение участникам</label><textarea id="broadcast-text" rows="4" placeholder="Текст сообщения"></textarea><button class="save" data-broadcast>Отправить</button></div>`; };
  const back = () => `<button class="back-link" data-tab="overview">← Назад в кабинет</button>`;
  const referral = () => { const item = data.referral || {}; return `${header()}${back()}<section class="page-title"><div class="eyebrow">ПРИГЛАШЕНИЯ</div><h1>Реферальная ссылка</h1><p>Приглашайте пользователей в своего партнёрского бота.</p></section><div class="card form"><label>Ваша ссылка</label><div class="copy-line"><input value="${esc(item.link || '')}" readonly><button class="copy" data-copy="${esc(item.link || '')}">Копировать</button></div><div class="hint">Приглашено участников: <b>${data.stats?.total_users || 0}</b></div></div><div class="card conditions"><h3>Условия программы</h3><p>• Приглашайте владельцев Telegram-ботов по этой ссылке.</p><p>• Если владелец подключает бота после перехода по вашей ссылке, он закрепляется за вами.</p><p>• Вы получаете <b>15%</b> от фактически заработанной им наценки.</p><p>• Начисление происходит автоматически после выполнения заказа.</p><p>• Оборот, пополнения и заказы без вашей ссылки не учитываются.</p><p>• Вывод реферального дохода доступен от <b>5$</b>.</p></div>`; };
  const markup = () => { const values = data.markups || {}; return `${header()}${back()}<section class="page-title"><div class="eyebrow">НАСТРОЙКИ</div><h1>Наценка</h1><p>Отдельно задайте дополнительную наценку для каждой категории.</p></section><div class="card form"><label>Цифровые товары, %</label><input class="markup-input" data-kind="goods" type="number" min="0" max="500" value="${Number(values.goods || 0)}"><button class="save" data-markup="goods">Сохранить</button></div><div class="card form"><label>Прокси, %</label><input class="markup-input" data-kind="proxy" type="number" min="0" max="500" value="${Number(values.proxy || 0)}"><button class="save" data-markup="proxy">Сохранить</button></div><div class="card form"><label>SMS, %</label><input class="markup-input" data-kind="sms" type="number" min="0" max="500" value="${Number(values.sms || 0)}"><button class="save" data-markup="sms">Сохранить</button></div>`; };
  const broadcast = () => `${header()}${back()}<section class="page-title"><div class="eyebrow">СВЯЗЬ С УЧАСТНИКАМИ</div><h1>Рассылка</h1><p>Отправьте сообщение пользователям вашего бота.</p></section><div class="card form"><label>Текст сообщения</label><textarea id="broadcast-text" rows="8" maxlength="4000" placeholder="Введите сообщение"></textarea><div class="hint">Получателей: <b>${data.stats?.total_users || 0}</b></div><button class="save" data-broadcast>Отправить сообщение</button></div>`;
  const subscription = () => { const item = data.subscription || {}; return `${header()}${back()}<section class="page-title"><div class="eyebrow">ДОСТУП</div><h1>Канал подписки</h1><p>Попросите пользователя подписаться на канал перед началом работы.</p></section><div class="card form"><label>Ссылка на канал</label><input id="sub-url" value="${esc(item.channel_url || '')}" placeholder="https://t.me/channel"><button class="save" data-config="subscription_channel_url">Сохранить ссылку</button></div><div class="card form"><label>Статус проверки подписки</label><button class="save" data-config="subscription_enabled" data-value="${item.enabled ? 0 : 1}">${item.enabled ? 'Выключить проверку' : 'Включить проверку'}</button></div>`; };
  const withdraw = () => { const minimum = Number(data.payout_minimum || 5); return `${header()}<section class="page-title"><div class="eyebrow">ВЫПЛАТЫ</div><h1>Вывод средств</h1><p>Создайте заявку на выплату доступного баланса.</p></section><div class="card form"><div class="method"><b>CryptoBot</b><small>USDT · после проверки заявки</small></div><label>Сумма, USDT</label><input id="payout-amount" type="number" min="${minimum}" step="0.01" max="${Number(data.available_balance || 0)}" placeholder="${minimum.toFixed(2)}"><label>Ваш CryptoBot username или ID</label><input id="payout-destination" placeholder="@username"><button class="save" data-payout>Создать заявку</button></div><div class="notice">Минимальная сумма выплаты — ${minimum.toFixed(2)} USDT.</div>`; };
  const profile = () => `${header()}<section class="page-title"><div class="eyebrow">ПРОФИЛЬ</div><h1>Профиль</h1></section><div class="card profile-card"><div class="profile-row"><span>Подключённый бот</span><b>@${esc(data.bot.username)}</b></div><div class="profile-row"><span>Статус</span><b>${data.bot.active ? 'Активен' : 'Приостановлен'}</b></div><div class="profile-row"><span>ID владельца</span><b>${data.bot.owner_id}</b></div></div>`;
  function bind() {
    // Creating a bot belongs to the Universal Shop solution, not to the
    // general bot list. Remove the legacy carousel card everywhere.
    root.querySelectorAll('.create-solution, [data-create-entry]').forEach((element) => element.remove());
    if (tab === 'universal_store') {
      const switcher = root.querySelector('.store-switcher');
      if (switcher) switcher.outerHTML = productCarousel();
      const carousel = root.querySelector('[data-product-carousel]');
      if (carousel) {
        if (!document.getElementById('product-carousel-style')) {
          const style = document.createElement('style');
          style.id = 'product-carousel-style';
          style.textContent = '.product-carousel{display:grid;grid-template-columns:34px minmax(0,1fr) 34px;gap:7px;align-items:center;margin:3px 0 15px}.product-stage{min-width:0}.product-preview{display:block;width:100%;height:auto;border-radius:18px}.product-preview[hidden]{display:none}.carousel-arrow{width:34px;height:48px;border:1px solid #4b6076;border-radius:17px;background:#26323f;color:#dbe6f0;font-size:30px;line-height:1;display:grid;place-items:center;padding:0}.carousel-arrow:active{transform:scale(.94);background:#3b5068}.product-dots{grid-column:1/-1;display:flex;justify-content:center;gap:5px;margin-top:1px}.product-dots i{width:6px;height:6px;border-radius:50%;background:#465260}.product-dots i.active{width:18px;border-radius:5px;background:#6d99d0}';
          document.head.appendChild(style);
        }
        const cards = Array.from(carousel.querySelectorAll('[data-product-card]'));
        const dots = Array.from(carousel.querySelectorAll('[data-carousel-dots] i'));
        let index = 0;
        const showCard = (next) => { index = (next + cards.length) % cards.length; cards.forEach((card, i) => { card.hidden = i !== index; }); dots.forEach((dot, i) => dot.classList.toggle('active', i === index)); };
        carousel.querySelector('[data-carousel-prev]')?.addEventListener('click', () => showCard(index - 1));
        carousel.querySelector('[data-carousel-next]')?.addEventListener('click', () => showCard(index + 1));
        dots.forEach((dot, i) => dot.addEventListener('click', () => showCard(i)));
        showCard(0);
      }
    }
    root.querySelectorAll('[data-tab]').forEach((element) => element.onclick = () => { tab = element.dataset.tab; render(); });
    root.querySelectorAll('[data-period]').forEach((element) => element.onclick = () => { period = element.dataset.period; render(); });
    root.querySelectorAll('[data-open-bot]').forEach((element) => element.onclick = async () => { const username = String(element.dataset.openBot || '').trim(); if (!username) return; const next = new URL(location.href); next.searchParams.set('cabinet_bot', username); history.replaceState({}, '', next.toString()); query = next.search; try { data = await api('/api/dashboard'); tab = 'overview'; render(); } catch (error) { tg?.showAlert?.(error.message); } });
    root.querySelectorAll('[data-open-bots]').forEach((element) => element.onclick = () => { tab = 'bots'; render(); });
    root.querySelectorAll('[data-create-bot]').forEach((element) => element.onclick = () => { tab = 'create_bot'; render(); });
    root.querySelectorAll('[data-submit-create-bot]').forEach((element) => element.onclick = async () => {
      const input = root.querySelector('#create-bot-token');
      const status = root.querySelector('[data-create-status]');
      const token = String(input?.value || '').trim();
      if (!token) { if (status) status.textContent = 'Введите токен бота.'; return; }
      element.disabled = true;
      if (status) status.textContent = 'Проверяем токен…';
      try {
        const result = await api('/api/create-bot', {method:'POST', body:JSON.stringify({token})});
        const username = String(result.bot?.username || '').trim();
        if (username) { const next = new URL(location.href); next.searchParams.set('cabinet_bot', username); history.replaceState({}, '', next.toString()); query = next.search; }
        data = await api('/api/dashboard');
        tab = 'overview';
        render();
      } catch (error) {
        if (status) status.textContent = error.message || 'Не удалось подключить бота.';
        element.disabled = false;
      }
    });
    root.querySelectorAll('[data-copy]').forEach((element) => element.onclick = async () => { await navigator.clipboard?.writeText(element.dataset.copy || ''); tg?.showAlert?.('Ссылка скопирована'); });
    root.querySelectorAll('[data-action="toggle"]').forEach((element) => element.onclick = async () => { try { const result = await api('/api/action', {method:'POST', body:JSON.stringify({action:'toggle'})}); data.bot.active = result.active; render(); } catch (error) { tg?.showAlert?.(error.message); } });
    root.querySelectorAll('[data-payout]').forEach((element) => element.onclick = async () => { try { const result = await api('/api/payout', {method:'POST', body:JSON.stringify({amount:Number(document.getElementById('payout-amount').value), destination:document.getElementById('payout-destination').value})}); tg?.showAlert?.(`Заявка №${result.request_id} создана`); tab = 'overview'; render(); } catch (error) { tg?.showAlert?.(error.message); } });
    root.querySelectorAll('[data-broadcast]').forEach((element) => element.onclick = async () => { try { const result = await api('/api/broadcast', {method:'POST', body:JSON.stringify({text:document.getElementById('broadcast-text').value})}); tg?.showAlert?.(`Отправлено: ${result.sent}`); document.getElementById('broadcast-text').value = ''; } catch (error) { tg?.showAlert?.(error.message); } });
    root.querySelectorAll('[data-markup]').forEach((element) => element.onclick = async () => { const kind = element.dataset.markup; const input = root.querySelector(`[data-kind="${kind}"]`); try { const result = await api('/api/settings', {method:'POST', body:JSON.stringify({kind, value:Number(input.value)})}); data.markups = data.markups || {}; data.markups[kind] = result.value; tg?.showAlert?.('Наценка сохранена'); } catch (error) { tg?.showAlert?.(error.message); } });
    root.querySelectorAll('[data-config]').forEach((element) => element.onclick = async () => { const field = element.dataset.config; const input = element.dataset.value !== undefined ? element.dataset.value : document.getElementById(field === 'referral_percent' ? 'ref-percent' : 'sub-url').value; try { await api('/api/config', {method:'POST', body:JSON.stringify({field, value:input})}); if (field === 'franchise_hide_create') { data.franchise = data.franchise || {}; data.franchise.create_button_visible = Number(input) !== 1; } if (field === 'subscription_enabled') { data.subscription = data.subscription || {}; data.subscription.enabled = Number(input) === 1; } if (field === 'subscription_channel_url') { data.subscription = data.subscription || {}; data.subscription.channel_url = input; } tg?.showAlert?.('Сохранено'); render(); } catch (error) { tg?.showAlert?.(error.message); } });
  }
  function render() { root.innerHTML = `<div class="shell">${tab === 'overview' ? (data.has_bot === false ? emptyOverview() : overview()) : tab === 'bots' ? bots() : tab === 'create_bot' ? createBotPage() : tab === 'universal_store' ? universalStore() : tab === 'stats' ? stats() : tab === 'withdraw' ? withdraw() : tab === 'referral' ? referral() : tab === 'markup' ? markup() : tab === 'broadcast' ? broadcast() : tab === 'subscription' ? subscription() : tab === 'settings' ? settings() : profile()}</div>${nav()}`; bind(); }
  const loadDashboard = async () => {
    try {
      data = await api('/api/dashboard');
      render();
    } catch (error) {
      const telegramUrl = 'https://t.me/SousPartnersBot?start=cabinet';
      const hasTelegramData = Boolean(getTelegramInitData());
      if (!hasTelegramData || error.status === 401) {
        root.innerHTML = `<div class="auth-screen"><div class="auth-icon">✈</div><h1>Перезапустите кабинет</h1><p>Telegram не передал данные сессии. Вернитесь в SousPartnersBot и снова нажмите «Кабинет».</p><button class="save auth-button" data-retry-dashboard>Повторить</button><a class="save auth-button" href="${telegramUrl}">Открыть SousPartnersBot</a></div>`;
      } else {
        root.innerHTML = `<div class="auth-screen"><div class="auth-icon">↻</div><h1>Не удалось загрузить кабинет</h1><p>${esc(error.message || 'Временная ошибка соединения.')}</p><button class="save auth-button" data-retry-dashboard>Повторить</button></div>`;
      }
      root.querySelector('[data-retry-dashboard]')?.addEventListener('click', loadDashboard);
    }
  };
  loadDashboard();
})();
