/*
 * fomo-pump.js — привязка модели к сохранённому DOM страницы токена.
 *
 * Разметка не менялась: скрипт находит элементы по их подписям
 * («Market cap», «Liquidity», «No holders yet» и т.д.) и обновляет значения.
 *
 * Цифры страницы (шапка, About, таблицы, футер) берутся из одного слепка
 * sim.dexFeed() и обновляются ровно раз в CFG.uiMs (по умолчанию 1 секунда).
 * График живёт отдельно и перерисовывается каждые CFG.tickMs.
 */
(function () {
  'use strict';

  var CFG = window.FOMO_CFG;
  var F = window.F;
  var U = window.FomoUtil;
  var sim = new window.FomoSim();
  var SYM = CFG.token.symbol;
  var NAME = CFG.token.name;
  var ADDRESS = sim.address;
  var SHORT = sim.shortAddress;

  /* ------------------------------------------------------------ поиск в DOM */

  function txt(el) { return (el.textContent || '').trim(); }

  function findByText(selector, text, root) {
    var nodes = (root || document).querySelectorAll(selector);
    for (var i = 0; i < nodes.length; i++) {
      if (txt(nodes[i]) === text) return nodes[i];
    }
    return null;
  }

  function deltaBox(root) {
    return root ? root.querySelector('div[class*="gap-0.75"]') : null;
  }

  /* --------------------------------------------- тикер / название / адрес */

  /**
   * Значения <number-flow-react> лежат в снимке внутри shadow DOM
   * (data-scrapbook-shadowdom) и без библиотеки не отображаются.
   * Собираем строку обратно: символы валюты/разделители + текущая цифра
   * каждого разряда. Так «cash» показывает $0,00, как на живом сайте.
   */
  function renderNumberFlows(root) {
    var nodes = (root || document).querySelectorAll('number-flow-react');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var raw = el.getAttribute('data-scrapbook-shadowdom');
      if (!raw || el.getAttribute('data-fomo-rendered')) continue;
      var box = document.createElement('div');
      // <style> из shadow DOM нельзя вносить в документ — он не изолирован
      box.innerHTML = raw.replace(/<style[\s\S]*?<\/style>/gi, '');
      var parts = box.querySelectorAll('.symbol__value, .digit');
      var out = '';
      for (var j = 0; j < parts.length; j++) {
        var p = parts[j];
        if (p.classList.contains('digit')) {
          out += (p.style.getPropertyValue('--current') || '').trim() || '0';
        } else {
          out += p.textContent;
        }
      }
      if (out) {
        el.textContent = out;
        el.setAttribute('data-fomo-rendered', '1');
      }
    }
  }

  /** Замена текста в готовой вёрстке: тикер, название, короткий адрес. */
  function retext(map) {
    var keys = [];
    Object.keys(map).forEach(function (k) { if (k && map[k] !== k) keys.push(k); });
    if (!keys.length) return;
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var tag = n.parentNode ? n.parentNode.nodeName : '';
        if (tag === 'SCRIPT' || tag === 'STYLE') return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var node;
    while ((node = walker.nextNode())) {
      var s = node.nodeValue;
      if (!s) continue;
      var out = s;
      for (var i = 0; i < keys.length; i++) {
        if (out.indexOf(keys[i]) >= 0) out = out.split(keys[i]).join(map[keys[i]]);
      }
      if (out !== s) node.nodeValue = out;
    }
  }

  function applyIdentity() {
    var cap = CFG.token;
    var capShort = U.shortAddress(cap.captureAddress);

    var map = {};
    map[cap.captureSymbol] = SYM;
    map[cap.captureName] = NAME;
    map[capShort] = SHORT;
    retext(map);

    // ссылки «Search on X» и прочие, где стоит полный адрес
    var links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute('href') || '';
      if (href.indexOf(cap.captureAddress) < 0 && href.indexOf(cap.captureSymbol) < 0) continue;
      links[i].setAttribute('href', href
        .split(cap.captureAddress).join(ADDRESS)
        .split(cap.captureSymbol).join(SYM));
    }

    // копирование адреса кнопкой рядом с ним
    var copy = document.querySelector('button[aria-label="Copy address"]');
    if (copy) {
      copy.addEventListener('click', function () {
        try {
          if (navigator.clipboard) navigator.clipboard.writeText(ADDRESS);
        } catch (e) { /* file:// может не дать доступ */ }
      });
    }

    document.title = F.money(sim.mc) + ' MC | ' + SYM + ' | fomo';
  }

  /* ---------------------------------------------------- шапка со статистикой */

  var stats = {};
  (function collectStats() {
    var mcLabel = findByText('div.text-xs.text-text-secondary', 'Market cap');
    if (!mcLabel) return;
    var strip = mcLabel.parentElement.parentElement; // .flex.w-max...
    var tiles = strip.children;
    for (var i = 0; i < tiles.length; i++) {
      var tile = tiles[i];
      var label = txt(tile.firstElementChild);
      stats[label] = tile.lastElementChild;
    }
  })();

  /* ------------------------------------------------------------ блок «About» */

  var about = { windows: {}, flow: [] };
  (function collectAbout() {
    var labels = ['5M', '1H', '4H', '1D'];
    var btns = document.querySelectorAll('button.flex-1.flex.flex-col.items-center.rounded-md');
    for (var i = 0; i < btns.length; i++) {
      var l = txt(btns[i].firstElementChild);
      if (labels.indexOf(l) >= 0) about.windows[l] = { btn: btns[i], box: deltaBox(btns[i]) };
    }
    var buysSpan = null;
    var spans = document.querySelectorAll('span.text-text-secondary');
    for (var j = 0; j < spans.length; j++) {
      if (txt(spans[j]) === 'buys') { buysSpan = spans[j]; break; }
    }
    if (buysSpan) {
      // .flex.flex-col.gap-3.px-2 -> три группы: сделки / объём / уникальные
      var group = buysSpan.closest('div.flex.flex-col.gap-1');
      var wrap = group ? group.parentElement : null;
      if (wrap) {
        for (var k = 0; k < wrap.children.length; k++) {
          var g = wrap.children[k];
          var row = g.firstElementChild;
          var bar = g.lastElementChild;
          if (!row || !bar) continue;
          about.flow.push({
            left: row.children[0] ? row.children[0].firstElementChild : null,
            right: row.children[1] ? row.children[1].firstElementChild : null,
            bar: bar.firstElementChild
          });
        }
      }
    }
  })();

  /* --------------------------------------- блок предупреждений убираем целиком */

  function dropWarnings() {
    var spans = document.querySelectorAll('span.text-critical');
    for (var i = 0; i < spans.length; i++) {
      if (!/^Warning:/.test(txt(spans[i]))) continue;
      var box = spans[i].closest('div.rounded-xl.overflow-hidden');
      if (box) box.remove();
      break;
    }
  }

  var buyBtn = (function () {
    // на этот момент в вёрстке ещё стоит тикер из снимка
    var span = findByText('span.block.truncate', 'Buy ' + CFG.token.captureSymbol);
    return span ? { span: span, btn: span.parentElement } : null;
  })();

  var buyInput = document.querySelector('input[placeholder="0"]');
  var presetBtns = document.querySelectorAll('button.hover-scrim.h-8.rounded-lg');

  /** Торговля доступна сразу: предупреждений на странице больше нет. */
  function unlockTrading() {
    if (buyBtn) {
      var b = buyBtn.btn;
      b.disabled = false;
      b.classList.remove('cursor-not-allowed', 'bg-bg-secondary', 'text-text-secondary');
      b.classList.add('bg-green', 'text-bg-primary', 'cursor-pointer');
    }
    var sellBtn = document.querySelector('button.flex-1.p-2.rounded-lg[disabled]');
    if (sellBtn) sellBtn.disabled = false;
    for (var i = 0; i < presetBtns.length; i++) presetBtns[i].disabled = false;
  }

  /* ------------------------------------------------------- таблицы и вкладки */

  var holdersPane = (function () {
    var empty = findByText('div.text-center.min-h-15', 'No holders yet');
    if (!empty) return null;
    return { scroll: empty.parentElement, empty: empty, header: empty.previousElementSibling, rows: [] };
  })();

  var swapsPane = (function () {
    var empty = findByText('div.text-center.min-h-15', 'No activity yet');
    if (!empty) return null;
    return { scroll: empty.parentElement, empty: empty, header: empty.previousElementSibling, rows: [] };
  })();

  var tabs = (function () {
    var bar = null;
    var btns = document.querySelectorAll('button.capitalize.whitespace-nowrap');
    var found = {};
    for (var i = 0; i < btns.length; i++) {
      var t = txt(btns[i]);
      if (t === 'Holders' || t === 'Swaps' || t === 'Thesis') {
        found[t] = btns[i];
        bar = btns[i].closest('div.flex.gap-3.text-sm');
      }
    }
    return bar ? found : {};
  })();

  // Карточка со вкладками: [панель вкладок, блок Holders, блок Swaps].
  // Ищем именно внешние блоки — у внутренних совпадают классы, и closest()
  // цепляется не за тот уровень.
  var panes = (function () {
    if (!tabs.Holders || !holdersPane || !swapsPane) return null;
    var card = tabs.Holders.closest('div.flex.flex-col.rounded-lg');
    if (!card || card.children.length < 3) return null;
    var h = card.children[1], s = card.children[2];
    if (!h.contains(holdersPane.scroll) || !s.contains(swapsPane.scroll)) return null;
    return { Holders: h, Swaps: s };
  })();

  if (tabs.Holders && panes) {
    var setTab = function (name) {
      ['Holders', 'Swaps', 'Thesis'].forEach(function (n) {
        var b = tabs[n];
        if (!b) return;
        b.classList.toggle('text-text-primary', n === name);
        b.classList.toggle('text-text-tertiary', n !== name);
      });
      ['Holders', 'Swaps'].forEach(function (n) {
        var p = panes[n];
        if (!p) return;
        var on = n === name;
        p.classList.toggle('hidden', !on);
        p.classList.toggle('flex', on);
      });
    };
    ['Holders', 'Swaps'].forEach(function (n) {
      if (tabs[n]) tabs[n].addEventListener('click', function () { setTab(n); });
    });
    if (tabs.Thesis) tabs.Thesis.addEventListener('click', function () { setTab('Holders'); });
  }

  /* ------------------------------------------------------------ прочие узлы */

  var ageEl = document.querySelector('span.text-xs.text-text-secondary.shrink-0[title]');
  var createdEl = (function () {
    var l = findByText('span.text-xs.text-text-secondary.whitespace-nowrap.shrink-0', 'Created');
    if (!l) return null;
    var row = l.parentElement;
    return row.lastElementChild ? row.lastElementChild.firstElementChild : null;
  })();

  var sidebarRows = (function () {
    var host = document.querySelector('.legend-list-content-container');
    if (!host) return [];
    var links = host.querySelectorAll('a[href*="/tokens/"]');
    var out = [];
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var priceEl = a.querySelector('span.tabular-nums');
      var mcEl = a.querySelector('span.relative.inline-block > span:first-child');
      var box = deltaBox(a);
      if (!priceEl || !mcEl || !box) continue;
      var price = parseFloat(txt(priceEl).replace(/[$,]/g, '')) || 0.01;
      var pctNow = parseFloat(txt(box).replace(/[^\d.]/g, '')) || 5;
      var down = txt(box).indexOf('\u25BC') >= 0;
      var mcTxt = txt(mcEl);
      var mult = mcTxt.indexOf('B') >= 0 ? 1e9 : mcTxt.indexOf('M') >= 0 ? 1e6 : mcTxt.indexOf('K') >= 0 ? 1e3 : 1;
      var mc = (parseFloat(mcTxt.replace(/[^\d.]/g, '')) || 1) * mult;
      out.push({
        priceEl: priceEl, mcEl: mcEl, box: box,
        price: price, price0: price, mc: mc,
        pct: down ? -pctNow : pctNow, pct0: down ? -pctNow : pctNow
      });
    }
    return out;
  })();

  /* ------------------------------------------- криптовалюты: один источник
   * Курсы в футере и строки во вкладках Watchlist / Crypto берутся из одного
   * списка CFG.crypto: цена, суточное изменение, капитализация и объём.
   */

  var CRYPTO = (CFG.crypto || []).map(function (c) {
    return {
      id: c.id, symbol: c.symbol, img: c.img, href: c.href,
      price: c.price, price0: c.price,
      pct: c.change, pct0: c.change,
      mc: c.mc, mc0: c.mc,
      vol: c.vol
    };
  });

  /** Микродрожание вокруг настоящего курса — цифры живут, но не врут. */
  function jitterCrypto() {
    var j = CFG.cryptoJitter || 0;
    var lim = CFG.cryptoJitterMax || 0.002;
    for (var i = 0; i < CRYPTO.length; i++) {
      var c = CRYPTO[i];
      if (!c.price0) continue;
      c.price = U.clamp(c.price * (1 + U.gauss() * j), c.price0 * (1 - lim), c.price0 * (1 + lim));
      var k = c.price / c.price0;
      if (c.mc0) c.mc = c.mc0 * k;
      c.pct = c.pct0 + (k - 1) * 100;
    }
  }

  var footerRows = (function () {
    var links = document.querySelectorAll('footer a.flex.items-center.gap-1.shrink-0');
    var out = [];
    for (var i = 0; i < links.length && i < CRYPTO.length; i++) {
      var priceEl = links[i].querySelector('span.tabular-nums');
      var box = deltaBox(links[i]);
      if (!priceEl || !box) continue;
      out.push({ coin: CRYPTO[i], priceEl: priceEl, box: box });
    }
    return out;
  })();

  /**
   * Вкладки списка токенов в левой панели: Watchlist / Crypto / Trending / ...
   * Trending — список из снимка, Watchlist и Crypto — криптовалюты с живыми
   * курсами; строки клонируются из настоящей строки списка, поэтому вёрстка
   * и классы 1:1.
   */
  var cryptoPane = null;

  (function sidebarTabs() {
    var list = document.querySelector('.legend-list-content-container');
    if (!list) return;
    var scroll = list.parentElement;
    var row = null;
    var names = ['Watchlist', 'Crypto', 'Trending', 'Most held', 'Graduated', 'Bonding'];
    var btns = [];
    var all = document.querySelectorAll('button.inline-flex.h-6.items-center');
    for (var i = 0; i < all.length; i++) {
      if (names.indexOf(txt(all[i])) < 0) continue;
      btns.push(all[i]);
      row = all[i].parentElement;
    }
    if (!row || !btns.length) return;

    cryptoPane = (function build() {
      // прототип берём со строкой объёма, если такая в снимке есть
      var withVol = list.querySelector('a[href*="/tokens/"] div.w-26');
      var proto = withVol ? withVol.closest('a') : list.querySelector('a[href*="/tokens/"]');
      if (!proto || !CRYPTO.length) return null;

      var box = document.createElement('div');
      box.className = 'flex flex-col';
      box.style.display = 'none';
      var rows = [];

      for (var i = 0; i < CRYPTO.length; i++) {
        var c = CRYPTO[i];
        var a = proto.cloneNode(true);
        a.setAttribute('href', c.href);
        var img = a.querySelector('img');
        if (img) { img.setAttribute('src', c.img); img.setAttribute('alt', c.symbol); }
        var nameEl = a.querySelector('span.text-sm.leading-4.truncate');
        if (nameEl) nameEl.textContent = c.symbol;
        var volWrap = a.querySelector('div.w-26');
        rows.push({
          coin: c,
          priceEl: a.querySelector('span.tabular-nums'),
          mcEl: a.querySelector('span.relative.inline-block > span:first-child'),
          box: deltaBox(a),
          volEl: volWrap ? volWrap.lastElementChild : null
        });
        box.appendChild(a);
      }
      scroll.appendChild(box);
      return { box: box, rows: rows };
    })();

    function select(btn) {
      for (var j = 0; j < btns.length; j++) {
        var on = btns[j] === btn;
        btns[j].classList.toggle('bg-bg-tertiary-solid', on);
        btns[j].classList.toggle('text-text-secondary', !on);
      }
      var name = txt(btn);
      // Watchlist / Trending / Most held / ... — список токенов из снимка;
      // только вкладка Crypto показывает криптовалюты (BTC/ETH/SOL/HYPE).
      var crypto = cryptoPane && name === 'Crypto';
      // у контейнера списка display задан инлайном — классом его не спрятать
      list.style.display = crypto ? 'none' : 'block';
      if (cryptoPane) cryptoPane.box.style.display = crypto ? 'flex' : 'none';
      if (crypto) paintCrypto();
    }

    for (var k = 0; k < btns.length; k++) {
      (function (b) {
        b.addEventListener('click', function () { select(b); });
      })(btns[k]);
    }
  })();

  /* ------------------------------------------------------------- рендер строк */

  var ARROW_IN = '<svg aria-hidden="true" class="-scale-y-100 text-text-tertiary size-3 shrink-0" fill="none" viewBox="0 0 80 80" xmlns="http://www.w3.org/2000/svg"><path d="M40 6.66602C21.59 6.66602 6.66669 21.5893 6.66669 39.9993C6.66669 58.4093 21.59 73.3327 40 73.3327C58.41 73.3327 73.3334 58.4093 73.3334 39.9993C73.3334 21.5893 58.41 6.66602 40 6.66602ZM51.7668 38.4328C51.2801 38.9194 50.64 39.166 50 39.166C49.36 39.166 48.7199 38.9228 48.2333 38.4328L42.5 32.6995V53.3327C42.5 54.7127 41.38 55.8327 40 55.8327C38.62 55.8327 37.5 54.7127 37.5 53.3327V32.7028L31.7668 38.436C30.7901 39.4127 29.2067 39.4127 28.23 38.436C27.2533 37.4594 27.2533 35.8759 28.23 34.8993L38.23 24.8993C38.46 24.6693 38.7363 24.4864 39.043 24.3597C39.653 24.1064 40.343 24.1064 40.953 24.3597C41.2596 24.4864 41.5368 24.6693 41.7668 24.8993L51.7668 34.8993C52.7435 35.8759 52.7435 37.4561 51.7668 38.4328Z" fill="currentColor"></path></svg>';

  var CLOCK_ICON = '<svg class="w-3 h-3 text-text-tertiary" fill="none" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path clip-rule="evenodd" d="M8.00016 1.33334C4.31816 1.33334 1.3335 4.318 1.3335 8C1.3335 11.682 4.31816 14.6667 8.00016 14.6667C11.6822 14.6667 14.6668 11.682 14.6668 8C14.6668 4.318 11.6822 1.33334 8.00016 1.33334ZM10.3535 10.3534C10.2562 10.4507 10.1282 10.5 10.0002 10.5C9.87216 10.5 9.74414 10.4514 9.64681 10.3534L7.64681 8.35335C7.55281 8.25936 7.50016 8.132 7.50016 8V4.66667C7.50016 4.39067 7.72416 4.16667 8.00016 4.16667C8.27616 4.16667 8.50016 4.39067 8.50016 4.66667V7.79265L10.3535 9.646C10.5488 9.842 10.5488 10.158 10.3535 10.3534Z" fill="currentColor" fill-rule="evenodd"></path></svg>';

  var HEART_ICON = '<svg class="w-4 h-4 transition-colors text-text-tertiary" fill="none" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path d="M8 14.5002C7.92933 14.5002 7.85864 14.4855 7.79264 14.4555C7.57464 14.3562 2.444 11.9769 1.62134 7.73954C1.30334 6.1002 1.62267 4.50086 2.47534 3.46219C3.16534 2.62086 4.15731 2.17352 5.34465 2.16752C5.35065 2.16752 5.35665 2.16752 5.36198 2.16752C6.71665 2.16752 7.54268 2.93887 7.99935 3.59553C8.45802 2.9362 9.29064 2.16152 10.654 2.16752C11.842 2.17352 12.8346 2.62086 13.5253 3.46219C14.3766 4.50019 14.6953 6.09952 14.3766 7.74019C13.5553 11.9775 8.42397 14.3575 8.20597 14.4562C8.14131 14.4855 8.07067 14.5002 8 14.5002ZM5.36133 3.16686C5.35733 3.16686 5.35402 3.16686 5.35002 3.16686C4.45802 3.17086 3.75136 3.48352 3.2487 4.09619C2.5827 4.90752 2.34202 6.1982 2.60335 7.54886C3.24002 10.8315 7.062 12.9648 8 13.4435C8.938 12.9648 12.76 10.8315 13.396 7.54886C13.6587 6.19753 13.418 4.90685 12.7533 4.09619C12.2507 3.48419 11.544 3.17218 10.65 3.16752C10.646 3.16752 10.642 3.16752 10.6387 3.16752C9.05734 3.16752 8.49671 4.7522 8.47404 4.81953C8.40471 5.02153 8.21398 5.15885 8.00065 5.15885C7.99932 5.15885 7.99863 5.15885 7.99796 5.15885C7.78397 5.15818 7.59331 5.02152 7.52531 4.81819C7.50331 4.75152 6.94199 3.16686 5.36133 3.16686Z" fill="currentColor" stroke="currentColor" stroke-width="0.25"></path></svg>';

  var HOLDER_ROW_CLASS = 'group py-2 grid items-center gap-6 min-w-full grid-cols-[1fr_auto] @[400px]:grid-cols-[12rem_7.5rem_6.25rem_6.25rem_20rem] @[400px]:w-max @[900px]:grid-cols-[minmax(0,220px)_7.5rem_6.25rem_6.25rem_minmax(0,1fr)] @[900px]:w-auto hover:bg-bg-secondary';

  function holderRowHtml(h) {
    var t = h.trader;
    var col = F.colorFor(h.pnl);
    var sign = h.pnl >= 0 ? '+' : '-';
    var badge = t.badge
      ? '<img alt="Grand" class="shrink-0 object-cover rounded-sm" src="' + t.badge + '" style="width: 14px; height: 14px;" title="Grand">'
      : '';
    var thesis = t.thesis
      ? '<div class="text-sm text-text-primary leading-tight font-normal text-left line-clamp-2 wrap-break-word flex-1 min-w-0">' + t.thesis + '</div>'
      : '<div class="text-sm text-text-tertiary leading-tight font-normal text-left flex-1 min-w-0"></div>';

    return '' +
      '<div class="flex gap-3 items-center sticky @[900px]:static left-0 z-1 pl-3 pr-4 py-2 -my-2 border-r @[900px]:border-r-0 overflow-hidden transition-[border-color] bg-bg-primary group-hover:bg-bg-secondary border-transparent">' +
        '<div class="flex gap-3 items-center min-w-0">' +
          '<img class="rounded-full flex items-center justify-center object-cover shrink-0" src="' + t.avatar + '" style="width: 32px; height: 32px; min-width: 32px; min-height: 32px;">' +
          '<div class="flex flex-col gap-0.5 items-start min-w-0">' +
            '<div class="flex items-center gap-1 min-w-0 w-full">' +
              '<div class="flex min-w-0 max-w-full shrink items-center gap-0.5 text-left text-sm leading-5" translate="no"><div class="min-w-0 truncate">' + t.name + '</div></div>' +
              badge +
            '</div>' +
            '<div class="flex gap-1 items-center">' + CLOCK_ICON +
              '<div class="text-xs leading-4 text-left font-normal text-text-secondary">' + F.holdSpan(h.holdMs) + ' avg. hold</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="flex-col items-start gap-0.5 hidden @[400px]:flex tabular-nums min-w-0" translate="no">' +
        '<div class="flex items-center gap-1 text-sm leading-5">' + ARROW_IN + F.moneyFull(h.value) + '</div>' +
        '<div class="text-xs leading-4 font-normal text-text-secondary truncate max-w-full">' + F.tokens(h.tokens) + ' ' + SYM + '</div>' +
      '</div>' +
      '<div class="flex-col justify-center gap-0.5 items-start hidden @[400px]:flex tabular-nums">' +
        '<div class="flex gap-0.5 items-center tabular-nums" style="line-height: 20px;" translate="no">' +
          '<div style="font-size: 14px; font-weight: 500; color: ' + col + ';">' + sign + '</div>' +
          '<div style="font-size: 14px; font-weight: 500; color: ' + col + ';">' + F.moneyFull(Math.abs(h.pnl)).replace('-', '') + '</div>' +
        '</div>' +
        '<div class="flex gap-0.75 items-center tabular-nums" style="line-height: 16px;" translate="no">' +
          '<div style="color: ' + col + '; font-weight: 400; font-size: 6px;">' + (h.pnl >= 0 ? '\u25B2' : '\u25BC') + '</div>' +
          '<div style="font-size: 12px; font-weight: 400; color: rgb(152, 153, 163);">' + F.pct(h.pnlPct) + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="flex-col items-start gap-0.5 hidden @[400px]:flex" translate="no">' +
        '<div class="text-sm leading-5">' + F.money(h.avgMc) + ' <span class="text-text-secondary">MC</span></div>' +
        '<div class="text-xs leading-4 font-normal text-text-secondary"><span class="tabular-nums" translate="no">' + F.priceHtml(h.avgPrice) + '</span></div>' +
      '</div>' +
      '<div class="items-center hidden @[400px]:flex pr-3 gap-4">' +
        '<div class="flex items-center cursor-pointer flex-col shrink-0 min-w-8" role="button" tabindex="0">' +
          '<div class="p-2 -m-2 active:scale-75 transition-transform duration-150">' + HEART_ICON + '</div>' +
          '<span class="text-xs min-w-2 transition-all text-text-tertiary">' + (t.thesis ? t.likes : '') + '</span>' +
        '</div>' + thesis +
      '</div>';
  }

  var SWAP_ROW_CLASS = 'group w-full grid gap-3 px-3 py-2 text-left border-b border-bg-tertiary/30 hover:bg-bg-secondary @[500px]:grid-cols-[1fr_4rem_7rem_6rem_2rem] @[500px]:items-center';

  function swapRowHtml(tr) {
    var isBuy = tr.side === 'buy';
    var col = isBuy ? F.GREEN : F.RED;
    return '' +
      '<div class="flex items-center gap-2 min-w-0">' +
        '<img class="rounded-full object-cover shrink-0" src="' + tr.trader.avatar + '" style="width: 24px; height: 24px;">' +
        '<div class="text-sm truncate" translate="no">' + tr.trader.name + '</div>' +
      '</div>' +
      '<div class="text-sm font-medium" style="color: ' + col + ';">' + (isBuy ? 'Buy' : 'Sell') + '</div>' +
      '<div class="flex flex-col tabular-nums" translate="no">' +
        '<span class="text-sm leading-5">' + F.moneyFull(tr.usd) + '</span>' +
        '<span class="text-xs leading-4 text-text-secondary">' + F.tokens(tr.tokens) + ' ' + SYM + '</span>' +
      '</div>' +
      '<div class="text-sm tabular-nums" translate="no">' + F.money(tr.mc) + '</div>' +
      '<div class="text-right text-xs text-text-tertiary tabular-nums" translate="no">' + F.ago(sim.simNow - tr.t) + '</div>';
  }

  /* ------------------------------------------------------------ обновление UI */

  function setDelta(box, v, size) {
    if (box) box.innerHTML = F.deltaHtml(v, size);
  }

  function paintHeader(feed) {
    if (stats['Market cap']) stats['Market cap'].textContent = F.money(feed.mc);
    if (stats['Price']) {
      var span = stats['Price'].querySelector('span.tabular-nums') || stats['Price'];
      span.innerHTML = F.priceHtml(feed.price);
    }
    setDelta(deltaBox(stats['24H change']), feed.change['1D'], 'md');
    if (stats['24H Vol.']) stats['24H Vol.'].textContent = F.money(feed.volume24h);
    if (stats['Liquidity']) stats['Liquidity'].textContent = F.money(feed.liquidity);
    if (stats['Holders']) stats['Holders'].textContent = F.count(feed.holders);
    if (stats['Top 10 holding']) stats['Top 10 holding'].textContent = feed.top10.toFixed(2) + '%';

    document.title = F.money(feed.mc) + ' MC | ' + SYM + ' | fomo';

    if (ageEl) {
      ageEl.textContent = F.age(feed.ageMs);
      ageEl.setAttribute('title', F.stamp(sim.createdAt()));
    }
    if (createdEl) createdEl.textContent = F.ageLong(feed.ageMs);
  }

  var WINDOW_MINUTES = { '5M': 5, '1H': 60, '4H': 240, '1D': 1440 };
  var activeWindow = '1H';

  // кнопки 5M / 1H / 4H / 1D переключают окно, по которому считается поток сделок
  Object.keys(about.windows).forEach(function (k) {
    about.windows[k].btn.addEventListener('click', function () {
      activeWindow = k;
      Object.keys(about.windows).forEach(function (n) {
        var b = about.windows[n].btn;
        var on = n === k;
        b.classList.toggle('bg-bg-tertiary', on);
        b.classList.toggle('border-transparent', on);
        b.classList.toggle('border-bg-tertiary-solid', !on);
        b.classList.toggle('hover:bg-bg-tertiary', !on);
      });
      paintAbout(sim.dexFeed());
    });
  });

  function paintAbout(feed) {
    Object.keys(about.windows).forEach(function (k) {
      setDelta(about.windows[k].box, feed.change[k], 'sm');
    });

    var flow = feed.flow[activeWindow] || feed.flow['1H'];
    var rows = about.flow;
    var data = [
      { l: flow.buys, r: flow.sells, fmt: F.count },
      { l: flow.buyVol, r: flow.sellVol, fmt: F.money },
      { l: flow.buyers, r: flow.sellers, fmt: F.count }
    ];
    for (var i = 0; i < rows.length && i < data.length; i++) {
      var d = data[i];
      if (rows[i].left) rows[i].left.textContent = d.fmt(d.l);
      if (rows[i].right) rows[i].right.textContent = d.fmt(d.r);
      if (rows[i].bar) {
        var total = d.l + d.r;
        var w = total > 0 ? (d.l / total) * 100 : 50;
        rows[i].bar.style.width = w.toFixed(1) + '%';
      }
    }
  }

  /* --- таблицы --- */

  function paintHolders(feed) {
    if (!holdersPane) return;

    var list = sim.topHolders(CFG.trades.holdersLimit);
    if (!list.length) return;
    if (holdersPane.empty && holdersPane.empty.parentElement) holdersPane.empty.remove();

    var pane = holdersPane;
    while (pane.rows.length > list.length) pane.rows.pop().remove();
    for (var i = 0; i < list.length; i++) {
      var row = pane.rows[i];
      if (!row) {
        row = document.createElement('button');
        row.className = HOLDER_ROW_CLASS;
        pane.scroll.appendChild(row);
        pane.rows.push(row);
      }
      row.innerHTML = holderRowHtml(list[i]);
    }

    if (tabs.Holders) tabs.Holders.textContent = 'Holders (' + F.count(feed.holders) + ')';
    if (tabs.Thesis) {
      var withThesis = 0;
      for (var j = 0; j < sim.traders.length; j++) {
        if (sim.traders[j].thesis && sim.traders[j].tokens > 0) withThesis++;
      }
      tabs.Thesis.textContent = withThesis > 0 ? 'Thesis (' + F.commas(withThesis) + ')' : 'Thesis';
    }
  }

  function paintSwaps() {
    if (!swapsPane) return;
    var list = sim.trades;
    if (!list.length) return;
    if (swapsPane.empty && swapsPane.empty.parentElement) swapsPane.empty.remove();

    var pane = swapsPane;
    while (pane.rows.length > list.length) pane.rows.pop().remove();
    for (var i = 0; i < list.length; i++) {
      var row = pane.rows[i];
      if (!row) {
        row = document.createElement('button');
        row.className = SWAP_ROW_CLASS;
        pane.scroll.appendChild(row);
        pane.rows.push(row);
      }
      row.innerHTML = swapRowHtml(list[i]);
      if (list[i].fresh) {
        list[i].fresh = false;
        row.style.transition = 'none';
        row.style.background = list[i].side === 'buy'
          ? 'rgba(33, 201, 94, 0.16)' : 'rgba(255, 98, 46, 0.16)';
        (function (r) {
          setTimeout(function () {
            r.style.transition = 'background 600ms ease-out';
            r.style.background = 'transparent';
          }, 30);
        })(row);
      }
    }
  }

  /* --- боковая панель и футер --- */

  // Проценты считаем от исходного значения, а не накапливаем по тикам —
  // иначе за пару минут они уезжают в десятки тысяч процентов.
  function paintSidebar() {
    for (var i = 0; i < sidebarRows.length; i++) {
      var r = sidebarRows[i];
      if (r.mc0 == null) r.mc0 = r.mc;
      var drift = U.gauss() * 0.0035;
      r.price = U.clamp(r.price * (1 + drift), r.price0 * 0.8, r.price0 * 1.25);
      r.mc = r.mc0 * (r.price / r.price0);
      r.priceEl.innerHTML = F.priceHtml(r.price);
      r.mcEl.textContent = F.money(r.mc);
      setDelta(r.box, r.pct0 + (r.price / r.price0 - 1) * 100, 'sm');
    }
  }

  /** Курсы в футере — значения из API как есть. */
  function paintFooter() {
    for (var i = 0; i < footerRows.length; i++) {
      var r = footerRows[i];
      r.priceEl.textContent = '$' + F.commas(r.coin.price.toFixed(2));
      setDelta(r.box, r.coin.pct, 'sm');
    }
  }

  /** Строки криптовалют во вкладках Watchlist / Crypto. */
  function paintCrypto() {
    if (!cryptoPane || cryptoPane.box.style.display === 'none') return;
    for (var i = 0; i < cryptoPane.rows.length; i++) {
      var r = cryptoPane.rows[i];
      if (r.priceEl) r.priceEl.innerHTML = F.priceHtml(r.coin.price);
      if (r.mcEl) r.mcEl.textContent = F.money(r.coin.mc);
      if (r.box) setDelta(r.box, r.coin.pct, 'sm');
      if (r.volEl) r.volEl.textContent = F.money(r.coin.vol);
    }
  }

  /**
   * Реальные текущие данные BTC / ETH / SOL / HYPE: цена, суточное изменение,
   * капитализация и объём. Обычный GET к публичному API, ничего не отправляем;
   * повторяется каждые CFG.cryptoRefreshMs, чтобы цифры оставались текущими.
   * Не получилось (нет сети, блокировка) — остаются значения из CFG.crypto.
   */
  function loadLivePrices() {
    if (!CFG.cryptoLive || !window.fetch || !CRYPTO.length) return;
    try {
      window.fetch(CFG.cryptoUrl, { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return;
          for (var i = 0; i < CRYPTO.length; i++) {
            var c = CRYPTO[i];
            var q = data[c.id];
            if (!q || !isFinite(q.usd)) continue;
            c.price = c.price0 = q.usd;
            if (isFinite(q.usd_24h_change)) c.pct = c.pct0 = q.usd_24h_change;
            if (isFinite(q.usd_market_cap)) c.mc = c.mc0 = q.usd_market_cap;
            if (isFinite(q.usd_24h_vol)) c.vol = q.usd_24h_vol;
          }
          paintFooter();
          paintCrypto();
        })
        .catch(function () { /* оставляем значения из конфига */ });
    } catch (e) { /* тоже не страшно */ }
  }

  /* ------------------------------------- разделитель высоты области графика */

  (function () {
    var handle = document.querySelector('div.cursor-ns-resize');
    if (!handle) return;
    var target = handle.previousElementSibling;
    if (!target) return;
    var startY = 0, startH = 0, dragging = false, manual = false;

    /**
     * Область графика растягивается на всю высоту окна: таблица Holders
     * уезжает под сгиб, страница выглядит как на скриншоте с $3.1M,
     * а не как вариант с таблицей сразу под графиком.
     */
    function fit() {
      if (!CFG.chart.fillViewport || manual) return;
      var top = target.getBoundingClientRect().top;
      var footer = document.querySelector('footer');
      var footerH = footer ? footer.getBoundingClientRect().height : 26;
      var handleH = handle.getBoundingClientRect().height || 10;
      var h = window.innerHeight - top - footerH - handleH - 2;
      if (h < 320) h = 320;
      if (Math.abs(parseFloat(target.style.height) - h) < 1) return;
      target.style.height = Math.round(h) + 'px';
      window.dispatchEvent(new Event('resize'));
    }

    fit();
    window.addEventListener('resize', fit);
    // фреймы и шрифты доезжают позже — пересчитываем ещё раз
    setTimeout(fit, 300);

    handle.addEventListener('mousedown', function (e) {
      manual = true;
      dragging = true;
      startY = e.clientY;
      startH = target.getBoundingClientRect().height;
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      target.style.height = Math.max(300, Math.min(1400, startH + (e.clientY - startY))) + 'px';
    });
    window.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      document.body.style.userSelect = '';
      window.dispatchEvent(new Event('resize'));
    });
  })();

  /* ------------------------------------------------------- график в iframe */

  var chartFrame = document.querySelector('iframe[id^="tradingview_"]');
  var chartReady = false;

  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'fomo:chart-ready') {
      chartReady = true;
      pushChart();
    }
  });

  function pushChart() {
    if (!chartReady || !chartFrame || !chartFrame.contentWindow) return;
    try {
      chartFrame.contentWindow.postMessage(sim.chartSnapshot(), '*');
    } catch (err) { /* игнорируем */ }
  }

  /* ---------------------------------------------- кнопка Buy (необязательно) */

  if (presetBtns.length && buyInput) {
    for (var pi = 0; pi < presetBtns.length; pi++) {
      (function (b) {
        b.addEventListener('click', function () {
          buyInput.value = txt(b).replace('$', '');
          buyInput.dispatchEvent(new Event('input'));
        });
      })(presetBtns[pi]);
    }
  }

  if (buyBtn) {
    buyBtn.btn.addEventListener('click', function () {
      var usd = parseFloat((buyInput && buyInput.value) || '0');
      if (!usd || usd <= 0) return;
      var price = sim.price();
      var me = sim.tradersByName['you'];
      if (!me) {
        me = {
          id: -1, name: 'you', avatar: 'avatars/a00.jpg', badge: null, thesis: null,
          likes: 0, tokens: 0, costUsd: 0, firstSeen: sim.simNow, lastSeen: sim.simNow
        };
        sim.tradersByName['you'] = me;
        sim.traders.push(me);
      }
      me.tokens += usd / price;
      me.costUsd += usd;
      me.lastSeen = sim.simNow;
      sim.trades.unshift({
        t: sim.simNow, side: 'buy', usd: usd, tokens: usd / price,
        mc: sim.mc, trader: me, fresh: true
      });
      paintSwaps();
      paintHolders(sim.dexFeed());
    });
  }

  /* ------------------------------------------------------------------- цикл
   * Цикл на setInterval, а не на requestAnimationFrame: rAF замирает, когда
   * браузер не рисует кадры (вкладка в фоне, окно свёрнуто), и тогда цифры
   * зависают. Таймер продолжает идти, шаг модели считается по реальному dt,
   * поэтому время на графике не отстаёт.
   */

  var lastFrame = Date.now();
  var lastFeedAt = 0;

  function loop() {
    var now = Date.now();
    var dt = Math.min(5000, Math.max(0, now - lastFrame));
    lastFrame = now;

    sim.step(dt);

    // Цифры страницы и график обновляются одним пакетом ровно раз в CFG.uiMs,
    // поэтому шапка, блок About и последняя свеча всегда показывают одно и то же.
    var feed = sim.dexFeed(Date.now());
    if (feed.at !== lastFeedAt) {
      lastFeedAt = feed.at;
      pushChart();
      paintHeader(feed);
      paintAbout(feed);
      paintSwaps();
      paintHolders(feed);
      paintSidebar();
      jitterCrypto();
      paintFooter();
      paintCrypto();
    }
  }

  /* ------------------------------------------------------------------- старт */

  applyIdentity();
  renderNumberFlows();
  dropWarnings();
  unlockTrading();
  paintFooter();
  loadLivePrices();
  if (CFG.cryptoRefreshMs > 0) setInterval(loadLivePrices, CFG.cryptoRefreshMs);

  var first = sim.dexFeed(Date.now());
  lastFeedAt = first.at;
  paintHeader(first);
  paintAbout(first);
  paintSwaps();
  paintHolders(first);

  setInterval(loop, CFG.tickMs);
  // вкладка вернулась из фона — досчитываем и сразу перерисовываем
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) loop();
  });

  // для отладки из консоли
  window.fomoSim = sim;
})();
