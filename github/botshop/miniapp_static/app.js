(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  function parseMiniAppContext() {
    const match = window.location.pathname.match(/^\/miniapp\/([A-Za-z0-9_]+)\//);
    const botSlug = match && match[1] ? match[1].toLowerCase() : 'main';
    return {
      botSlug,
      basePath: `/miniapp/${botSlug}`,
    };
  }

  const pathContext = parseMiniAppContext();
  const state = {
    botSlug: pathContext.botSlug,
    basePath: pathContext.basePath,
    initData: tg ? tg.initData || '' : '',
    language: normalizeLanguageCode(tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.language_code : 'ru'),
    boot: null,
    sections: [],
    categories: [],
    selectedCategoryId: null,
    productsPage: 1,
    expandedSectionIds: [],
    rawProductItems: [],
    productItems: [],
    productMeta: null,
    cartItems: [],
    balanceCheckoutInFlight: false,
    sortMode: 'popular',
    searchQuery: '',
    favoritesOnly: false,
    searchTimer: null,
    pendingFilters: {
      priceMin: '',
      priceMax: '',
      attributeTokens: [],
    },
    activeFilters: {
      priceMin: '',
      priceMax: '',
      attributeTokens: [],
    },
    filtersExpanded: false,
    orderWaitPoll: null,
    orderWaitPollId: 0,
  };
  const ORDER_WAIT_POLL_INTERVAL_MS = 500;

  const DEFAULT_SUPPORT_URL = 'https://t.me/UniversallSupportBot?start=market';

  function normalizeLanguageCode(value) {
    const normalized = String(value || '').trim().toLowerCase();
    return normalized === 'en' ? 'en' : 'ru';
  }

  const UI_COPY = {
    ru: {
      document_title: 'SOUS MARKET',
      balance_label: 'Баланс',
      catalog_kicker: 'Каталог',
      catalog_title: 'Каталог',
      catalog_intro: 'Цена и остаток видны до покупки.',
      proof_delivery: 'Выдача после оплаты',
      proof_balance: 'Оплата с баланса',
      proof_support: 'Поддержка',
      catalog_category: 'Категория',
      catalog_choose_category: 'Выберите категорию',
      catalog_price_from: 'Цена от',
      catalog_price_to: 'Цена до',
      catalog_tags: 'Теги',
      catalog_apply: 'Применить',
      catalog_reset: 'Сбросить',
      catalog_results_idle: 'Выберите категорию, чтобы увидеть товары.',
      catalog_loading: 'Загрузка каталога…',
      catalog_filter_aria: 'Открыть фильтр',
      catalog_filter_title: 'Фильтр',
      catalog_sort_popular: 'Сначала популярные',
      catalog_sort_cheap: 'Сначала дешёвые',
      catalog_sort_expensive: 'Сначала дорогие',
      catalog_sort_stock: 'По остатку',
      catalog_empty_pick: 'Выберите категорию',
      catalog_empty_products: 'Выберите категорию, чтобы увидеть доступные товары.',
      catalog_empty_category: 'В этой категории пока нет доступных товаров.',
      catalog_category_not_selected: 'Категория ещё не выбрана.',
      catalog_categories_unavailable: 'Категории пока недоступны.',
      catalog_load_failed: 'Не удалось загрузить каталог.',
      catalog_loading_products: 'Загрузка товаров…',
      catalog_load_products_failed: 'Не удалось загрузить товары.',
      catalog_load_category_failed: 'Не удалось загрузить товары этой категории.',
      orders_kicker: 'Заказы',
      orders_title: 'Заказы',
      orders_loading: 'Загрузка заказов…',
      orders_empty_title: 'Пусто',
      orders_empty_text: 'Пока нет покупок',
      orders_status_idle: 'Здесь будут появляться текущие и завершённые заказы.',
      order_label: 'Заказ',
      order_title: 'Заказ',
      order_completed: 'Заказ выполнен',
      order_status: 'Статус заказа',
      loading: 'Загрузка…',
      order_load_failed: 'Не удалось загрузить заказ.',
      quantity_short: 'шт.',
      profile_kicker: 'Профиль',
      profile_title: 'Профиль',
      profile_topup: 'Пополнить',
      profile_purchases: 'покупок',
      profile_referral: 'Реферальная программа',
      profile_transactions: 'История операций',
      profile_promocode: 'Активировать промокод',
      profile_support: 'Поддержка',
      profile_rules: 'Правила',
      nav_orders: 'Заказы',
      nav_catalog: 'Каталог',
      nav_profile: 'Профиль',
      cart_open: 'Открыть корзину',
      cart_title: 'Корзина',
    },
    en: {
      document_title: 'SOUS MARKET',
      balance_label: 'Balance',
      catalog_kicker: 'Catalog',
      catalog_title: 'Catalog',
      catalog_intro: 'See the price and stock before purchase.',
      proof_delivery: 'Delivery after payment',
      proof_balance: 'Pay from balance',
      proof_support: 'Support',
      catalog_category: 'Category',
      catalog_choose_category: 'Choose a category',
      catalog_price_from: 'Price from',
      catalog_price_to: 'Price to',
      catalog_tags: 'Tags',
      catalog_apply: 'Apply',
      catalog_reset: 'Reset',
      catalog_results_idle: 'Choose a category to view products.',
      catalog_loading: 'Loading catalog…',
      catalog_filter_aria: 'Open filters',
      catalog_filter_title: 'Filters',
      catalog_sort_popular: 'Most popular first',
      catalog_sort_cheap: 'Lowest price first',
      catalog_sort_expensive: 'Highest price first',
      catalog_sort_stock: 'By stock',
      catalog_empty_pick: 'Choose a category',
      catalog_empty_products: 'Choose a category to view available products.',
      catalog_empty_category: 'There are no available products in this category yet.',
      catalog_category_not_selected: 'No category selected yet.',
      catalog_categories_unavailable: 'Categories are not available yet.',
      catalog_load_failed: 'Failed to load catalog.',
      catalog_loading_products: 'Loading products…',
      catalog_load_products_failed: 'Failed to load products.',
      catalog_load_category_failed: 'Failed to load products for this category.',
      orders_kicker: 'Orders',
      orders_title: 'Orders',
      orders_loading: 'Loading orders…',
      orders_empty_title: 'Empty',
      orders_empty_text: 'No purchases yet',
      orders_status_idle: 'Your current and completed orders will appear here.',
      order_label: 'Order',
      order_title: 'Order',
      order_completed: 'Order completed',
      order_status: 'Order status',
      loading: 'Loading…',
      order_load_failed: 'Failed to load order.',
      quantity_short: 'pcs.',
      profile_kicker: 'Profile',
      profile_title: 'Profile',
      profile_topup: 'Top up',
      profile_purchases: 'purchases',
      profile_referral: 'Referral program',
      profile_transactions: 'Transaction history',
      profile_promocode: 'Activate promo code',
      profile_support: 'Support',
      profile_rules: 'Rules',
      nav_orders: 'Orders',
      nav_catalog: 'Catalog',
      nav_profile: 'Profile',
      cart_open: 'Open cart',
      cart_title: 'Cart',
    },
  };

  function t(key) {
    const copy = UI_COPY[state.language] || UI_COPY.ru;
    return copy[key] || UI_COPY.ru[key] || key;
  }

  const EN_DYNAMIC_REPLACEMENTS = [
    ['Открыть прокси', 'Open proxy'],
    ['Открыть товар', 'Open product'],
    ['Купить', 'Buy'],
    ['Продано', 'Sold'],
    ['Описание появится в карточке товара.', 'Description will appear in the product card.'],
    ['Без описания', 'No description'],
    ['Остаток: ', 'Stock: '],
    ['Продаж: ', 'Sales: '],
    ['Без тегов', 'No tags'],
    ['Покупка', 'Purchase'],
    ['Добавить в корзину', 'Add to cart'],
    ['Открыть корзину', 'Open cart'],
    ['Ожидаем выдачу', 'Waiting for delivery'],
    ['Заказ оформлен и передан в обработку. Подождите пару секунд, затем откройте карточку заказа или это окно ещё раз.', 'The order has been placed and handed over for processing. Wait a few seconds, then reopen the order card or this window.'],
    ['Обновить статус', 'Refresh status'],
    ['Быстрый просмотр', 'Quick preview'],
    ['Скачать данные файлом', 'Download data as file'],
    ['Скачать файлом', 'Download as file'],
    ['Заказы оформлены внутри mini app.', 'Orders were placed inside the mini app.'],
    ['Оформляем заказ...', 'Placing order...'],
    ['Поддержка', 'Support'],
    ['Ссылка на поддержку для этого бота пока не настроена.', 'Support link for this bot is not configured yet.'],
    ['Правила', 'Rules'],
    ['История операций', 'Transaction history'],
    ['Ошибка запуска', 'Startup error'],
    ['Не удалось инициализировать mini app.', 'Failed to initialize the mini app.'],
    ['Пополнить баланс', 'Top up balance'],
    ['Недостаточно средств', 'Insufficient funds'],
    [' / шт.', ' / pc.'],
    ['$/шт.', '$/pc.'],
    [' шт.', ' pcs.'],
  ];

  function replaceDynamicText(text) {
    if (state.language !== 'en') {
      return text;
    }
    return EN_DYNAMIC_REPLACEMENTS.reduce((result, [from, to]) => result.split(from).join(to), String(text || ''));
  }

  function localizeDynamicContent(root) {
    if (state.language !== 'en' || !root) {
      return;
    }
    const textNodes = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      textNodes.push(walker.currentNode);
    }
    textNodes.forEach((node) => {
      const nextValue = replaceDynamicText(node.textContent);
      if (nextValue !== node.textContent) {
        node.textContent = nextValue;
      }
    });
    const applyAttributes = (element) => {
      ['aria-label', 'title', 'placeholder'].forEach((attr) => {
        if (element.hasAttribute && element.hasAttribute(attr)) {
          const currentValue = element.getAttribute(attr) || '';
          const nextValue = replaceDynamicText(currentValue);
          if (nextValue !== currentValue) {
            element.setAttribute(attr, nextValue);
          }
        }
      });
    };
    if (root.nodeType === 1) {
      applyAttributes(root);
      root.querySelectorAll('*').forEach(applyAttributes);
    }
  }

  const brandName = document.getElementById('brand-name');
  const brandBadge = document.getElementById('brand-badge');
  const brandSubtitle = document.getElementById('brand-subtitle');
  const botFooter = document.getElementById('bot-footer');
  const balanceValue = document.getElementById('balance-value');
  const profileOrdersCount = document.getElementById('profile-orders-count');
  const catalogCategories = document.getElementById('catalog-categories');
  const catalogSelectionTitle = document.getElementById('catalog-selection-title');
  const catalogSelectionSubtitle = document.getElementById('catalog-selection-subtitle');
  const catalogFilterToggle = document.getElementById('catalog-filter-toggle');
  const catalogFilterPanel = document.getElementById('catalog-filter-panel');
  const catalogCategoryFilter = document.getElementById('catalog-category-filter');
  const catalogPriceMin = document.getElementById('catalog-price-min');
  const catalogPriceMax = document.getElementById('catalog-price-max');
  const catalogAttributeFilters = document.getElementById('catalog-attribute-filters');
  const catalogApplyFilters = document.getElementById('catalog-apply-filters');
  const catalogResetFilters = document.getElementById('catalog-reset-filters');
  const catalogSortFilter = document.getElementById('catalog-sort-filter');
  const catalogSearch = document.getElementById('catalog-search');
  const catalogFavoritesFilter = document.getElementById('catalog-favorites-filter');
  const catalogTopFavorites = document.getElementById('catalog-top-favorites');
  const catalogCartButton = document.getElementById('catalog-cart-button');
  const catalogCartCount = document.getElementById('catalog-cart-count');
  const catalogResultsMeta = document.getElementById('catalog-results-meta');
  const catalogProducts = document.getElementById('catalog-products');
  const catalogPagination = document.getElementById('catalog-pagination');
  const catalogStatus = document.getElementById('catalog-status');
  const ordersList = document.getElementById('orders-list');
  const ordersStatus = document.getElementById('orders-status');
  const ordersEmpty = document.getElementById('orders-empty');
  const overlay = document.getElementById('overlay');
  const overlayContent = document.getElementById('overlay-content');

  const navButtons = Array.from(document.querySelectorAll('.nav-button'));
  const screens = Array.from(document.querySelectorAll('.screen'));
  let bootstrapRefreshInFlight = null;

  function applyStaticTranslations() {
    document.documentElement.lang = state.language;
    document.title = t('document_title');

    const balanceLabel = document.querySelector('.balance-label');
    if (balanceLabel) balanceLabel.textContent = t('balance_label');

    const catalogKicker = document.querySelector('#screen-catalog .section-kicker');
    const catalogTitle = document.querySelector('#screen-catalog h2');
    const catalogIntro = document.getElementById('catalog-intro');
    if (catalogKicker) catalogKicker.textContent = t('catalog_kicker');
    if (catalogTitle) catalogTitle.textContent = t('catalog_title');
    if (catalogIntro) catalogIntro.textContent = t('catalog_intro');
    const proofDelivery = document.getElementById('proof-delivery');
    const proofBalance = document.getElementById('proof-balance');
    const proofSupport = document.getElementById('proof-support');
    if (proofDelivery) proofDelivery.textContent = t('proof_delivery');
    if (proofBalance) proofBalance.textContent = t('proof_balance');
    if (proofSupport) proofSupport.textContent = t('proof_support');
    if (catalogSelectionTitle) catalogSelectionTitle.textContent = t('catalog_category');
    const categoryPlaceholder = catalogCategoryFilter ? catalogCategoryFilter.querySelector('option[value=""]') : null;
    if (categoryPlaceholder) categoryPlaceholder.textContent = t('catalog_choose_category');
    const filterLabels = Array.from(document.querySelectorAll('#catalog-filter-panel .field-label span'));
    if (filterLabels[0]) filterLabels[0].textContent = t('catalog_category');
    if (filterLabels[1]) filterLabels[1].textContent = t('catalog_price_from');
    if (filterLabels[2]) filterLabels[2].textContent = t('catalog_price_to');
    if (filterLabels[3]) filterLabels[3].textContent = t('catalog_tags');
    if (catalogApplyFilters) catalogApplyFilters.textContent = t('catalog_apply');
    if (catalogResetFilters) catalogResetFilters.textContent = t('catalog_reset');
    if (catalogResultsMeta && !state.selectedCategoryId) catalogResultsMeta.textContent = t('catalog_results_idle');
    if (catalogStatus && !catalogStatus.textContent.trim()) catalogStatus.textContent = t('catalog_loading');
    if (catalogFilterToggle) {
      catalogFilterToggle.setAttribute('aria-label', t('catalog_filter_aria'));
      catalogFilterToggle.setAttribute('title', t('catalog_filter_title'));
    }
    if (catalogSortFilter) {
      const sortKeys = {
        popular: 'catalog_sort_popular',
        cheap: 'catalog_sort_cheap',
        expensive: 'catalog_sort_expensive',
        stock: 'catalog_sort_stock',
      };
      Array.from(catalogSortFilter.options).forEach((option) => {
        option.textContent = t(sortKeys[option.value] || option.value);
      });
    }

    const ordersKicker = document.querySelector('#screen-orders .section-kicker');
    const ordersTitle = document.querySelector('#screen-orders h2');
    if (ordersKicker) ordersKicker.textContent = t('orders_kicker');
    if (ordersTitle) ordersTitle.textContent = t('orders_title');
    if (ordersStatus && !ordersList.children.length) ordersStatus.textContent = t('orders_loading');
    const emptyTitle = ordersEmpty ? ordersEmpty.querySelector('h3') : null;
    const emptyText = ordersEmpty ? ordersEmpty.querySelector('p') : null;
    if (emptyTitle) emptyTitle.textContent = t('orders_empty_title');
    if (emptyText) emptyText.textContent = t('orders_empty_text');

    const profileKicker = document.querySelector('#screen-profile .section-kicker');
    const profileTitle = document.querySelector('#screen-profile h2');
    if (profileKicker) profileKicker.textContent = t('profile_kicker');
    if (profileTitle) profileTitle.textContent = t('profile_title');
    const profileTopup = document.getElementById('profile-topup');
    if (profileTopup) profileTopup.textContent = t('profile_topup');
    const profileStatsLabel = document.querySelector('#screen-profile .profile-stats span');
    if (profileStatsLabel) profileStatsLabel.textContent = t('profile_purchases');
    const profileMenuLabels = Array.from(document.querySelectorAll('#screen-profile .menu-text'));
    const profileMenuKeys = ['profile_referral', 'profile_transactions', 'profile_promocode', 'profile_support', 'profile_rules'];
    profileMenuLabels.forEach((node, index) => {
      const key = profileMenuKeys[index];
      if (key) node.textContent = t(key);
    });

    navButtons.forEach((button) => {
      const label = button.querySelector('.nav-label');
      if (label) label.textContent = t(`nav_${button.dataset.screen}`);
    });
    if (catalogCartButton) {
      catalogCartButton.setAttribute('aria-label', t('cart_open'));
      catalogCartButton.setAttribute('title', t('cart_title'));
    }
    localizeDynamicContent(document.body);
  }

  applyStaticTranslations();

  function switchScreen(screenId) {
    screens.forEach((screen) => {
      screen.classList.toggle('screen-active', screen.id === `screen-${screenId}`);
    });
    navButtons.forEach((button) => {
      button.classList.toggle('nav-button-active', button.dataset.screen === screenId);
    });
  }

  navButtons.forEach((button) => {
    button.addEventListener('click', () => switchScreen(button.dataset.screen));
  });

  function openOverlay(html) {
    clearOrderWaitPoll();
    overlayContent.innerHTML = html;
    localizeDynamicContent(overlayContent);
    overlay.classList.remove('hidden');
  }

  function closeOverlay() {
    clearOrderWaitPoll();
    overlay.classList.add('hidden');
    overlayContent.innerHTML = '';
  }

  document.getElementById('overlay-close').addEventListener('click', closeOverlay);
  overlay.querySelector('.overlay-backdrop').addEventListener('click', closeOverlay);

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

  function deepClone(payload) {
    return JSON.parse(JSON.stringify(payload));
  }

  function parseMoneyInput(value) {
    const normalized = String(value || '')
      .trim()
      .replace(',', '.')
      .replace(/\s+/g, '');
    if (!normalized) {
      return NaN;
    }
    const sanitized = normalized.replace(/[^0-9.]/g, '');
    if (!sanitized || sanitized === '.') {
      return NaN;
    }
    const firstDotIndex = sanitized.indexOf('.');
    const canonical = firstDotIndex === -1
      ? sanitized
      : `${sanitized.slice(0, firstDotIndex + 1)}${sanitized.slice(firstDotIndex + 1).replace(/\./g, '')}`;
    return Number(canonical);
  }

  function formatBrandNameFromSlug(botSlug) {
    if (!botSlug || botSlug === 'main') {
      return 'SOUS MARKET';
    }

    const normalized = String(botSlug)
      .replace(/^@+/, '')
      .replace(/bot$/i, '')
      .replace(/([a-zа-я])([A-ZА-Я])/g, '$1 $2')
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    return normalized ? normalized.toUpperCase() : 'SOUS MARKET';
  }

  function clearOrderWaitPoll() {
    if (state.orderWaitPoll) {
      window.clearTimeout(state.orderWaitPoll);
      state.orderWaitPoll = null;
    }
    state.orderWaitPollId += 1;
  }

  function isAwaitingDelivery(order) {
    return Boolean(order && order.is_account_order && !order.can_download && ['paid', 'delivery_pending'].includes(order.status));
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
          <p>Как только товар будет готов, откроем меню.</p>
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
        const payload = await api(`${state.basePath}/api/orders/${encodeURIComponent(orderId)}/refresh`, { method: 'POST' });
        if (payload && payload.profile && state.boot) {
          state.boot.profile = payload.profile;
          renderBootstrap();
        }

        const order = payload && payload.order ? payload.order : null;
        if (order && !isAwaitingDelivery(order)) {
          clearOrderWaitPoll();
          await refreshBootstrapSilently();
          onReady(order);
          return;
        }
      } catch (_error) {
        // Keep waiting and retry silently.
      }

      if (pollId !== state.orderWaitPollId || overlay.classList.contains('hidden')) {
        return;
      }
      state.orderWaitPoll = window.setTimeout(tick, ORDER_WAIT_POLL_INTERVAL_MS);
    };

    state.orderWaitPoll = window.setTimeout(tick, 250);
  }

  function buildBadge(name) {
    const letters = String(name || '')
      .split(/\s+/)
      .filter(Boolean)
      .map((chunk) => chunk[0]);
    return (letters.slice(0, 2).join('') || 'SM').toUpperCase();
  }

  function buildMonogramLogoSvg(label, background, foreground) {
    return `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect x="1.5" y="1.5" width="21" height="21" rx="6" fill="${background}"/><text x="12" y="15.1" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="8.4" font-weight="700" fill="${foreground}">${escapeHtml(label)}</text></svg>`;
  }

  const BRAND_LOGOS = {
    vk: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#0077FF" d="m9.489.004.729-.003h3.564l.73.003.914.01.433.007.418.011.403.014.388.016.374.021.36.025.345.03.333.033c1.74.196 2.933.616 3.833 1.516.9.9 1.32 2.092 1.516 3.833l.034.333.029.346.025.36.02.373.025.588.012.41.013.644.009.915.004.98-.001 3.313-.003.73-.01.914-.007.433-.011.418-.014.403-.016.388-.021.374-.025.36-.03.345-.033.333c-.196 1.74-.616 2.933-1.516 3.833-.9.9-2.092 1.32-3.833 1.516l-.333.034-.346.029-.36.025-.373.02-.588.025-.41.012-.644.013-.915.009-.98.004-3.313-.001-.73-.003-.914-.01-.433-.007-.418-.011-.403-.014-.388-.016-.374-.021-.36-.025-.345-.03-.333-.033c-1.74-.196-2.933-.616-3.833-1.516-.9-.9-1.32-2.092-1.516-3.833l-.034-.333-.029-.346-.025-.36-.02-.373-.025-.588-.012-.41-.013-.644-.009-.915-.004-.98.001-3.313.003-.73.01-.914.007-.433.011-.418.014-.403.016-.388.021-.374.025-.36.03-.345.033-.333c.196-1.74.616-2.933 1.516-3.833.9-.9 2.092-1.32 3.833-1.516l.333-.034.346-.029.36-.025.373-.02.588-.025.41-.012.644-.013.915-.009ZM6.79 7.3H4.05c.13 6.24 3.25 9.99 8.72 9.99h.31v-3.57c2.01.2 3.53 1.67 4.14 3.57h2.84c-.78-2.84-2.83-4.41-4.11-5.01 1.28-.74 3.08-2.54 3.51-4.98h-2.58c-.56 1.98-2.22 3.78-3.8 3.95V7.3H10.5v6.92c-1.6-.4-3.62-2.34-3.71-6.92Z"/></svg>`,
    telegram: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#26A5E4" d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/></svg>`,
    tiktok: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#111111" d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg>`,
    discord: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#5865F2" d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/></svg>`,
    mail: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#1F6BFF" d="M3 5.5h18a1.5 1.5 0 0 1 1.5 1.5v10A1.5 1.5 0 0 1 21 18.5H3A1.5 1.5 0 0 1 1.5 17V7A1.5 1.5 0 0 1 3 5.5Zm0 1.8v.29l9 5.66 9-5.66V7.3H3Zm18 2.41-8.52 5.35a.9.9 0 0 1-.96 0L3 9.71V16.7h18V9.71Z"/></svg>`,
    vpn: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#179A6B" d="M12 1.5 4 4.5v6.33c0 5.04 3.43 9.73 8 10.67 4.57-.94 8-5.63 8-10.67V4.5l-8-3Zm0 2.21 5.8 2.17v4.95c0 3.94-2.5 7.59-5.8 8.46-3.3-.87-5.8-4.52-5.8-8.46V5.88L12 3.71Zm0 3.29a3.5 3.5 0 0 0-3.5 3.5v.5H8a1 1 0 0 0-1 1V16a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1h-.5v-.5A3.5 3.5 0 0 0 12 7Zm-1.7 3.5a1.7 1.7 0 1 1 3.4 0v.5h-3.4v-.5Z"/></svg>`,
    reddit: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#FF4500" d="M12 0C5.373 0 0 5.373 0 12c0 3.314 1.343 6.314 3.515 8.485l-2.286 2.286C.775 23.225 1.097 24 1.738 24H12c6.627 0 12-5.373 12-12S18.627 0 12 0Zm4.388 3.199c1.104 0 1.999.895 1.999 1.999 0 1.105-.895 2-1.999 2-.946 0-1.739-.657-1.947-1.539v.002c-1.147.162-2.032 1.15-2.032 2.341v.007c1.776.067 3.4.567 4.686 1.363.473-.363 1.064-.58 1.707-.58 1.547 0 2.802 1.254 2.802 2.802 0 1.117-.655 2.081-1.601 2.531-.088 3.256-3.637 5.876-7.997 5.876-4.361 0-7.905-2.617-7.998-5.87-.954-.447-1.614-1.415-1.614-2.538 0-1.548 1.255-2.802 2.803-2.802.645 0 1.239.218 1.712.585 1.275-.79 2.881-1.291 4.64-1.365v-.01c0-1.663 1.263-3.034 2.88-3.207.188-.911.993-1.595 1.959-1.595Zm-8.085 8.376c-.784 0-1.459.78-1.506 1.797-.047 1.016.64 1.429 1.426 1.429.786 0 1.371-.369 1.418-1.385.047-1.017-.553-1.841-1.338-1.841Zm7.406 0c-.786 0-1.385.824-1.338 1.841.047 1.017.634 1.385 1.418 1.385.785 0 1.473-.413 1.426-1.429-.046-1.017-.721-1.797-1.506-1.797Zm-3.703 4.013c-.974 0-1.907.048-2.77.135-.147.015-.241.168-.183.305.483 1.154 1.622 1.964 2.953 1.964 1.33 0 2.47-.81 2.953-1.964.057-.137-.037-.29-.184-.305-.863-.087-1.795-.135-2.769-.135Z"/></svg>`,
    linkedin: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect width="24" height="24" rx="4.5" fill="#0A66C2"/><circle cx="7.1" cy="8.1" r="1.4" fill="#fff"/><rect x="5.9" y="10" width="2.4" height="8.2" rx="1.2" fill="#fff"/><path fill="#fff" d="M10.1 10h2.3v1.15c.53-.85 1.45-1.45 2.86-1.45 2.38 0 3.5 1.57 3.5 4.28v4.22h-2.48v-3.92c0-1.25-.45-2.1-1.58-2.1-.86 0-1.37.58-1.6 1.14-.08.2-.1.48-.1.76v4.12H10.1V10Z"/></svg>`,
    steam: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#111827" d="M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.203-.59 1.912-.59.063 0 .125.004.188.006l2.861-4.142V8.91c0-2.495 2.028-4.524 4.524-4.524 2.494 0 4.524 2.031 4.524 4.527s-2.03 4.525-4.524 4.525h-.105l-4.076 2.911c0 .052.004.105.004.159 0 1.875-1.515 3.396-3.39 3.396-1.635 0-3.016-1.173-3.331-2.727L.436 15.27C1.862 20.307 6.486 24 11.979 24c6.627 0 11.999-5.373 11.999-12S18.605 0 11.979 0zM7.54 18.21l-1.473-.61c.262.543.714.999 1.314 1.25 1.297.539 2.793-.076 3.332-1.375.263-.63.264-1.319.005-1.949s-.75-1.121-1.377-1.383c-.624-.26-1.29-.249-1.878-.03l1.523.63c.956.4 1.409 1.5 1.009 2.455-.397.957-1.497 1.41-2.454 1.012H7.54zm11.415-9.303c0-1.662-1.353-3.015-3.015-3.015-1.665 0-3.015 1.353-3.015 3.015 0 1.665 1.35 3.015 3.015 3.015 1.663 0 3.015-1.35 3.015-3.015zm-5.273-.005c0-1.252 1.013-2.266 2.265-2.266 1.249 0 2.266 1.014 2.266 2.266 0 1.251-1.017 2.265-2.266 2.265-1.253 0-2.265-1.014-2.265-2.265z"/></svg>`,
    chatgpt: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect width="24" height="24" rx="6" fill="#10A37F"/><path fill="#fff" d="M11.97 4.2a3.2 3.2 0 0 1 5.27 2.4c1.57.13 2.81 1.44 2.81 3.04 0 .7-.24 1.36-.68 1.88a3.2 3.2 0 0 1-.97 5.48 3.2 3.2 0 0 1-4.63 2.76 3.2 3.2 0 0 1-5.52-.9 3.2 3.2 0 0 1-3.58-4.69 3.2 3.2 0 0 1 .93-5.52A3.2 3.2 0 0 1 9.98 4.8c.58 0 1.15.16 1.65.46l.34-.2Zm-2.6 2.5a1.9 1.9 0 0 0-1.66 2.84l.22.39-1.16.68a1.9 1.9 0 0 0 .94 3.55h.44v1.34a1.9 1.9 0 0 0 3.2 1.39l.34-.3.82 1.03a1.9 1.9 0 0 0 3.31-1.28v-.45l1.27-.24a1.9 1.9 0 0 0 .58-3.48l-.38-.23.53-1.18a1.9 1.9 0 0 0-1.73-2.68h-.45l-.19-1.33a1.9 1.9 0 0 0-3.4-.91l-.28.35-1.06-.82a1.9 1.9 0 0 0-1.17-.4Z"/></svg>`,
    instagram: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#FF0069" d="M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077"/></svg>`,
    facebook: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#0866FF" d="M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z"/></svg>`,
    ok: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#EE8208" d="M12 0a6.2 6.2 0 0 0-6.194 6.195 6.2 6.2 0 0 0 6.195 6.192 6.2 6.2 0 0 0 6.193-6.192A6.2 6.2 0 0 0 12.001 0zm0 3.63a2.567 2.567 0 0 1 2.565 2.565 2.568 2.568 0 0 1-2.564 2.564 2.568 2.568 0 0 1-2.565-2.564 2.567 2.567 0 0 1 2.565-2.564zM6.807 12.6a1.814 1.814 0 0 0-.91 3.35 11.611 11.611 0 0 0 3.597 1.49l-3.462 3.463a1.815 1.815 0 0 0 2.567 2.566L12 20.066l3.405 3.403a1.813 1.813 0 0 0 2.564 0c.71-.709.71-1.858 0-2.566l-3.462-3.462a11.593 11.593 0 0 0 3.596-1.49 1.814 1.814 0 1 0-1.932-3.073 7.867 7.867 0 0 1-8.34 0c-.318-.2-.674-.29-1.024-.278z"/></svg>`,
    rambler: buildMonogramLogoSvg('R', '#315EFB', '#FFFFFF'),
    yahoo: buildMonogramLogoSvg('Y!', '#5F01D1', '#FFFFFF'),
    yandex: buildMonogramLogoSvg('Я', '#FF0000', '#FFFFFF'),
    outlook: buildMonogramLogoSvg('O', '#0A64F0', '#FFFFFF'),
    gmail: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#EA4335" d="M2.4 6.1 12 13.2l9.6-7.1v11.8a1.1 1.1 0 0 1-1.1 1.1h-2.2V9.9L12 14.4 5.8 9.9V19H3.5a1.1 1.1 0 0 1-1.1-1.1V6.1Z"/><path fill="#34A853" d="M18.3 19V9.9l3.3-2.5V17.9a1.1 1.1 0 0 1-1.1 1.1h-2.2Z"/><path fill="#4285F4" d="M2.4 19h3.4V9.9L2.4 7.4V19Z"/><path fill="#FBBC04" d="M2.4 6.1 4.6 4.4 12 10l7.4-5.6 2.2 1.7-9.6 7.1L2.4 6.1Z"/></svg>`,
    icloud: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#57A6FF" d="M16.7 9.3a4.5 4.5 0 0 0-8.53-1.84A3.84 3.84 0 0 0 4.5 11.3 3.7 3.7 0 0 0 8.2 15h8.46A3.35 3.35 0 1 0 16.7 9.3Z"/></svg>`,
    apple: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#111111" d="M16.37 12.76c.01 2.74 2.4 3.65 2.43 3.66-.02.06-.38 1.32-1.26 2.62-.76 1.13-1.55 2.26-2.8 2.28-1.23.02-1.63-.73-3.04-.73-1.42 0-1.85.71-3.02.75-1.2.04-2.12-1.2-2.89-2.33-1.57-2.27-2.76-6.4-1.16-9.2.8-1.39 2.22-2.27 3.76-2.3 1.17-.02 2.28.78 3.04.78.76 0 2.18-.96 3.67-.82.63.03 2.4.26 3.54 1.92-.09.06-2.11 1.24-2.1 3.67ZM14.78 5.54c.63-.77 1.05-1.85.93-2.92-.91.04-2.01.61-2.66 1.38-.58.68-1.09 1.78-.95 2.83 1.01.08 2.04-.52 2.68-1.29Z"/></svg>`,
    whatsapp: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#25D366" d="M20.52 3.48A11.86 11.86 0 0 0 12.07 0C5.5 0 .15 5.34.15 11.91c0 2.1.55 4.16 1.6 5.98L0 24l6.29-1.65a11.83 11.83 0 0 0 5.66 1.44h.01c6.57 0 11.92-5.34 11.92-11.91 0-3.18-1.24-6.18-3.36-8.4ZM12.07 21.8h-.01a9.87 9.87 0 0 1-5.03-1.38l-.36-.21-3.73.98 1-3.64-.24-.37a9.84 9.84 0 0 1-1.51-5.27c0-5.46 4.44-9.9 9.9-9.9 2.64 0 5.12 1.03 6.99 2.91a9.82 9.82 0 0 1 2.9 6.99c0 5.46-4.44 9.9-9.9 9.9Zm5.43-7.41c-.3-.15-1.75-.86-2.02-.96-.27-.1-.47-.15-.66.15-.2.3-.76.96-.93 1.16-.17.2-.34.22-.64.08-.3-.15-1.25-.46-2.39-1.47-.88-.79-1.47-1.76-1.65-2.06-.17-.3-.02-.47.13-.62.13-.13.3-.34.45-.51.15-.17.2-.3.3-.5.1-.2.05-.37-.02-.52-.08-.15-.66-1.59-.91-2.18-.24-.57-.48-.49-.66-.49h-.56c-.2 0-.51.08-.78.37-.27.3-1.03 1-1.03 2.43 0 1.42 1.05 2.8 1.2 2.99.15.2 2.05 3.13 4.97 4.39.69.3 1.24.48 1.66.61.7.22 1.34.19 1.84.11.56-.08 1.75-.72 2-1.41.25-.69.25-1.29.17-1.41-.08-.12-.28-.2-.58-.35Z"/></svg>`,
    skype: buildMonogramLogoSvg('S', '#00AFF0', '#FFFFFF'),
    paypal: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#003087" d="M9.63 4.25h5.15c1.76 0 3.02.37 3.76 1.11.74.75 1 1.86.77 3.35-.24 1.57-.86 2.74-1.87 3.49-1.01.75-2.45 1.13-4.31 1.13h-1.47l-1.05 6.42H6.73L9.63 4.25Z"/><path fill="#009CDE" d="M11.2 6.01h4.17c1.2 0 2.07.23 2.61.7.54.47.72 1.17.54 2.11-.18.95-.61 1.67-1.31 2.16-.69.49-1.67.74-2.93.74h-1.19l-.79 4.8H8.92l1.28-7.75Z"/></svg>`,
    windows: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#00A4EF" d="M2 4.2 10.4 3v8H2v-6.8Zm9.6-1.34L22 1.3V11h-10.4V2.86ZM2 13h8.4v8L2 19.8V13Zm9.6 0H22v9.7l-10.4-1.56V13Z"/></svg>`,
    sms: buildMonogramLogoSvg('SMS', '#20B486', '#FFFFFF'),
    ai: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="2" width="20" height="20" rx="6" fill="#6D28D9"/><path fill="#fff" d="M8.35 15.8h1.42l.55-1.56h3.34l.56 1.56h1.43L12.7 8.2h-1.42L8.35 15.8Zm2.43-2.7 1.18-3.38 1.2 3.38h-2.38ZM17.25 8.95a.8.8 0 1 0 0 1.6.8.8 0 0 0 0-1.6Zm-.8 2.48h1.6v4.37h-1.6v-4.37Z"/></svg>`,
    dating: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="2" width="20" height="20" rx="6" fill="#E11D48"/><path fill="#fff" d="M12 17.55 10.84 16.5C6.7 12.74 4 10.29 4 7.29 4 4.84 5.9 3 8.25 3c1.33 0 2.6.64 3.4 1.64C12.45 3.64 13.72 3 15.05 3 17.4 3 19.3 4.84 19.3 7.29c0 3-2.7 5.45-6.84 9.21L12 17.55Z"/></svg>`,
    line: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><rect width="24" height="24" rx="6" fill="#06C755"/><path fill="#fff" d="M12 5.2c-3.6 0-6.5 2.42-6.5 5.4 0 2.67 2.28 4.9 5.36 5.31l-.23 1.36c-.04.24.2.42.42.3l1.67-1.04c3.21-.18 5.78-2.47 5.78-5.93 0-2.98-2.92-5.4-6.5-5.4Zm-2.74 6.47H8.08V9.24H9.1v1.5h.16v.93Zm1.98 0h-1.02V9.24h1.02v2.43Zm2.63 0h-1.03l-.8-1.09v1.09h-1.02V9.24h1.02l.8 1.08V9.24h1.03v2.43Zm2.03 0h-1.77V9.24h1.77v.93h-.75v.16h.75v.93h-.75v.17h.75v.24Z"/></svg>`,
    wechat: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#07C160" d="M8.9 3.5C4.96 3.5 1.75 6.08 1.75 9.25c0 1.8 1.05 3.41 2.7 4.47L3.5 16.5l2.95-1.47c.78.19 1.6.3 2.45.3 3.95 0 7.15-2.58 7.15-5.75S12.85 3.5 8.9 3.5Zm-2.4 4.26a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6Zm4.8 0a.8.8 0 1 1 0 1.6.8.8 0 0 1 0-1.6Zm4.05 3.4c-3.14 0-5.7 2.06-5.7 4.6 0 2.55 2.56 4.62 5.7 4.62.67 0 1.32-.1 1.93-.28l2.28 1.15-.68-2.07c1.14-.85 1.83-2 1.83-3.42 0-2.54-2.55-4.6-5.7-4.6Zm-1.9 3.03a.65.65 0 1 1 0 1.3.65.65 0 0 1 0-1.3Zm3.8 0a.65.65 0 1 1 0 1.3.65.65 0 0 1 0-1.3Z"/></svg>`,
    viber: buildMonogramLogoSvg('V', '#7360F2', '#FFFFFF'),
    max: buildMonogramLogoSvg('MAX', '#111827', '#FFFFFF'),
    twitch: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#9146FF" d="M2.4 1.8 1 5.53v14.88h5.02V24l3.6-3.59h3.02L19.96 13V1.8H2.4Zm15.91 10.46-4.3 4.3h-3.59l-3.15 3.14v-3.14H4.6V3.95h13.7v8.31Zm-3.14-5.74h-1.43v4.3h1.43v-4.3Zm-3.87 0H9.87v4.3h1.43v-4.3Z"/></svg>`,
    x: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#111111" d="M18.9 2H22l-6.77 7.74L23.2 22h-6.25l-4.9-7.36L5.61 22H2.5l7.24-8.28L1.8 2h6.4l4.43 6.77L18.9 2Zm-1.1 18h1.73L7.2 3.9H5.34L17.8 20Z"/></svg>`,
    youtube: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#FF0000" d="M23.5 6.2a3 3 0 0 0-2.11-2.12C19.47 3.5 12 3.5 12 3.5s-7.47 0-9.39.58A3 3 0 0 0 .5 6.2 31.3 31.3 0 0 0 0 12a31.3 31.3 0 0 0 .5 5.8 3 3 0 0 0 2.11 2.12c1.92.58 9.39.58 9.39.58s7.47 0 9.39-.58a3 3 0 0 0 2.11-2.12A31.3 31.3 0 0 0 24 12a31.3 31.3 0 0 0-.5-5.8ZM9.6 15.6V8.4L15.84 12 9.6 15.6Z"/></svg>`,
    other: buildMonogramLogoSvg('⋯', '#7A3D2E', '#FFFFFF'),
    default: `<svg viewBox="0 0 24 24" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><path fill="#7A3D2E" d="M3 5.5A2.5 2.5 0 0 1 5.5 3H9l1.6 1.8h7.9A2.5 2.5 0 0 1 21 7.3v9.2A2.5 2.5 0 0 1 18.5 19H5.5A2.5 2.5 0 0 1 3 16.5v-11Zm2 .5v10h14V7.3a.5.5 0 0 0-.5-.5h-8.8L8.1 5H5.5a.5.5 0 0 0-.5.5Z"/></svg>`,
  };

  function getLogoMarkup(theme) {
    return BRAND_LOGOS[theme] || BRAND_LOGOS.default;
  }

  const PLATFORM_THEME_ALIASES = {
    instagram: ['instagram', 'инстаграм', 'инста'],
    chatgpt: ['chatgpt', 'chat gpt', 'gpt chat', 'чатгпт', 'чат gpt', 'openai', 'open ai', 'нейросети', 'нейросеть', 'нейросетях', 'нейросетям', 'нейросетями'],
    facebook: ['facebook', 'meta', 'фейсбук', 'fb'],
    vk: ['вконтакте', 'вк', 'vk', 'vkontakte'],
    telegram: ['telegram', 'tg', 'телеграм'],
    tiktok: ['tiktok', 'tik tok', 'тикток'],
    discord: ['discord', 'дискорд'],
    reddit: ['reddit', 'реддит', 'реддитт'],
    linkedin: ['linkedin', 'linked in', 'linkdin', 'линкедин', 'линкд ин'],
    steam: ['steam', 'стим', 'игров'],
    x: ['twitter', 'x.com', 'твиттер'],
    youtube: ['youtube', 'ютуб', 'ютуьб', 'ютюб', 'yt'],
    whatsapp: ['whatsapp', 'ватсап', 'вацап'],
    skype: ['skype', 'скайп'],
    line: ['line'],
    wechat: ['wechat', 'we chat', 'вичат'],
    viber: ['viber', 'вайбер'],
    max: ['max', 'макс'],
    twitch: ['twitch', 'твич', 'stream', 'стрим', 'streaming', 'стриминг'],
    ok: ['одноклассники', 'однокласс', 'ok', 'ok.ru', 'одноклы'],
  };

  const PLATFORM_THEME_RULES = Object.entries(PLATFORM_THEME_ALIASES).map(([theme, aliases]) => ({
    theme,
    pattern: new RegExp(
      `(?:^|[^a-zа-я0-9])(?:${aliases.map((alias) => alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(?:$|[^a-zа-я0-9])`,
      'i',
    ),
  }));

  const SECONDARY_THEME_RULES = [
    { match: ['gmail', 'google mail', 'google'], theme: 'gmail' },
    { match: ['mail.yandex', 'yandex', 'яндекс'], theme: 'yandex' },
    { match: ['mail.ru', 'почт', 'другие почты', 'другие почты и сервисы', 'mail'], theme: 'mail' },
    { match: ['rambler', 'рамблер'], theme: 'rambler' },
    { match: ['yahoo', 'яху'], theme: 'yahoo' },
    { match: ['hotmail', 'outlook'], theme: 'outlook' },
    { match: ['icloud'], theme: 'icloud' },
    { match: ['apple id', 'apple'], theme: 'apple' },
    { match: ['paypal'], theme: 'paypal' },
    { match: ['windows'], theme: 'windows' },
    { match: ['sms'], theme: 'sms' },
    { match: ['ai', 'искусственный интеллект', 'deepseek', 'gemini'], theme: 'ai' },
    { match: ['сайты знакомств', 'знакомств', 'общения', 'dating sites', 'dating'], theme: 'dating' },
    { match: ['vpn', 'proxy', 'rdp', 'vps'], theme: 'vpn' },
    { match: ['остальное', 'прочее', 'другое', 'other'], theme: 'other' },
  ];

  const VISUAL_SOURCE_PRIORITY = [
    (item, category, section) => item && item.title,
    (item, category) => category && category.name,
    (item, category) => category && category.parent_name,
    (item, category, section) => section && section.name,
    (item) => item && item.description,
    (item) => (Array.isArray(item && item.attributes) ? item.attributes.join(' ') : ''),
  ];

  function findThemeByKeywords(normalized, mapping) {
    const found = mapping.find((item) => item.match.some((chunk) => normalized.includes(chunk)));
    return found ? found.theme : null;
  }

  function findPlatformTheme(text) {
    const normalized = String(text || '').toLowerCase();
    const found = PLATFORM_THEME_RULES.find((item) => item.pattern.test(normalized));
    return found ? found.theme : null;
  }

  function resolveExplicitPlatformTheme(text) {
    return findPlatformTheme(text);
  }

  function resolveVisualTheme(name) {
    const normalized = String(name || '').toLowerCase();
    const explicitPlatformTheme = findPlatformTheme(normalized);
    if (explicitPlatformTheme) {
      return {
        theme: explicitPlatformTheme,
        logoMarkup: getLogoMarkup(explicitPlatformTheme),
      };
    }
    const secondaryTheme = findThemeByKeywords(normalized, SECONDARY_THEME_RULES);
    if (secondaryTheme) {
      return {
        theme: secondaryTheme,
        logoMarkup: getLogoMarkup(secondaryTheme),
      };
    }
    return {
      theme: 'default',
      logoMarkup: getLogoMarkup('default'),
    };
  }

  function resolveDirectoryVisual(name) {
    return resolveVisualTheme(name);
  }

  function resolveProductVisual(item, category, section) {
    const titleOverrideTheme = resolveExplicitPlatformTheme(item && item.title);
    if (titleOverrideTheme) {
      return {
        theme: titleOverrideTheme,
        logoMarkup: getLogoMarkup(titleOverrideTheme),
      };
    }

    for (const sourceBuilder of VISUAL_SOURCE_PRIORITY) {
      const source = sourceBuilder(item, category, section);
      const platformTheme = resolveExplicitPlatformTheme(source);
      if (platformTheme) {
        return {
          theme: platformTheme,
          logoMarkup: getLogoMarkup(platformTheme),
        };
      }
    }
    for (const sourceBuilder of VISUAL_SOURCE_PRIORITY) {
      const source = sourceBuilder(item, category, section);
      const visual = resolveVisualTheme(source);
      if (visual.theme !== 'default') {
        return visual;
      }
    }
    return resolveVisualTheme('');
  }

  function getCategoryById(categoryId) {
    return state.categories.find((category) => category.id === categoryId) || null;
  }

  function getSelectedCategory() {
    return state.categories.find((category) => category.id === state.selectedCategoryId) || null;
  }

  function getSelectedSection() {
    return state.sections.find((section) => (section.items || []).some((item) => item.id === state.selectedCategoryId)) || null;
  }

  function isSectionExpanded(sectionId) {
    return state.expandedSectionIds.includes(sectionId);
  }

  function toggleSection(sectionId) {
    if (isSectionExpanded(sectionId)) {
      state.expandedSectionIds = state.expandedSectionIds.filter((id) => id !== sectionId);
      return;
    }
    state.expandedSectionIds = [...state.expandedSectionIds, sectionId];
  }

  function ensureSectionExpanded(sectionId) {
    if (!isSectionExpanded(sectionId)) {
      state.expandedSectionIds = [...state.expandedSectionIds, sectionId];
    }
  }

  function renderCategoryFilterOptions() {
    catalogCategoryFilter.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = t('catalog_choose_category');
    catalogCategoryFilter.appendChild(placeholder);

    state.sections.forEach((section) => {
      const group = document.createElement('optgroup');
      group.label = section.name || 'Раздел';
      (section.items || []).forEach((item) => {
        const option = document.createElement('option');
        option.value = String(item.id);
        option.textContent = `${section.name || 'Раздел'} -> ${item.name || 'Категория'}`;
        group.appendChild(option);
      });
      catalogCategoryFilter.appendChild(group);
    });
  }

  function updateFilterPanelVisibility() {
    const expanded = Boolean(state.filtersExpanded);
    catalogFilterPanel.classList.toggle('catalog-filter-panel-collapsed', !expanded);
    catalogFilterToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    catalogFilterToggle.classList.toggle('catalog-filter-sticker-active', expanded);
    catalogFilterToggle.setAttribute('aria-label', expanded ? 'Скрыть фильтр' : 'Открыть фильтр');
  }

  function resetPendingFilters() {
    state.pendingFilters = {
      priceMin: '',
      priceMax: '',
      attributeTokens: [],
    };
  }

  function resetActiveFilters() {
    state.activeFilters = {
      priceMin: '',
      priceMax: '',
      attributeTokens: [],
    };
  }

  function syncFilterInputs() {
    catalogPriceMin.value = state.pendingFilters.priceMin;
    catalogPriceMax.value = state.pendingFilters.priceMax;
    catalogSortFilter.value = state.sortMode;
  }

  function getAvailableAttributeTokens(items) {
    const counts = new Map();
    (items || []).forEach((item) => {
      (item.attributes || []).forEach((attribute) => {
        const token = String(attribute || '').trim();
        if (!token) {
          return;
        }
        counts.set(token, (counts.get(token) || 0) + 1);
      });
    });

    return Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0], 'ru'))
      .slice(0, 16);
  }

  const SEARCH_CHAR_MAP = {
    а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'i',
    к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f',
    х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sh', ы: 'y', э: 'e', ю: 'yu', я: 'ya', ь: '', ъ: '',
  };

  function normalizeSearchText(value) {
    const source = String(value || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
    return Array.from(source)
      .map((character) => Object.prototype.hasOwnProperty.call(SEARCH_CHAR_MAP, character) ? SEARCH_CHAR_MAP[character] : character)
      .join('')
      .replace(/instagram|instgram|insta|threads/g, ' instagram ')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function editDistance(left, right) {
    const a = String(left || '');
    const b = String(right || '');
    const row = Array.from({ length: b.length + 1 }, (_, index) => index);
    for (let i = 1; i <= a.length; i += 1) {
      let previous = row[0];
      row[0] = i;
      for (let j = 1; j <= b.length; j += 1) {
        const saved = row[j];
        row[j] = Math.min(row[j] + 1, row[j - 1] + 1, previous + (a[i - 1] === b[j - 1] ? 0 : 1));
        previous = saved;
      }
    }
    return row[b.length];
  }

  function productSearchScore(item, query) {
    const normalizedQuery = normalizeSearchText(query);
    if (!normalizedQuery) return 1;
    const title = normalizeSearchText(item.title);
    const description = normalizeSearchText(item.description);
    const attributes = normalizeSearchText((item.attributes || []).join(' '));
    const haystack = `${title} ${attributes} ${description}`.trim();
    if (title === normalizedQuery) return 1000;
    if (title.startsWith(normalizedQuery)) return 800 - title.length;
    if (title.includes(normalizedQuery)) return 650 - title.indexOf(normalizedQuery);
    if (haystack.includes(normalizedQuery)) return 500 - haystack.indexOf(normalizedQuery);
    const queryTokens = normalizedQuery.split(' ').filter(Boolean);
    const valueTokens = haystack.split(' ').filter(Boolean);
    let total = 0;
    for (const token of queryTokens) {
      let best = 0;
      for (const candidate of valueTokens) {
        if (candidate.startsWith(token) || token.startsWith(candidate)) best = Math.max(best, 80);
        const allowed = Math.max(token.length, candidate.length) >= 7 ? 2 : 1;
        const distance = editDistance(token, candidate);
        if (distance <= allowed) best = Math.max(best, 70 - distance * 10);
      }
      if (!best) return 0;
      total += best;
    }
    return total;
  }

  function renderAttributeFilters() {
    catalogAttributeFilters.innerHTML = '';
    const entries = getAvailableAttributeTokens(state.rawProductItems);
    if (!entries.length) {
      catalogAttributeFilters.innerHTML = '<div class="filter-chip-empty">У этой категории пока нет тегов для быстрого фильтра.</div>';
      return;
    }

    entries.forEach(([token, count]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `filter-chip${state.pendingFilters.attributeTokens.includes(token) ? ' filter-chip-active' : ''}`;
      button.innerHTML = `
        <span>${escapeHtml(token)}</span>
        <strong>${count}</strong>
      `;
      button.addEventListener('click', () => {
        if (state.pendingFilters.attributeTokens.includes(token)) {
          state.pendingFilters.attributeTokens = state.pendingFilters.attributeTokens.filter((item) => item !== token);
        } else {
          state.pendingFilters.attributeTokens = [...state.pendingFilters.attributeTokens, token];
        }
        renderAttributeFilters();
      });
      catalogAttributeFilters.appendChild(button);
    });
  }

  function applyProductFilters() {
    const minPrice = Number(state.activeFilters.priceMin || 0);
    const maxPrice = Number(state.activeFilters.priceMax || 0);
    const requiredTokens = state.activeFilters.attributeTokens || [];

    const rankedItems = state.rawProductItems.map((item) => ({ item, score: productSearchScore(item, state.searchQuery) }));
    const filteredItems = rankedItems.filter(({ item, score }) => {
      if (state.searchQuery && score <= 0) return false;
      if (state.favoritesOnly && !item.favorite) return false;
      const price = Number(item.price_usdt || 0);
      if (state.activeFilters.priceMin !== '' && price < minPrice) {
        return false;
      }
      if (state.activeFilters.priceMax !== '' && price > maxPrice) {
        return false;
      }
      if (requiredTokens.length) {
        const attributes = (item.attributes || []).map((attribute) => String(attribute || '').trim());
        return requiredTokens.every((token) => attributes.includes(token));
      }
      return true;
    }).sort((left, right) => right.score - left.score).map(({ item }) => item);

    state.productItems = filteredItems;
    renderProducts(filteredItems);
    updateCatalogSelection();
    catalogResultsMeta.textContent = requiredTokens.length
      ? `Фильтровано по тегам: ${requiredTokens.join(', ')}`
      : '';
  }

  function sortProducts(items) {
    const sorted = [...items];
    if (state.searchQuery) {
      return sorted;
    }
    if (state.sortMode === 'cheap') {
      return sorted.sort((left, right) => Number(left.price_usdt || 0) - Number(right.price_usdt || 0));
    }
    if (state.sortMode === 'expensive') {
      return sorted.sort((left, right) => Number(right.price_usdt || 0) - Number(left.price_usdt || 0));
    }
    if (state.sortMode === 'stock') {
      return sorted.sort((left, right) => Number(right.quantity || 0) - Number(left.quantity || 0));
    }
    return sorted.sort((left, right) => Number(right.sales_count || 0) - Number(left.sales_count || 0));
  }

  function updateCatalogSelection() {
    const category = getSelectedCategory();
    const section = getSelectedSection();
    if (!category) {
      catalogSelectionTitle.textContent = 'Категория';
      catalogSelectionSubtitle.textContent = '';
      catalogCategoryFilter.value = '';
      catalogResultsMeta.textContent = '';
      return;
    }

    catalogSelectionTitle.textContent = category.name || 'Категория';
    catalogSelectionSubtitle.textContent = section
      ? `${section.name}`
      : '';
    catalogCategoryFilter.value = String(category.id);
    if (!state.productMeta) {
      catalogResultsMeta.textContent = '';
      return;
    }
    catalogResultsMeta.textContent = '';
  }

  function renderCategoryDirectory() {
    catalogCategories.innerHTML = '';
    state.sections.forEach((section) => {
      if (section.decorative) {
        const divider = document.createElement('div');
        divider.className = 'directory-mail-divider';
        divider.textContent = section.name || 'Почты ↓';
        catalogCategories.appendChild(divider);
        return;
      }
      const visual = resolveDirectoryVisual(section.name);
      const card = document.createElement('article');
      const expanded = isSectionExpanded(section.id);
      card.className = `directory-card${expanded ? ' directory-card-expanded' : ' directory-card-collapsed'}`;
      card.innerHTML = `
        <div class="directory-head">
          <button class="directory-toggle" type="button" aria-expanded="${expanded ? 'true' : 'false'}">
            <div class="directory-brand">
              <span class="directory-badge directory-badge--${visual.theme}">${visual.logoMarkup}</span>
              <div class="directory-brand-copy">
                <h3 class="directory-title">${escapeHtml(section.name || 'Категория')}</h3>
                <p class="directory-caption"></p>
              </div>
            </div>
          </button>
        </div>
        <div class="directory-list"></div>
      `;

      const toggleButton = card.querySelector('.directory-toggle');
      toggleButton.addEventListener('click', () => {
        toggleSection(section.id);
        renderCategoryDirectory();
      });

      const list = card.querySelector('.directory-list');
      (section.items || []).forEach((item) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `directory-item${state.selectedCategoryId === item.id ? ' directory-item-active' : ''}`;
        button.innerHTML = `
          <span class="directory-item-arrow">▶</span>
          <span class="directory-item-copy">
            <strong>${escapeHtml(item.name || 'Подкатегория')}</strong>
            <small>${escapeHtml(item.parent_name || section.name || '')}</small>
          </span>
        `;
        button.addEventListener('click', async () => selectCategory(item.id, { scroll: true }));
        list.appendChild(button);
      });

      catalogCategories.appendChild(card);
    });
  }

  function renderCatalogPlaceholder(message) {
    if (catalogPagination) {
      catalogPagination.innerHTML = '';
      catalogPagination.classList.add('hidden');
    }
    catalogProducts.innerHTML = `<article class="catalog-empty-card">${escapeHtml(message)}</article>`;
    localizeDynamicContent(catalogProducts);
  }

  function openExternalUrl(url) {
    const cleanUrl = normalizeExternalUrl(url);
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
    window.open(cleanUrl, '_blank', 'noopener');
    return true;
  }

  function renderProxyEntryCard(category) {
    if (catalogPagination) {
      catalogPagination.innerHTML = '';
      catalogPagination.classList.add('hidden');
    }
    catalogProducts.innerHTML = `
      <article class="catalog-empty-card catalog-proxy-entry">
        <strong>ProxySoxy</strong>
        <p>${escapeHtml(category.description || 'Basic / Private / Dedicated')}</p>
        <button id="catalog-open-proxy" class="primary-button catalog-proxy-button" type="button">Открыть прокси</button>
      </article>
    `;
    localizeDynamicContent(catalogProducts);
    document.getElementById('catalog-open-proxy')?.addEventListener('click', () => {
      openExternalUrl(category.redirect_url || '');
    });
  }

  function renderProducts(items) {
    const category = getSelectedCategory();
    const section = getSelectedSection();
    const sortedItems = sortProducts(Array.isArray(items) ? items : []);
    catalogProducts.innerHTML = '';
    if (!category) {
      renderCatalogPlaceholder(t('catalog_empty_products'));
      return;
    }
    if (!sortedItems.length) {
      renderCatalogPlaceholder(t('catalog_empty_category'));
      return;
    }
    sortedItems.forEach((item) => {
      const visual = resolveProductVisual(item, category, section);
      const card = document.createElement('article');
      card.className = 'product-card';
      card.innerHTML = `
        <div class="product-row">
          <div class="product-platform-badge product-platform-badge--${visual.theme}">${visual.logoMarkup}</div>
          <div class="product-main">
            <h3>${escapeHtml(item.title)}</h3>
            <p class="product-description">${escapeHtml(item.description || 'Описание появится в карточке товара.')}</p>
            <div class="product-meta">
              ${(item.attributes || []).slice(0, 3).map((attribute) => `<span class="meta-pill">${escapeHtml(attribute)}</span>`).join('')}
              <span class="meta-pill product-sales">Продано ${escapeHtml(item.sales_count || 0)}</span>
            </div>
          </div>
          <div class="product-side">
            <button class="product-favorite-button${item.favorite ? ' product-favorite-button-active' : ''}" type="button" aria-label="${item.favorite ? 'Удалить из избранного' : 'Добавить в избранное'}" aria-pressed="${item.favorite ? 'true' : 'false'}">${item.favorite ? '♥' : '♡'}</button>
            <span class="product-stock">${escapeHtml(item.quantity)} шт.</span>
            <strong class="price-tag">${Number(item.price_usdt || 0).toFixed(2)} $/шт.</strong>
            <button class="product-buy-button" type="button" aria-label="Открыть товар">Купить</button>
          </div>
        </div>
      `;
      localizeDynamicContent(card);
      const buyButton = card.querySelector('.product-buy-button');
      const favoriteButton = card.querySelector('.product-favorite-button');
      favoriteButton.addEventListener('click', async (event) => {
        event.stopPropagation();
        favoriteButton.disabled = true;
        const nextFavorite = !Boolean(item.favorite);
        try {
          const payload = await api(`${state.basePath}/api/products/${encodeURIComponent(item.id)}/favorite`, {
            method: 'POST',
            body: { favorite: nextFavorite },
          });
          item.favorite = Boolean(payload.favorite);
          applyProductFilters();
        } catch (error) {
          favoriteButton.disabled = false;
          if (tg && typeof tg.showAlert === 'function') tg.showAlert(error.message || 'Не удалось изменить избранное');
        }
      });
      buyButton.addEventListener('click', (event) => {
        event.stopPropagation();
        card.click();
      });
      card.addEventListener('click', () => {
        const overlayAttributes = (item.attributes || [])
          .map((attribute) => `<span class="sheet-attribute-pill">${escapeHtml(attribute)}</span>`)
          .join('');
        openOverlay(`
          <div class="sheet-product-head">
            <div class="sheet-product-badge product-platform-badge product-platform-badge--${visual.theme}">${visual.logoMarkup}</div>
            <div class="sheet-product-copy">
              <h3 class="sheet-product-title">${escapeHtml(item.title)}</h3>
              <p class="sheet-product-subtitle">${escapeHtml(item.description || 'Без описания')}</p>
            </div>
          </div>
          <div class="sheet-card sheet-product-summary">
            <div class="sheet-price-line">
              <strong>${Number(item.price_usdt || 0).toFixed(2)} $</strong>
              <span>Остаток: ${escapeHtml(item.quantity)} шт. • Продаж: ${escapeHtml(item.sales_count)}</span>
            </div>
            <div class="sheet-attribute-grid">${overlayAttributes || '<span class="sheet-attribute-pill">Без тегов</span>'}</div>
          </div>
          <div class="sheet-card sheet-product-action">
            <strong>Покупка</strong>
            <button class="ghost-button" type="button" id="sheet-toggle-favorite">${item.favorite ? '♥ Удалить из избранного' : '♡ Добавить в избранное'}</button>
            <div class="sheet-qty-picker">
              <button class="sheet-qty-button" type="button" id="sheet-qty-dec">−</button>
              <span class="sheet-qty-value" id="sheet-qty-value">1</span>
              <button class="sheet-qty-button" type="button" id="sheet-qty-inc">+</button>
            </div>
            <button class="primary-button sheet-buy-button" type="button" id="add-product-to-cart">Добавить в корзину • ${Number(item.price_usdt || 0).toFixed(2)} $</button>
            <button class="ghost-button" type="button" id="open-cart-from-product">Открыть корзину</button>
          </div>
        `);
        document.getElementById('sheet-toggle-favorite')?.addEventListener('click', async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          try {
            const payload = await api(`${state.basePath}/api/products/${encodeURIComponent(item.id)}/favorite`, {
              method: 'POST',
              body: { favorite: !Boolean(item.favorite) },
            });
            item.favorite = Boolean(payload.favorite);
            button.textContent = item.favorite ? '♥ Удалить из избранного' : '♡ Добавить в избранное';
            applyProductFilters();
          } catch (error) {
            if (tg && typeof tg.showAlert === 'function') tg.showAlert(error.message || 'Не удалось изменить избранное');
          } finally {
            button.disabled = false;
          }
        });
        const quantityValue = document.getElementById('sheet-qty-value');
        const decrementButton = document.getElementById('sheet-qty-dec');
        const incrementButton = document.getElementById('sheet-qty-inc');
        const addToCartButton = document.getElementById('add-product-to-cart');
        const openCartButton = document.getElementById('open-cart-from-product');
        let selectedQuantity = 1;
        const maxQuantity = Math.max(1, Number(item.quantity || 1));

        const syncQuantityUi = () => {
          if (quantityValue) {
            quantityValue.textContent = String(selectedQuantity);
          }
          if (addToCartButton) {
            addToCartButton.textContent = `Добавить в корзину • ${(Number(item.price_usdt || 0) * selectedQuantity).toFixed(2)} $`;
          }
          if (decrementButton) {
            decrementButton.disabled = selectedQuantity <= 1;
          }
          if (incrementButton) {
            incrementButton.disabled = selectedQuantity >= maxQuantity;
          }
        };

        decrementButton?.addEventListener('click', () => {
          selectedQuantity = Math.max(1, selectedQuantity - 1);
          syncQuantityUi();
        });
        incrementButton?.addEventListener('click', () => {
          selectedQuantity = Math.min(maxQuantity, selectedQuantity + 1);
          syncQuantityUi();
        });
        addToCartButton?.addEventListener('click', () => {
          addProductToCart(item, category, selectedQuantity);
          addToCartButton.textContent = 'Добавлено в корзину';
          window.setTimeout(() => {
            syncQuantityUi();
          }, 1200);
        });
        openCartButton?.addEventListener('click', () => {
          addProductToCart(item, category, selectedQuantity);
          openCartOverlay();
        });
        if (maxQuantity > 0) {
          syncQuantityUi();
        }
      });
      catalogProducts.appendChild(card);
    });
  }

  function scrollCatalogProductsIntoView() {
    catalogProducts.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderCatalogPagination() {
    if (!catalogPagination) {
      return;
    }

    const category = getSelectedCategory();
    const meta = state.productMeta;
    const isProxyRedirectCategory = Boolean(category && category.kind === 'proxy_redirect');
    if (!category || !meta || isProxyRedirectCategory || Number(meta.lastPage || 1) <= 1) {
      catalogPagination.innerHTML = '';
      catalogPagination.classList.add('hidden');
      return;
    }

    const currentPage = Math.max(1, Number(meta.page || 1));
    const lastPage = Math.max(1, Number(meta.lastPage || 1));
    const prevDisabled = currentPage <= 1;
    const nextDisabled = currentPage >= lastPage;

    catalogPagination.innerHTML = `
      <button class="catalog-page-button" type="button" data-page-action="prev" ${prevDisabled ? 'disabled' : ''}>Назад</button>
      <span class="catalog-page-indicator">Страница ${currentPage} из ${lastPage}</span>
      <button class="catalog-page-button catalog-page-button-primary" type="button" data-page-action="next" ${nextDisabled ? 'disabled' : ''}>Далее</button>
    `;
    catalogPagination.classList.remove('hidden');

    catalogPagination.querySelector('[data-page-action="prev"]')?.addEventListener('click', async () => {
      if (prevDisabled) {
        return;
      }
      state.productsPage = currentPage - 1;
      await loadProducts();
      scrollCatalogProductsIntoView();
    });

    catalogPagination.querySelector('[data-page-action="next"]')?.addEventListener('click', async () => {
      if (nextDisabled) {
        return;
      }
      state.productsPage = currentPage + 1;
      await loadProducts();
      scrollCatalogProductsIntoView();
    });
  }

  async function selectCategory(categoryId, options) {
    const nextCategoryId = Number(categoryId || 0) || null;
    state.selectedCategoryId = nextCategoryId;
    state.productsPage = 1;
    state.productMeta = null;
    state.rawProductItems = [];
    state.productItems = [];
    resetPendingFilters();
    resetActiveFilters();
    syncFilterInputs();
    renderAttributeFilters();

    const category = getSelectedCategory();
    const section = getSelectedSection();
    if (category && section) {
      ensureSectionExpanded(section.id);
    }

    renderCategoryDirectory();
    updateCatalogSelection();

    if (!category) {
      renderCatalogPlaceholder(t('catalog_empty_products'));
      catalogStatus.textContent = t('catalog_category_not_selected');
      return;
    }

    if (category.kind === 'proxy_redirect') {
      catalogStatus.textContent = '';
      renderProxyEntryCard(category);
      if (options && options.scroll) {
        scrollCatalogProductsIntoView();
      }
      return;
    }

    await loadProducts();
    if (options && options.scroll) {
      scrollCatalogProductsIntoView();
    }
  }

  function createPreviewPayload(path) {
    const query = path.includes('?') ? path.split('?')[1] : '';
    const params = new URLSearchParams(query);
    const categoryId = Number(params.get('category_id') || '101');
    const page = Number(params.get('page') || '1');
    const brandName = formatBrandNameFromSlug(state.botSlug);
    const botUsername = state.botSlug === 'main' ? 'sousmarketbot' : state.botSlug.replace(/^@+/, '');
    const marginPercentage = state.botSlug === 'main' ? 15 : 40;
    const orders = [
      {
        id: 1842,
        title: 'Мобильные прокси',
        category_name: 'Прокси',
        quantity: 1,
        total_price: 8.5,
        status: 'delivered',
        status_label: 'Выдан',
        delivery_preview: 'Товар выдан через бота. Детали заказа будут доступны после подключения живой базы.',
        created_at: '2026-06-28 14:32:00',
        is_account_order: false,
      },
    ];
    const transactions = [
      {
        id: 'topup-91',
        kind: 'topup',
        title: 'Пополнение #91',
        amount: 15,
        amount_prefix: '+',
        created_at: '2026-06-28 13:55:00',
        subtitle: 'USDT',
      },
      {
        id: 'order-1842',
        kind: 'order',
        title: 'Мобильные прокси',
        amount: 8.5,
        amount_prefix: '-',
        created_at: '2026-06-28 14:32:00',
        subtitle: 'Прокси',
      },
    ];
    const flatCategories = [
      { id: 101, name: 'Прокси', parent_name: '' },
      { id: 102, name: 'Telegram', parent_name: 'Аккаунты' },
      { id: 103, name: 'Premium', parent_name: 'Аккаунты' },
      { id: 104, name: 'SMS', parent_name: 'Сервисы' },
    ];
    const productMap = {
      101: [
        {
          id: 5001,
          title: 'IPv4 прокси RU',
          description: 'Быстрая выдача и стабильное подключение.',
          quantity: 128,
          sales_count: 342,
          price_rub: 250,
          price_usdt: 5,
          attributes: ['IPv4', 'Россия', '1 день'],
        },
        {
          id: 5002,
          title: 'Мобильные прокси EU',
          description: 'Подходит для фарма, рекламы и рабочих кабинетов.',
          quantity: 64,
          sales_count: 117,
          price_rub: 425,
          price_usdt: 8.5,
          attributes: ['Mobile', 'EU', 'Ротация'],
        },
      ],
      102: [
        {
          id: 5101,
          title: 'Telegram аккаунт RU',
          description: 'Готовый аккаунт для старта сразу после покупки.',
          quantity: 37,
          sales_count: 91,
          price_rub: 550,
          price_usdt: 11,
          attributes: ['RU', 'Новый', 'SMS включен'],
        },
      ],
      103: [
        {
          id: 5201,
          title: 'Telegram Premium 3 мес.',
          description: 'Подписка с быстрой выдачей в рабочем чате.',
          quantity: 19,
          sales_count: 53,
          price_rub: 890,
          price_usdt: 17.8,
          attributes: ['3 месяца', 'Мгновенно'],
        },
      ],
      104: [
        {
          id: 5301,
          title: 'SMS-аренда номера',
          description: 'Одноразовый номер для регистрации сервисов.',
          quantity: 214,
          sales_count: 408,
          price_rub: 120,
          price_usdt: 2.4,
          attributes: ['SMS', 'Аренда', 'Онлайн'],
        },
      ],
    };

    if (path.includes('/api/bootstrap')) {
      return {
        brand: {
          name: brandName,
          badge: buildBadge(brandName),
          avatar_url: '',
          subtitle: `@${botUsername}`,
        },
        bot: {
          username: botUsername,
          is_partner: state.botSlug !== 'main',
          support_url: DEFAULT_SUPPORT_URL,
          menu_url: '',
        },
        user: {
          id: 1000001,
          username: 'preview_user',
          first_name: 'Preview',
          last_name: '',
        },
        profile: {
          user_id: 1000001,
          balance: 24.75,
          purchases_count: orders.length,
          purchases_total: 8.5,
          topups_total: 15,
          referrals_count: 3,
          referral_earnings: 5.4,
          registered_at: '2026-06-20 12:00:00',
          referral_link: `https://t.me/${botUsername}?start=r_preview`,
          referral_percent: 50,
          discount_percent: 0,
          discount_code: '',
        },
        orders,
        orders_count: orders.length,
        transactions,
        rules_text: '1. Проверьте товар сразу после выдачи.\n2. Спорные ситуации решаются через текущего бота.\n3. Для пополнения и покупки используйте кнопки внутри mini app или чата.',
      };
    }

    if (path.includes('/api/catalog')) {
      return {
        sections: [
          { id: 1, name: 'Прокси', items: [flatCategories[0]] },
          { id: 2, name: 'Аккаунты', items: [flatCategories[1], flatCategories[2]] },
          { id: 3, name: 'Сервисы', items: [flatCategories[3]] },
        ],
        flat: flatCategories,
        margin_percentage: marginPercentage,
      };
    }

    if (path.includes('/api/products')) {
      const items = productMap[categoryId] || [];
      return {
        items,
        page,
        last_page: 1,
        total: items.length,
        margin_percentage: marginPercentage,
      };
    }

    if (path.includes('/api/orders')) {
      return {
        items: orders,
        page: 1,
        per_page: 10,
        total: orders.length,
      };
    }

    if (path.includes('/api/transactions')) {
      return { items: transactions };
    }

    return null;
  }

  function shouldUsePreviewFallback(errorMessage) {
    if (state.initData) {
      return false;
    }

    const normalized = String(errorMessage || '').toLowerCase();
    const isLocalPreviewHost = window.location.protocol === 'file:' || ['localhost', '127.0.0.1'].includes(window.location.hostname);
    if (!isLocalPreviewHost) {
      return false;
    }

    return (
      normalized.includes('failed to fetch') ||
      normalized.includes('networkerror') ||
      normalized.includes('telegram initdata required') ||
      normalized.includes('request failed')
    );
  }

  async function api(path, options = {}) {
    const publicError = (value) => {
      const text = String(value || '').trim();
      // Do not show upstream/provider names or implementation diagnostics.
      if (!text || /too many attempts|too many requests|business_logic|offer_not_found|provider|supplier|traceback|exception|internal_error/i.test(text)) {
        return 'Не удалось выполнить запрос. Попробуйте обновить страницу позже.';
      }
      return text;
    };
    try {
      const response = await fetch(path, {
        method: options.method || 'GET',
        headers: {
          ...(state.initData ? { 'X-Telegram-Init-Data': state.initData } : {}),
          ...(options.body ? { 'Content-Type': 'application/json' } : {}),
          ...(options.headers || {}),
        },
        body: options.body ? JSON.stringify(options.body) : undefined,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ error: 'Request failed' }));
        if ((!options.method || options.method === 'GET') && shouldUsePreviewFallback(payload.error)) {
          const previewPayload = createPreviewPayload(path);
          if (previewPayload) {
            return deepClone(previewPayload);
          }
        }
        throw new Error(publicError(payload.error));
      }
      return response.json();
    } catch (error) {
      if ((!options.method || options.method === 'GET') && shouldUsePreviewFallback(error && error.message)) {
        const previewPayload = createPreviewPayload(path);
        if (previewPayload) {
          return deepClone(previewPayload);
        }
      }
      throw new Error(publicError(error && error.message));
    }
  }

  function formatMoney(value) {
    return `${Number(value || 0).toFixed(2)} $`;
  }

  function getCartStorageKey() {
    const userId = state.boot && state.boot.profile ? state.boot.profile.user_id : 'guest';
    return `miniapp-cart:${state.botSlug}:${userId}`;
  }

  function getCartItemsCount() {
    return state.cartItems.reduce((total, item) => total + Math.max(Number(item.quantity || 0), 0), 0);
  }

  function getCartTotalAmount() {
    return state.cartItems.reduce(
      (total, item) => total + (Number(item.price_usdt || 0) * Math.max(Number(item.quantity || 0), 0)),
      0,
    );
  }

  function renderCartButton() {
    if (!catalogCartButton || !catalogCartCount) {
      return;
    }
    const count = getCartItemsCount();
    catalogCartCount.textContent = String(count);
    catalogCartCount.classList.toggle('hidden', count <= 0);
    catalogCartButton.classList.toggle('catalog-cart-button-active', count > 0);
  }

  function loadCart() {
    try {
      const rawValue = window.localStorage.getItem(getCartStorageKey());
      const parsed = rawValue ? JSON.parse(rawValue) : [];
      state.cartItems = Array.isArray(parsed)
        ? parsed
            .filter((item) => item && Number(item.product_id || 0) > 0 && Number(item.quantity || 0) > 0)
            .map((item) => ({
              product_id: Number(item.product_id || 0),
              category_id: Number(item.category_id || 0),
              category_name: String(item.category_name || ''),
              title: String(item.title || 'Товар'),
              description: String(item.description || ''),
              quantity: Math.max(1, Number(item.quantity || 1)),
              quantity_available: Math.max(1, Number(item.quantity_available || 1)),
              price_usdt: Number(item.price_usdt || 0),
              attributes: Array.isArray(item.attributes) ? item.attributes : [],
            }))
        : [];
    } catch (_error) {
      state.cartItems = [];
    }
    renderCartButton();
  }

  function saveCart() {
    try {
      window.localStorage.setItem(getCartStorageKey(), JSON.stringify(state.cartItems));
    } catch (_error) {
      // Ignore storage errors inside Telegram WebView.
    }
    renderCartButton();
  }

  function triggerTextDownload(filename, content) {
    const blob = new Blob([String(content || '')], { type: 'text/plain;charset=utf-8' });
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }

  function addProductToCart(item, category, quantity) {
    const normalizedQuantity = Math.max(1, Number(quantity || 1));
    const availableQuantity = Math.max(1, Number(item.quantity || 1));
    const existingIndex = state.cartItems.findIndex((cartItem) => cartItem.product_id === Number(item.id));
    if (existingIndex >= 0) {
      const currentItem = state.cartItems[existingIndex];
      state.cartItems[existingIndex] = {
        ...currentItem,
        quantity: Math.min(currentItem.quantity + normalizedQuantity, availableQuantity),
        quantity_available: availableQuantity,
        price_usdt: Number(item.price_usdt || currentItem.price_usdt || 0),
        title: item.title || currentItem.title,
        description: item.description || currentItem.description,
        category_name: (category && category.name) || currentItem.category_name || '',
        attributes: Array.isArray(item.attributes) ? item.attributes : currentItem.attributes,
      };
    } else {
      state.cartItems.push({
        product_id: Number(item.id),
        category_id: Number(category && category.id ? category.id : state.selectedCategoryId || 0),
        category_name: (category && category.name) || '',
        title: String(item.title || 'Товар'),
        description: String(item.description || ''),
        quantity: Math.min(normalizedQuantity, availableQuantity),
        quantity_available: availableQuantity,
        price_usdt: Number(item.price_usdt || 0),
        attributes: Array.isArray(item.attributes) ? item.attributes : [],
      });
    }
    saveCart();
  }

  function updateCartItemQuantity(productId, nextQuantity) {
    const normalizedProductId = Number(productId || 0);
    const existingIndex = state.cartItems.findIndex((item) => item.product_id === normalizedProductId);
    if (existingIndex === -1) {
      return;
    }
    const currentItem = state.cartItems[existingIndex];
    const availableQuantity = Math.max(1, Number(currentItem.quantity_available || 1));
    const normalizedQuantity = Math.max(0, Math.min(Number(nextQuantity || 0), availableQuantity));
    if (normalizedQuantity <= 0) {
      state.cartItems = state.cartItems.filter((item) => item.product_id !== normalizedProductId);
    } else {
      state.cartItems[existingIndex] = {
        ...currentItem,
        quantity: normalizedQuantity,
      };
    }
    saveCart();
  }

  function buildOrderDetailMarkup(order, headingText) {
    const title = String(headingText || (order.status === 'delivered' ? 'Заказ выполнен' : 'Заказ оформлен'));
    const deliveryReady = Boolean(order.can_download && order.delivery_text);
    const isWaitingDelivery = isAwaitingDelivery(order);
    const statusToneClass = order.status === 'delivered'
      ? 'order-status-chip-success'
      : order.status === 'credited'
        ? 'order-status-chip-warning'
        : 'order-status-chip-muted';

    return `
      <div class="checkout-result-shell">
        <div class="checkout-result-hero">
          <div class="checkout-result-icon">${order.status === 'delivered' ? '✓' : '◌'}</div>
          <div>
            <h3 class="sheet-title checkout-result-title">${escapeHtml(title)}</h3>
            <p class="sheet-subtitle">${escapeHtml(order.category_name || 'Покупка через mini app')}</p>
          </div>
        </div>
        <div class="checkout-stats-grid">
          <div class="checkout-stat-card">
            <span>Номер заказа</span>
            <strong>${order.order_number ? `#${escapeHtml(order.order_number)}` : 'Оформляется'}</strong>
          </div>
          <div class="checkout-stat-card">
            <span>Дата создания</span>
            <strong>${escapeHtml(order.created_at || '')}</strong>
          </div>
          <div class="checkout-stat-card">
            <span>Статус заказа</span>
            <strong><span class="order-status-chip ${statusToneClass}">${escapeHtml(order.status_label || '')}</span></strong>
          </div>
          <div class="checkout-stat-card">
            <span>Сумма заказа</span>
            <strong>${formatMoney(order.total_price)}</strong>
          </div>
        </div>
        <div class="sheet-card checkout-product-card">
          <div class="checkout-product-head">
            <div>
              <strong>${escapeHtml(order.title || 'Товар')}</strong>
              <p>${escapeHtml(order.category_name || '')}</p>
            </div>
            <span class="order-status-chip ${statusToneClass}">${escapeHtml(order.status_label || '')}</span>
          </div>
          <div class="checkout-product-meta">
            <span>${escapeHtml(order.quantity)} шт.</span>
            <span>${formatMoney(order.total_price)}</span>
          </div>
          ${isWaitingDelivery ? buildWaitingDeliveryMarkup(order.id) : ''}
          <div class="checkout-action-grid">
            ${deliveryReady ? '<button class="checkout-action-button" type="button" id="preview-order-data">Быстрый просмотр</button>' : ''}
            ${deliveryReady ? '<button class="checkout-action-button" type="button" id="download-order-data">Скачать данные файлом</button>' : ''}
          </div>
        </div>
      </div>
    `;
  }

  function openDeliveryPreview(order) {
        openOverlay(`
      <h3 class="sheet-title">Данные заказа</h3>
      <p class="sheet-subtitle">${escapeHtml(order.title || 'Товар')} • Заказ ${order.order_number ? `#${escapeHtml(order.order_number)}` : 'оформляется'}</p>
      <div class="sheet-card">
        <pre class="delivery-preview-block">${escapeHtml(order.delivery_text || '')}</pre>
      </div>
        `);
  }

  function renderOrderDetailOverlay(order, headingText) {
    openOverlay(buildOrderDetailMarkup(order, headingText));
    const previewButton = document.getElementById('preview-order-data');
    const downloadButton = document.getElementById('download-order-data');

    if (previewButton) {
      previewButton.addEventListener('click', () => openDeliveryPreview(order));
    }
    if (downloadButton) {
      downloadButton.addEventListener('click', () => {
        triggerTextDownload(`order-${order.id}.txt`, order.delivery_text || '');
      });
    }
    if (isAwaitingDelivery(order)) {
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
        <p class="sheet-subtitle">Покупка создана, но данные заказа пока недоступны.</p>
      `);
      return;
    }

    const title = orders.length > 1 ? 'Корзина оформлена' : (orders[0].status === 'delivered' ? 'Заказ выполнен' : 'Заказ оформлен');
    const cardsMarkup = orders.map((order) => `
      <div class="sheet-card checkout-product-card" data-order-card="${escapeHtml(order.id)}">
        <div class="checkout-product-head">
          <div>
            <strong>${escapeHtml(order.title || 'Товар')}</strong>
            <p>${escapeHtml(order.category_name || '')}</p>
          </div>
          <span class="order-status-chip ${order.status === 'delivered' ? 'order-status-chip-success' : order.status === 'credited' ? 'order-status-chip-warning' : 'order-status-chip-muted'}">${escapeHtml(order.status_label || '')}</span>
        </div>
        <div class="checkout-product-meta">
          <span>Заказ ${order.order_number ? `#${escapeHtml(order.order_number)}` : 'оформляется'}</span>
          <span>${formatMoney(order.total_price)}</span>
          <span>${escapeHtml(order.created_at || '')}</span>
        </div>
        <div class="checkout-action-grid">
          ${order.can_download && order.delivery_text ? `<button class="checkout-action-button" type="button" data-preview-order="${escapeHtml(order.id)}">Быстрый просмотр</button>` : ''}
          ${order.can_download && order.delivery_text ? `<button class="checkout-action-button" type="button" data-download-order="${escapeHtml(order.id)}">Скачать данные файлом</button>` : ''}
        </div>
        ${isAwaitingDelivery(order) ? buildWaitingDeliveryMarkup(order.id, true) : ''}
      </div>
    `).join('');

    openOverlay(`
      <div class="checkout-result-shell">
        <div class="checkout-result-hero">
          <div class="checkout-result-icon">${orders.every((order) => order.status === 'delivered') ? '✓' : '◌'}</div>
          <div>
            <h3 class="sheet-title checkout-result-title">${escapeHtml(title)}</h3>
            <p class="sheet-subtitle">Заказы оформлены внутри mini app.</p>
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
          triggerTextDownload(`order-${order.id}.txt`, order.delivery_text || '');
        }
      });
    });
    const waitingOrder = orders.find((order) => isAwaitingDelivery(order));
    if (waitingOrder) {
      startOrderWaitPoll(waitingOrder.id, (updatedOrder) => {
        renderOrderDetailOverlay(
          updatedOrder,
          updatedOrder.status === 'delivered' ? t('order_completed') : t('order_status'),
        );
      });
    }
  }

  async function openOrderDetails(orderId, options = {}) {
    openOverlay(`<h3 class="sheet-title">${escapeHtml(t('order_title'))}</h3><p class="sheet-subtitle">${escapeHtml(t('loading'))}</p>`);
    try {
      const payload = options.refresh
        ? await api(`${state.basePath}/api/orders/${encodeURIComponent(orderId)}/refresh`, { method: 'POST' })
        : await api(`${state.basePath}/api/orders/${encodeURIComponent(orderId)}`);
      if (payload && payload.profile && state.boot) {
        state.boot.profile = payload.profile;
        renderBootstrap();
      }
      if (!payload || !payload.order) {
        throw new Error(t('order_load_failed'));
      }
      await refreshBootstrapSilently();
      renderOrderDetailOverlay(payload.order, payload.order.status === 'delivered' ? t('order_completed') : t('order_status'));
    } catch (error) {
      openOverlay(`
        <h3 class="sheet-title">${escapeHtml(t('order_title'))}</h3>
        <p class="sheet-subtitle">${escapeHtml(error.message || t('order_load_failed'))}</p>
      `);
    }
  }

  function openCartOverlay(errorMessage = '') {
    const totalAmount = getCartTotalAmount();
    const balance = Number((state.boot && state.boot.profile && state.boot.profile.balance) || 0);
    const checkoutInFlight = state.balanceCheckoutInFlight === true;
    const canPay = state.cartItems.length > 0 && balance >= totalAmount && !checkoutInFlight;
    const needsTopup = state.cartItems.length > 0 && !checkoutInFlight && balance < totalAmount;
    const cartRows = state.cartItems.map((item) => `
      <div class="sheet-card cart-item-card" data-cart-item="${escapeHtml(item.product_id)}">
        <div class="cart-item-head">
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <p>${escapeHtml(item.category_name || '')}</p>
          </div>
          <button class="cart-remove-button" type="button" data-remove-cart-item="${escapeHtml(item.product_id)}">×</button>
        </div>
        <div class="cart-item-meta">
          <span>${formatMoney(item.price_usdt)} / шт.</span>
          <span>Остаток: ${escapeHtml(item.quantity_available)} шт.</span>
        </div>
        <div class="cart-item-footer">
          <div class="cart-qty-control">
            <button type="button" data-cart-dec="${escapeHtml(item.product_id)}">−</button>
            <span>${escapeHtml(item.quantity)}</span>
            <button type="button" data-cart-inc="${escapeHtml(item.product_id)}">+</button>
          </div>
          <strong>${formatMoney(Number(item.price_usdt || 0) * Number(item.quantity || 0))}</strong>
        </div>
      </div>
    `).join('');

    openOverlay(`
      <h3 class="sheet-title">Корзина</h3>
      <p class="sheet-subtitle">Покупка товаров прямо внутри mini app.</p>
      ${errorMessage ? `<div class="sheet-card cart-message-card">${escapeHtml(errorMessage)}</div>` : ''}
      ${state.cartItems.length ? cartRows : '<div class="sheet-card cart-message-card">Корзина пока пустая.</div>'}
      <div class="sheet-card cart-summary-card">
        <div class="cart-summary-row"><span>Товаров</span><strong>${escapeHtml(getCartItemsCount())}</strong></div>
        <div class="cart-summary-row"><span>Сумма</span><strong>${formatMoney(totalAmount)}</strong></div>
        <div class="cart-summary-row"><span>Баланс</span><strong>${formatMoney(balance)}</strong></div>
        <button class="primary-button cart-checkout-button" type="button" id="cart-balance-checkout" ${canPay ? '' : 'disabled'}>
          ${state.cartItems.length ? (checkoutInFlight ? replaceDynamicText('Оформляем заказ...') : (canPay ? 'Оплатить с баланса' : 'Недостаточно баланса')) : 'Корзина пуста'}
        </button>
        ${needsTopup ? '<button class="ghost-button cart-topup-button" type="button" id="cart-open-topup">Пополнить баланс</button>' : ''}
      </div>
    `);

    overlayContent.querySelectorAll('[data-cart-dec]').forEach((button) => {
      button.addEventListener('click', () => {
        const productId = Number(button.getAttribute('data-cart-dec') || 0);
        const item = state.cartItems.find((row) => row.product_id === productId);
        if (item) {
          updateCartItemQuantity(productId, item.quantity - 1);
          openCartOverlay();
        }
      });
    });
    overlayContent.querySelectorAll('[data-cart-inc]').forEach((button) => {
      button.addEventListener('click', () => {
        const productId = Number(button.getAttribute('data-cart-inc') || 0);
        const item = state.cartItems.find((row) => row.product_id === productId);
        if (item) {
          updateCartItemQuantity(productId, item.quantity + 1);
          openCartOverlay();
        }
      });
    });
    overlayContent.querySelectorAll('[data-remove-cart-item]').forEach((button) => {
      button.addEventListener('click', () => {
        const productId = Number(button.getAttribute('data-remove-cart-item') || 0);
        updateCartItemQuantity(productId, 0);
        openCartOverlay();
      });
    });

    const checkoutButton = document.getElementById('cart-balance-checkout');
    const topupButton = document.getElementById('cart-open-topup');
    if (topupButton) {
      topupButton.addEventListener('click', () => {
        openTopupHelp();
      });
    }
    if (checkoutButton && canPay) {
      checkoutButton.addEventListener('click', async () => {
        if (state.balanceCheckoutInFlight) {
          return;
        }
        state.balanceCheckoutInFlight = true;
        checkoutButton.disabled = true;
        checkoutButton.textContent = replaceDynamicText('Оформляем заказ...');
        try {
          const payload = await api(`${state.basePath}/api/checkout/balance`, {
            method: 'POST',
            body: {
              items: state.cartItems.map((item) => ({
                product_id: item.product_id,
                category_id: item.category_id,
                category_name: item.category_name,
                quantity: item.quantity,
              })),
            },
          });
          state.cartItems = [];
          saveCart();
          if (payload && payload.profile && state.boot) {
            state.boot.profile = payload.profile;
          }
          await refreshBootstrapSilently();
          switchScreen('orders');
          renderCheckoutResultOverlay(payload.orders || []);
        } catch (error) {
          state.balanceCheckoutInFlight = false;
          openCartOverlay(error && error.message ? error.message : 'Не удалось оформить корзину.');
          return;
        } finally {
          state.balanceCheckoutInFlight = false;
        }
      });
    }
  }

  async function refreshBootstrapSilently() {
    if (bootstrapRefreshInFlight) {
      return bootstrapRefreshInFlight;
    }
    bootstrapRefreshInFlight = (async () => {
      try {
        const payload = await api(`${state.basePath}/api/bootstrap`);
        state.boot = payload;
        renderBootstrap();
      } catch (_error) {
        // Silent refresh is best-effort only.
      } finally {
        bootstrapRefreshInFlight = null;
      }
    })();
    return bootstrapRefreshInFlight;
  }

  function renderBootstrap() {
    if (!state.boot) return;
    state.language = normalizeLanguageCode(state.boot.profile ? state.boot.profile.language_code : state.language);
    applyStaticTranslations();
    brandName.textContent = state.boot.brand.name || 'SOUS MARKET';
    const brandAvatarUrl = String(state.boot.brand.avatar_url || '').trim();
    if (brandAvatarUrl) {
      brandBadge.innerHTML = `<img src="${escapeHtml(brandAvatarUrl)}" alt="${escapeHtml(state.boot.brand.name || 'Bot avatar')}" />`;
      brandBadge.classList.add('brand-badge-has-image');
    } else {
      brandBadge.textContent = state.boot.brand.badge || 'SM';
      brandBadge.classList.remove('brand-badge-has-image');
    }
    brandSubtitle.textContent = state.boot.brand.subtitle || '';
    if (botFooter) {
      botFooter.textContent = '';
    }
    balanceValue.textContent = `${(state.boot.profile.balance || 0).toFixed(2)} $`;
    profileOrdersCount.textContent = String(state.boot.profile.purchases_count || 0);
    renderOrders(state.boot.orders || []);
    renderCartButton();
  }

  function renderOrders(items) {
    ordersList.innerHTML = '';
    const hasItems = Array.isArray(items) && items.length > 0;
    ordersEmpty.classList.toggle('hidden', hasItems);
    ordersStatus.textContent = hasItems ? '' : t('orders_status_idle');
    if (!hasItems) {
      return;
    }
    items.forEach((item) => {
      const card = document.createElement('article');
      card.className = 'order-card';
      card.innerHTML = `
        <div class="order-head">
          <div>
            <h3>${escapeHtml(item.title)}</h3>
            <div class="order-subtitle">${escapeHtml(item.category_name || '')}</div>
          </div>
          <span class="status-pill">${escapeHtml(item.status_label || '')}</span>
        </div>
        <div class="order-meta-line">
          ${t('order_label')} ${item.order_number ? `#${escapeHtml(item.order_number)}` : 'оформляется'} • ${Number(item.total_price || 0).toFixed(2)} $ • ${escapeHtml(item.quantity)} ${t('quantity_short')}
        </div>
        <div class="order-meta-line">${escapeHtml(item.created_at || '')}</div>
      `;
      card.addEventListener('click', async () => openOrderDetails(item.id));
      ordersList.appendChild(card);
    });
    localizeDynamicContent(ordersList);
  }

  async function loadCatalog() {
    catalogStatus.textContent = t('catalog_loading');
    try {
      const payload = await api(`${state.basePath}/api/catalog`);
      state.sections = payload.sections || [];
      state.categories = payload.flat || [];
      state.selectedCategoryId = null;
      state.expandedSectionIds = [];
      state.rawProductItems = [];
      state.productItems = [];
      state.productMeta = null;
      resetPendingFilters();
      resetActiveFilters();
      syncFilterInputs();
      renderCategoryFilterOptions();
      renderAttributeFilters();
      renderCategoryDirectory();
      updateCatalogSelection();
      renderCatalogPlaceholder(t('catalog_empty_pick'));
      if (!state.categories.length) {
        catalogStatus.textContent = t('catalog_categories_unavailable');
        return;
      }
      catalogStatus.textContent = '';
    } catch (error) {
      catalogStatus.textContent = error.message || t('catalog_load_failed');
    }
  }

  async function loadProducts() {
    if (!state.selectedCategoryId) {
      state.rawProductItems = [];
      state.productItems = [];
      state.productMeta = null;
      renderAttributeFilters();
      renderCatalogPlaceholder(t('catalog_empty_pick'));
      updateCatalogSelection();
      return;
    }
    const selectedCategory = getSelectedCategory();
    if (selectedCategory && selectedCategory.kind === 'proxy_redirect') {
      state.rawProductItems = [];
      state.productItems = [];
      state.productMeta = null;
      renderAttributeFilters();
      renderProxyEntryCard(selectedCategory);
      catalogStatus.textContent = '';
      updateCatalogSelection();
      return;
    }
    updateCatalogSelection();
    renderCatalogPagination();
    catalogStatus.textContent = t('catalog_loading_products');
    try {
      const payload = await api(`${state.basePath}/api/products?category_id=${encodeURIComponent(state.selectedCategoryId)}&page=${state.productsPage}`);
      state.rawProductItems = payload.items || [];
      state.productMeta = {
        total: Number(payload.total || 0),
        page: Number(payload.page || 1),
        lastPage: Number(payload.last_page || 1),
        marginPercentage: Number(payload.margin_percentage || 0),
      };
      renderAttributeFilters();
      applyProductFilters();
      renderCatalogPagination();
      catalogStatus.textContent = '';
    } catch (error) {
      state.rawProductItems = [];
      state.productItems = [];
      state.productMeta = null;
      catalogStatus.textContent = error.message || t('catalog_load_products_failed');
      renderAttributeFilters();
      renderCatalogPlaceholder(t('catalog_load_category_failed'));
    }
  }

  function bindCatalogFilters() {
    catalogSearch?.addEventListener('input', () => {
      state.searchQuery = catalogSearch.value.trim();
      if (state.searchTimer) window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(async () => {
        const query = state.searchQuery;
        if (!query) {
          applyProductFilters();
          return;
        }
        const rankedCategories = state.categories
          .map((category) => ({
            category,
            score: productSearchScore(
              {
                title: category.name,
                description: category.parent_name,
                attributes: [],
              },
              query,
            ),
          }))
          .filter((row) => row.score > 0)
          .sort((left, right) => right.score - left.score);
        const currentCategory = getSelectedCategory();
        const currentCategoryScore = currentCategory
          ? productSearchScore({ title: currentCategory.name, description: currentCategory.parent_name, attributes: [] }, query)
          : 0;
        if (rankedCategories.length && (!currentCategory || currentCategoryScore <= 0)) {
          await selectCategory(rankedCategories[0].category.id, { scroll: false });
          return;
        }
        applyProductFilters();
      }, 260);
    });
    catalogTopFavorites?.addEventListener('click', async () => {
      catalogStatus.textContent = 'Загрузка избранного…';
      try {
        const payload = await api(`${state.basePath}/api/favorites`);
        state.selectedCategoryId = null;
        state.rawProductItems = payload.items || [];
        state.productItems = state.rawProductItems;
        state.productMeta = { total: Number(payload.total || 0), page: 1, lastPage: 1 };
        state.favoritesOnly = true;
        catalogSelectionTitle.textContent = 'Избранное';
        catalogSelectionSubtitle.textContent = 'Все сохранённые товары';
        catalogFavoritesFilter?.classList.add('catalog-favorites-filter-active');
        catalogFavoritesFilter?.setAttribute('aria-pressed', 'true');
        if (catalogFavoritesFilter) catalogFavoritesFilter.textContent = '♥ Избранное';
        renderProducts(state.rawProductItems);
        catalogStatus.textContent = state.rawProductItems.length ? '' : 'В избранном пока нет товаров.';
        scrollCatalogProductsIntoView();
      } catch (error) {
        catalogStatus.textContent = error.message || 'Не удалось загрузить избранное';
      }
    });
    catalogFavoritesFilter?.addEventListener('click', () => {
      state.favoritesOnly = !state.favoritesOnly;
      catalogFavoritesFilter.classList.toggle('catalog-favorites-filter-active', state.favoritesOnly);
      catalogFavoritesFilter.setAttribute('aria-pressed', state.favoritesOnly ? 'true' : 'false');
      catalogFavoritesFilter.textContent = `${state.favoritesOnly ? '♥' : '♡'} Избранное`;
      applyProductFilters();
    });
    catalogFilterToggle.addEventListener('click', () => {
      state.filtersExpanded = !state.filtersExpanded;
      updateFilterPanelVisibility();
    });
    catalogCategoryFilter.addEventListener('change', async () => {
      await selectCategory(catalogCategoryFilter.value, { scroll: true });
    });
    catalogPriceMin.addEventListener('input', () => {
      state.pendingFilters.priceMin = catalogPriceMin.value.trim();
    });
    catalogPriceMax.addEventListener('input', () => {
      state.pendingFilters.priceMax = catalogPriceMax.value.trim();
    });
    catalogApplyFilters.addEventListener('click', () => {
      state.activeFilters = {
        priceMin: state.pendingFilters.priceMin,
        priceMax: state.pendingFilters.priceMax,
        attributeTokens: [...state.pendingFilters.attributeTokens],
      };
      applyProductFilters();
    });
    catalogResetFilters.addEventListener('click', () => {
      resetPendingFilters();
      resetActiveFilters();
      syncFilterInputs();
      renderAttributeFilters();
      applyProductFilters();
    });
    catalogSortFilter.addEventListener('change', () => {
      state.sortMode = catalogSortFilter.value || 'popular';
      if (!state.selectedCategoryId) {
        renderCatalogPlaceholder(t('catalog_empty_products'));
        return;
      }
      applyProductFilters();
    });
  }

  function bindCatalogCartActions() {
    catalogCartButton?.addEventListener('click', () => {
      openCartOverlay();
    });
  }

  function bindProfileActions() {
    document.getElementById('topup-trigger').addEventListener('click', openTopupHelp);
    document.getElementById('profile-topup').addEventListener('click', openTopupHelp);
    document.querySelectorAll('.menu-row').forEach((button) => {
      button.addEventListener('click', async () => {
        const action = button.dataset.action;
        if (action === 'referral') {
          openReferral();
          return;
        }
        if (action === 'transactions') {
          await openTransactions();
          return;
        }
        if (action === 'promocode') {
          openPromoCodeSheet();
          return;
        }
        if (action === 'support') {
          openSupport();
          return;
        }
        if (action === 'rules') {
          openRules();
        }
      });
    });
  }

  function openTopupHelp() {
    openOverlay(`
      <h3 class="sheet-title">Пополнение</h3>
      <p class="sheet-subtitle">Введите сумму и выберите способ оплаты.</p>
      <div class="sheet-card topup-sheet-card">
        <strong>Текущий баланс: ${Number((state.boot && state.boot.profile && state.boot.profile.balance) || 0).toFixed(2)} $</strong>
        <div class="topup-form">
          <label class="topup-field">
            <span>Сумма</span>
            <input id="topup-amount" type="text" inputmode="decimal" placeholder="0.10" autocomplete="off" />
          </label>
          <button class="primary-button topup-submit-button" type="button" id="show-topup-methods">Пополнить</button>
        </div>
        <div id="topup-methods" class="topup-methods hidden">
          <button class="topup-method-button" type="button" data-provider="lolz">LOLZ</button>
          <button class="topup-method-button" type="button" data-provider="heleket">Heleket</button>
          <button class="topup-method-button" type="button" data-provider="xrocket">XRocket</button>
          <button class="topup-method-button" type="button" data-provider="crystalpay">CryptoBot</button>
        </div>
      </div>
    `);
    const submitButton = document.getElementById('show-topup-methods');
    const amountInput = document.getElementById('topup-amount');
    const methodsBox = document.getElementById('topup-methods');
    if (submitButton && amountInput && methodsBox) {
      amountInput.addEventListener('blur', () => {
        const amount = parseMoneyInput(amountInput.value);
        if (Number.isFinite(amount) && amount > 0) {
          amountInput.value = amount.toFixed(amount < 1 ? 2 : 2).replace(/\.00$/, '');
        }
      });
      submitButton.addEventListener('click', () => {
        const amount = parseMoneyInput(amountInput.value);
        if (!Number.isFinite(amount) || amount <= 0) {
          amountInput.focus();
          return;
        }
        methodsBox.classList.remove('hidden');
      });
      methodsBox.querySelectorAll('.topup-method-button').forEach((button) => {
        button.addEventListener('click', async () => {
          const amount = parseMoneyInput(amountInput.value);
          if (!Number.isFinite(amount) || amount <= 0) {
            amountInput.focus();
            return;
          }
          const originalText = button.textContent;
          button.disabled = true;
          button.textContent = 'Создаём счёт...';
          try {
            const payload = await api(`${state.basePath}/api/topup`, {
              method: 'POST',
              body: {
                amount,
                provider: button.dataset.provider,
              },
            });
            const payUrl = normalizeExternalUrl(payload && payload.pay_url ? payload.pay_url : '', { allowTelegramScheme: true });
            if (!payUrl) {
              throw new Error('Провайдер не вернул ссылку на оплату');
            }
            button.textContent = `${originalText} • ${amount.toFixed(2)} $`;
            if (tg && /^https?:\/\/t\.me\//i.test(payUrl)) {
              tg.openTelegramLink(payUrl);
              return;
            }
            if (tg && typeof tg.openLink === 'function' && /^https?:\/\//i.test(payUrl)) {
              tg.openLink(payUrl);
              return;
            }
            if (/^tg:\/\//i.test(payUrl) || /^https?:\/\/t\.me\//i.test(payUrl)) {
              window.location.href = payUrl;
              return;
            }
            if (/^https?:\/\//i.test(payUrl)) {
              window.open(payUrl, '_blank', 'noopener');
              return;
            }
            throw new Error('Провайдер вернул небезопасную ссылку на оплату');
          } catch (error) {
            button.textContent = error && error.message ? error.message : 'Ошибка счёта';
            window.setTimeout(() => {
              button.textContent = originalText;
              button.disabled = false;
            }, 2200);
          }
        });
      });
    }
  }

  function openPromoCodeSheet(feedbackMessage = '', feedbackKind = '', initialCode = '') {
    const profile = (state.boot && state.boot.profile) || {};
    const discountPercent = Number(profile.discount_percent || 0);
    const discountCode = String(profile.discount_code || '').trim();
    const hasActiveDiscount = discountPercent > 0 && discountCode;
    openOverlay(`
      <h3 class="sheet-title">Промокод</h3>
      <p class="sheet-subtitle">Активируйте код и получите баланс или скидку на следующий заказ.</p>
      <div class="sheet-card topup-sheet-card promo-sheet-card">
        <div class="promo-status-card ${hasActiveDiscount ? 'promo-status-card-active' : ''}">
          ${
            hasActiveDiscount
              ? `<strong>Активна скидка ${discountPercent.toFixed(2)}%</strong><p>Код: ${escapeHtml(discountCode)}</p>`
              : '<strong>Активной скидки пока нет</strong><p>Если код даёт скидку, она сохранится до следующего заказа.</p>'
          }
        </div>
        ${feedbackMessage ? `<div class="promo-feedback ${feedbackKind === 'success' ? 'promo-feedback-success' : 'promo-feedback-error'}">${escapeHtml(feedbackMessage)}</div>` : ''}
        <label class="topup-field">
          <span>Промокод</span>
          <input id="promocode-input" type="text" placeholder="Введите промокод" autocomplete="off" value="${escapeHtml(initialCode)}" />
        </label>
        <button class="primary-button topup-submit-button" type="button" id="promocode-apply-button">Активировать</button>
      </div>
    `);

    const input = document.getElementById('promocode-input');
    const button = document.getElementById('promocode-apply-button');
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
        const payload = await api(`${state.basePath}/api/promocode/activate`, {
          method: 'POST',
          body: { code },
        });
        if (payload && payload.profile && state.boot) {
          state.boot.profile = payload.profile;
          renderBootstrap();
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

  function openReferral() {
    const profile = state.boot.profile;
    openOverlay(`
      <h3 class="sheet-title">Реферальная программа</h3>
      <p class="sheet-subtitle">Реферальная ссылка</p>
      <div class="sheet-card">
        <strong>${Number(profile.referral_percent || 0).toFixed(2)}% от дохода с покупок приглашённых пользователей</strong>
        <p class="sheet-subtitle">Приглашено: ${profile.referrals_count} • Заработано: ${Number(profile.referral_earnings || 0).toFixed(2)} $</p>
        <div class="copy-box">
          <input id="ref-link" type="text" readonly value="${escapeHtml(profile.referral_link || '')}" />
          <button id="copy-ref-link" class="ghost-button" type="button">Копировать</button>
        </div>
      </div>
    `);
    const copyButton = document.getElementById('copy-ref-link');
    const refInput = document.getElementById('ref-link');
    if (copyButton && refInput) {
      copyButton.addEventListener('click', async () => {
        await navigator.clipboard.writeText(refInput.value);
        copyButton.textContent = 'Скопировано';
      });
    }
  }

  async function openTransactions() {
    openOverlay('<h3 class="sheet-title">История операций</h3><p class="sheet-subtitle">Загрузка…</p>');
    try {
      const payload = await api(`${state.basePath}/api/transactions`);
      const rows = (payload.items || []).map((item) => `
        <div class="sheet-card transaction-row">
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <span>${escapeHtml(item.subtitle || '')} • ${escapeHtml(item.created_at || '')}</span>
          </div>
          <div class="transaction-amount">${escapeHtml(item.amount_prefix)}${Number(item.amount || 0).toFixed(2)} $</div>
        </div>
      `).join('');
      openOverlay(`
        <h3 class="sheet-title">История операций</h3>
        <p class="sheet-subtitle">Пополнения и покупки.</p>
        ${rows || '<div class="sheet-card">Операций пока нет.</div>'}
      `);
    } catch (error) {
      openOverlay(`
        <h3 class="sheet-title">История операций</h3>
        <p class="sheet-subtitle">${escapeHtml(error.message || 'Не удалось загрузить операции.')}</p>
      `);
    }
  }

  function openSupport() {
    const supportUrl = (state.boot && state.boot.bot ? state.boot.bot.support_url : '') || DEFAULT_SUPPORT_URL;
    if (openExternalUrl(supportUrl)) {
      return;
    }
    openOverlay(`
      <h3 class="sheet-title">Поддержка</h3>
      <p class="sheet-subtitle">Ссылка на поддержку для этого бота не настроена или имеет небезопасный формат.</p>
    `);
  }

  function openRules() {
    openOverlay(`
      <h3 class="sheet-title">Правила</h3>
      <p class="sheet-subtitle">${escapeHtml(state.boot.rules_text || '')}</p>
    `);
  }

  function bindLifecycleRefresh() {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        refreshBootstrapSilently();
      }
    });
    window.addEventListener('focus', () => {
      refreshBootstrapSilently();
    });
    window.addEventListener('pageshow', () => {
      refreshBootstrapSilently();
    });
  }

  async function bootstrap() {
    try {
      state.boot = await api(`${state.basePath}/api/bootstrap`);
      loadCart();
      renderBootstrap();
      updateFilterPanelVisibility();
      bindProfileActions();
      bindCatalogFilters();
      bindCatalogCartActions();
      bindLifecycleRefresh();
      await loadCatalog();
    } catch (error) {
      document.body.innerHTML = `
        <main class="app-shell">
          <section class="screen screen-active">
            <div class="section-head">
              <span class="section-kicker">Mini App</span>
              <h2>Ошибка запуска</h2>
              <p>${escapeHtml(error.message || 'Не удалось инициализировать mini app.')}</p>
            </div>
          </section>
        </main>
      `;
      localizeDynamicContent(document.body);
    }
  }

  bootstrap();
})();
