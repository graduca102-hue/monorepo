/*
 * fomo-core.js — модель фейкового пампа.
 * Не трогает DOM: только считает цену, свечи, сделки и производные метрики.
 * Используется основной страницей (fomo-pump.js), состояние передаётся
 * в iframe графика через postMessage.
 *
 * Время идёт в реальном масштабе (CFG.speed = 1), свеча = CFG.candleSeconds.
 * Вся волна пампа лежит в истории: на момент открытия страницы токен уже
 * стоит CFG.pump.mcNow, дальше модель продолжает жить.
 */
(function (global) {
  'use strict';

  var CFG = global.FOMO_CFG;

  /* ------------------------------------------------------------------ utils */

  function rnd(a, b) { return a + Math.random() * (b - a); }
  function rndInt(a, b) { return Math.floor(rnd(a, b + 1)); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  /** число или диапазон [min, max] */
  function value(v) { return Array.isArray(v) ? rnd(v[0], v[1]) : v; }

  // нормальное распределение (Box–Muller)
  function gauss() {
    var u = 1 - Math.random(), v = Math.random();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  // base58 без 0, O, I, l — как в адресах Solana
  var B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

  /** Случайный Solana-подобный адрес (44 символа base58). */
  function randomAddress(len) {
    var n = len || 44, s = '';
    var buf = null;
    if (global.crypto && global.crypto.getRandomValues) {
      buf = new Uint8Array(n);
      global.crypto.getRandomValues(buf);
    }
    for (var i = 0; i < n; i++) {
      var r = buf ? buf[i] : Math.floor(Math.random() * 256);
      s += B58[r % B58.length];
    }
    return s;
  }

  /** 6vRogh...Q6BJbu */
  function shortAddress(addr, head, tail) {
    head = head || 6; tail = tail || 6;
    if (!addr || addr.length <= head + tail) return addr || '';
    return addr.slice(0, head) + '...' + addr.slice(-tail);
  }

  /* --------------------------------------------------------------- трейдеры */

  var HANDLES = [
    'grindset', 'solkid', 'liquidog', 'nocoiner', 'pumpwizard', 'exitliq',
    'bagholder', 'rugsurvivor', 'mevjanitor', 'chartlord', 'jeetproof',
    'sniper0x', 'wenlambo', 'candlemaker', 'topblaster', 'lowfloat',
    'apeharder', 'diamondgrip', 'freshwallet', 'insentos', 'frogmanhaha',
    'copytrader', 'nukeitall', 'greenwick', 'sizeup', 'anonfren',
    'lastbuyer', 'orderflow', 'devwallet', 'whalewatch', 'cryptobread',
    'moonmath', 'thesisguy', 'flipzone', 'earlygang', 'noexit',
    'gigachart', 'redcandle', 'liqhunter', 'fomoking'
  ];

  var AVATARS = (function () {
    var a = [];
    for (var i = 0; i < 40; i++) a.push('avatars/a' + (i < 10 ? '0' + i : i) + '.jpg');
    return a;
  })();

  var BADGES = ['avatars/badge-grand.jpg', 'avatars/badge-2.jpg', 'avatars/badge-3.jpg'];

  var THESES = [
    'A beautiful chart is being drawn by the market participants',
    'first mover on this one, chart speaks for itself',
    'liquidity just got added, this is only the beginning',
    'volume profile is insane for a token this fresh',
    'holders going vertical, top10 melting fast',
    'this is the cleanest breakout of the day',
    'adding on every dip until it stops working',
    'supply is locked, float is tiny, do the math',
    'looks like we\'ll see 50m sooner than previously anticipated',
    'devs are quiet, chart is loud'
  ];

  function makeTrader(i) {
    var h = HANDLES[i % HANDLES.length];
    if (i >= HANDLES.length) h = h + rndInt(2, 99);
    return {
      id: i,
      name: h,
      avatar: AVATARS[i % AVATARS.length],
      badge: Math.random() < 0.28 ? pick(BADGES) : null,
      thesis: Math.random() < 0.35 ? pick(THESES) : null,
      likes: rndInt(3, 240),
      // накопленная позиция
      tokens: 0,
      costUsd: 0,
      firstSeen: 0,
      lastSeen: 0
    };
  }

  /* ------------------------------------------------------------------ модель */

  function Sim() {
    var t = this;
    t.cfg = CFG;
    t.supply = CFG.token.supply;
    t.candleMs = CFG.candleSeconds * 1000;

    t.startWall = Date.now();
    // момент открытия страницы, выровненный по сетке таймфрейма
    t.openedAt = Math.floor(t.startWall / t.candleMs) * t.candleMs;
    t.rampMs = CFG.pump.rampSeconds * 1000;
    // волна закончилась не «сейчас», а раньше: справа остаётся болтанка на верхах
    t.rampEnd = t.openedAt - CFG.pump.rampEndsAgoSeconds * 1000;
    t.pumpT0 = t.rampEnd - t.rampMs;
    t.mcNow = value(CFG.pump.mcNow);

    t.simNow = t.openedAt;
    t.noise = 0;
    t.pullback = null;

    t.candles = [];               // {t, o, h, l, c, v, nb, ns, vb, vs}
    t.trades = [];                // лента свапов (свежие в начале)
    t.traders = [];
    t.tradersByName = {};
    t.holderCount = 0;
    t.volume24h = 0;
    t.tradeCarry = 0;

    t.address = CFG.token.address || randomAddress();
    t.shortAddress = shortAddress(t.address);

    t.mc = CFG.pump.mcStart;
    t.buildHistory();
    t.simNow = t.startWall;
    t.mcOpen24h = t.candles.length ? t.candles[0].o : CFG.pump.mcStart;
    t.holders();

    /* Точки отсчёта для окон 5M / 1H / 4H / 1D.
     * У dexscreener у каждого окна своя опорная цена, поэтому четыре кнопки
     * в блоке About показывают разные числа. История графика — 96 секунд,
     * так что для 1H / 4H / 1D берём фиксированные опорные капитализации,
     * а 5M считаем по настоящим свечам. */
    var m = CFG.metrics;
    t.windowRefMc = {};
    Object.keys(m.windowRef || {}).forEach(function (k) {
      t.windowRefMc[k] = t.mcNow * value(m.windowRef[k]);
    });
    t.flowFactor = {};
    Object.keys(m.flowFactor || {}).forEach(function (k) {
      t.flowFactor[k] = value(m.flowFactor[k]);
    });
    t.window5mSec = m.window5mSeconds != null
      ? m.window5mSeconds
      : CFG.pump.rampEndsAgoSeconds + 8;
  }

  /**
   * Опорная капитализация в момент времени ms.
   * До pumpT0 — «мёртвый» токен, затем волна в лог-пространстве до mcNow,
   * после rampEnd — медленный дрейф вверх.
   */
  Sim.prototype.baseMc = function (ms) {
    var p = CFG.pump;
    if (ms <= this.pumpT0) return p.mcStart;
    if (ms <= this.rampEnd) {
      var L0 = Math.log(p.mcStart), L1 = Math.log(this.mcNow);
      var u = (ms - this.pumpT0) / this.rampMs;
      return Math.exp(L0 + (L1 - L0) * Math.pow(u, p.curve));
    }
    var hours = (ms - this.rampEnd) / 3600000;
    return this.mcNow * Math.pow(1 + p.driftPerHour, hours);
  };

  /**
   * Параметры болтанки зависят от фазы: во время волны цена идёт ровно,
   * после неё — «пила» с большими свечами и частыми откатами.
   */
  Sim.prototype.regime = function () {
    var p = CFG.pump;
    if (this.simNow <= this.rampEnd) {
      return {
        noise: p.noise,
        theta: p.noiseTheta,
        chance: p.pullbackChancePerSec,
        depth: p.pullbackDepth
      };
    }
    return {
      noise: p.noiseTop != null ? p.noiseTop : p.noise,
      theta: p.noiseThetaTop != null ? p.noiseThetaTop : p.noiseTheta,
      chance: p.pullbackChancePerSecTop != null ? p.pullbackChancePerSecTop : p.pullbackChancePerSec,
      depth: p.pullbackDepthTop || p.pullbackDepth
    };
  };

  /** Один шаг цены: откаты + OU-шум вокруг опорной кривой. */
  Sim.prototype.advanceModel = function (dtMs) {
    var p = CFG.pump;
    var r = this.regime();
    var dtSec = Math.max(dtMs, 0) / 1000;

    if (this.pullback) {
      this.pullback.left -= dtSec;
      if (this.pullback.left <= 0) this.pullback = null;
    } else if (Math.random() < r.chance * dtSec) {
      var secs = rnd(p.pullbackSeconds[0], p.pullbackSeconds[1]);
      this.pullback = {
        depth: rnd(r.depth[0], r.depth[1]),
        left: secs,
        total: secs
      };
    }

    // Ornstein–Uhlenbeck: возврат к нулю
    this.noise += (-r.theta * this.noise) * dtSec +
      r.noise * 0.9 * gauss() * Math.sqrt(Math.max(dtSec, 1e-6));
    this.noise = clamp(this.noise, -r.noise * 3, r.noise * 3);

    var dipMul = 1;
    if (this.pullback) {
      var phase = clamp(1 - this.pullback.left / this.pullback.total, 0, 1);
      dipMul = 1 - this.pullback.depth * Math.sin(Math.PI * phase);
    }

    this.mc = Math.max(1, this.baseMc(this.simNow) * (1 + this.noise) * dipMul);
  };

  Sim.prototype.blankCandle = function (t, price) {
    // nb/ns — покупки/продажи, vb/vs — их объёмы, v — общий объём,
    // tr/br/sr — трейдеры/покупатели/продавцы (счётчики блока About)
    return {
      t: t, o: price, h: price, l: price, c: price,
      v: 0, nb: 0, ns: 0, vb: 0, vs: 0, tr: 0, br: 0, sr: 0
    };
  };

  Sim.prototype.currentCandle = function () {
    return this.candles[this.candles.length - 1];
  };

  /** Дописывает текущую свечу текущей ценой. */
  Sim.prototype.touchCandle = function () {
    var c = this.currentCandle();
    if (!c) return;
    c.c = this.mc;
    if (this.mc > c.h) c.h = this.mc;
    if (this.mc < c.l) c.l = this.mc;
  };

  /** Открывает новые свечи, если время ушло вперёд. */
  Sim.prototype.rollCandles = function () {
    var cur = this.currentCandle();
    var bucket = Math.floor(this.simNow / this.candleMs) * this.candleMs;
    while (cur && bucket > cur.t) {
      cur = this.blankCandle(cur.t + this.candleMs, cur.c);
      this.candles.push(cur);
      if (this.candles.length > 2000) this.candles.shift();
    }
    this.touchCandle();
  };

  /** История: вся волна пампа и болтанка после неё, вместе со сделками. */
  Sim.prototype.buildHistory = function () {
    var n = CFG.history.bars;
    var sub = Math.max(1, CFG.history.subSteps || 1);
    var first = this.openedAt - (n - 1) * this.candleMs;

    this.simNow = first;
    this.mc = this.baseMc(first);

    for (var i = 0; i < n; i++) {
      var t = first + i * this.candleMs;
      this.candles.push(this.blankCandle(t, this.mc));
      for (var s = 1; s <= sub; s++) {
        this.simNow = t + (this.candleMs * s) / sub;
        this.advanceModel(this.candleMs / sub);
        this.touchCandle();
      }
      this.simNow = t + this.candleMs - 1;
      this.accumulateStats(this.candleMs / 60000);
      this.spawnTrades(this.candleMs / 60000, false);
    }
  };

  /** Основной шаг модели. dtReal — мс реального времени. */
  Sim.prototype.step = function (dtReal) {
    var dtSim = dtReal * CFG.speed;
    this.simNow += dtSim;
    this.advanceModel(dtSim);
    this.rollCandles();
    this.accumulateStats(dtSim / 60000);
    this.spawnTrades(dtSim / 60000, true);
  };

  /* ------------------------------------ статистика сделок (формулы dexscreener)
   * Порт buildTickStats из проекта dexscreener (js/overlay-live-chart.js).
   * За один «тик» набирается пачка сделок; доля покупок привязана к цвету
   * свечи, объём покупок ~половина, трейдеры — доля от числа сделок.
   */

  Sim.prototype.dexTickStats = function (mc, greenCandle) {
    var d = CFG.dex;
    var txns = rndInt(d.txnsPerTick[0], d.txnsPerTick[1]);
    var buyShare = clamp(
      rnd(d.buyShareBase[0], d.buyShareBase[1]) +
        (greenCandle ? d.buyShareDirBias : -d.buyShareDirBias) + d.buyShareExtraBias,
      d.buyShareClamp[0], d.buyShareClamp[1]);
    var buys = clamp(Math.round(txns * buyShare), 1, txns - 1);
    var sells = txns - buys;

    var avgFrac = Math.exp(rnd(Math.log(d.volumePerTxnFrac[0]), Math.log(d.volumePerTxnFrac[1])));
    var volume = txns * mc * avgFrac;
    var buyVolume = volume * clamp(buys / txns + rnd(-d.buyVolJitter, d.buyVolJitter),
      d.buyVolClamp[0], d.buyVolClamp[1]);

    var traders = Math.round(txns * rnd(d.tradersFrac[0], d.tradersFrac[1]));
    var buyerShare = clamp(buys / txns + rnd(-d.buyerJitter, d.buyerJitter),
      d.buyerClamp[0], d.buyerClamp[1]);
    var buyers = clamp(Math.round(traders * buyerShare), 0, traders);

    return {
      txns: txns, buys: buys, sells: sells,
      volume: volume, buyVolume: buyVolume, sellVolume: volume - buyVolume,
      traders: traders, buyers: buyers, sellers: traders - buyers
    };
  };

  /** Накопление dexscreener-статистики в текущую свечу за интервал dtMin. */
  Sim.prototype.accumulateStats = function (dtMin) {
    var d = CFG.dex;
    var prog = this.progress();
    var perCandle = d.ticksPerCandleStart +
      (d.ticksPerCandlePeak - d.ticksPerCandleStart) * Math.pow(prog, 1.4);
    // сколько тиков приходится на dtMin (одна свеча = candleMs)
    var ticks = perCandle * (dtMin * 60000) / this.candleMs;
    this.statCarry = (this.statCarry || 0) + ticks;
    var n = Math.floor(this.statCarry);
    this.statCarry -= n;
    if (n > 120) n = 120;
    var cc = this.currentCandle();
    if (!cc || n <= 0) return;

    var green = cc.c >= cc.o;
    for (var i = 0; i < n; i++) {
      var s = this.dexTickStats(this.mc, green);
      cc.nb += s.buys; cc.ns += s.sells;
      cc.vb += s.buyVolume; cc.vs += s.sellVolume; cc.v += s.volume;
      cc.tr += s.traders; cc.br += s.buyers; cc.sr += s.sellers;
    }
  };

  /** 0..1 — насколько прошла волна пампа (после открытия страницы всегда 1). */
  Sim.prototype.progress = function () {
    return clamp((this.simNow - this.pumpT0) / this.rampMs, 0, 1);
  };

  Sim.prototype.getTrader = function () {
    var wantNew = this.traders.length < 8 ||
      Math.random() < clamp(0.55 - this.progress() * 0.25, 0.2, 0.6);
    if (wantNew) {
      var t = makeTrader(this.traders.length);
      t.firstSeen = this.simNow;
      this.traders.push(t);
      return t;
    }
    return pick(this.traders);
  };

  Sim.prototype.spawnTrades = function (dtMin, fresh) {
    var tr = CFG.trades;
    var prog = this.progress();
    var rate = tr.ratePerMinStart + (tr.ratePerMinPeak - tr.ratePerMinStart) * Math.pow(prog, 1.6);

    this.tradeCarry += rate * dtMin;
    var n = Math.floor(this.tradeCarry);
    this.tradeCarry -= n;
    if (n > 40) n = 40;

    var buyP = this.pullback ? tr.buyRatioDip : tr.buyRatio;

    for (var i = 0; i < n; i++) {
      var side = Math.random() < buyP ? 'buy' : 'sell';
      var frac = Math.exp(rnd(Math.log(tr.sizeFracMin), Math.log(tr.sizeFracMax)));
      var usd = Math.max(1.2, this.mc * frac);
      var price = this.mc / this.supply;
      var tokens = usd / price;

      var trader = this.getTrader();
      trader.lastSeen = this.simNow;
      if (side === 'buy') {
        trader.tokens += tokens;
        trader.costUsd += usd;
      } else {
        var sold = Math.min(trader.tokens, tokens);
        var ratio = trader.tokens > 0 ? sold / trader.tokens : 0;
        trader.costUsd -= trader.costUsd * ratio;
        trader.tokens -= sold;
      }

      // лента Swaps и позиции холдеров; агрегаты (nb/ns/vb/vs/v) считает
      // accumulateStats по формулам dexscreener, поэтому здесь их не трогаем
      this.trades.unshift({
        t: this.simNow - rnd(0, Math.min(400, this.candleMs)),
        side: side,
        usd: usd,
        tokens: tokens,
        mc: this.mc,
        trader: trader,
        fresh: !!fresh
      });
      if (this.trades.length > tr.feedLimit) this.trades.pop();
    }
  };

  /* ------------------------------------------------------- производные числа */

  Sim.prototype.price = function () { return this.mc / this.supply; };

  Sim.prototype.liquidity = function () {
    return this.mc * CFG.metrics.liquidityFrac;
  };

  /**
   * Число холдеров: считается от капитализации, плюс медленный набор со
   * временем. Никогда не уменьшается — как на реальной странице.
   */
  Sim.prototype.holders = function () {
    var m = CFG.metrics;
    var minutes = Math.max(0, (this.simNow - this.openedAt) / 60000);
    var base = m.holdersMul * Math.pow(this.mc, m.holdersPow);
    var v = Math.round(base * (1 + (m.holdersGrowthPerMin || 0) * minutes));
    if (v > this.holderCount) this.holderCount = v;
    return this.holderCount;
  };

  Sim.prototype.top10 = function () {
    var m = CFG.metrics;
    var span = Math.log(this.mcNow / CFG.pump.mcStart);
    var prog = clamp(Math.log(this.mc / CFG.pump.mcStart) / (span || 1), 0, 1);
    return m.top10Start - (m.top10Start - m.top10Floor) * Math.pow(prog, 0.75);
  };

  /** Изменение капитализации за последние `minutes` минут. */
  Sim.prototype.changeOver = function (minutes) {
    var target = this.simNow - minutes * 60000;
    var ref = null;
    for (var i = this.candles.length - 1; i >= 0; i--) {
      if (this.candles[i].t <= target) { ref = this.candles[i].c; break; }
    }
    if (ref == null) ref = this.candles[0].o;
    if (!ref) return null;
    return (this.mc / ref - 1) * 100;
  };

  /** Изменение для окна блока About: 5M — по свечам, остальные — от опорной. */
  Sim.prototype.changeWindow = function (key) {
    if (key === '5M') return this.changeOver(this.window5mSec / 60);
    var ref = this.windowRefMc[key];
    if (!ref) return this.changeOver(1440);
    return (this.mc / ref - 1) * 100;
  };

  /** «24H change» в шапке — то же число, что 1D в блоке About. */
  Sim.prototype.change24h = function () {
    return this.changeWindow('1D');
  };

  /**
   * Поток сделок для окна. В истории лежит 96 секунд, это и есть окно 1H;
   * 4H и 1D домножаются на свои коэффициенты — иначе три окна показывали бы
   * одинаковые цифры.
   */
  Sim.prototype.flowWindow = function (key) {
    if (key === '5M') return this.flow(this.window5mSec / 60);
    var base = this.flow(1440);
    var f = this.flowFactor[key];
    if (!f || f === 1) return base;
    return {
      buys: Math.round(base.buys * f),
      sells: Math.round(base.sells * f),
      buyers: Math.round(base.buyers * f),
      sellers: Math.round(base.sellers * f),
      buyVol: base.buyVol * f,
      sellVol: base.sellVol * f,
      volume: base.volume * f
    };
  };

  /**
   * Статистика сделок за окно (минуты). Счётчики свечей заполняет
   * accumulateStats по формулам dexscreener, включая buyers/sellers
   * (трейдеры как доля от числа сделок) — отдельная эвристика не нужна.
   */
  Sim.prototype.flow = function (minutes) {
    var cutoff = this.simNow - minutes * 60000;
    var r = { buys: 0, sells: 0, buyVol: 0, sellVol: 0, volume: 0, buyers: 0, sellers: 0 };
    for (var i = this.candles.length - 1; i >= 0; i--) {
      var c = this.candles[i];
      if (c.t + this.candleMs < cutoff) break;
      r.buys += c.nb || 0;
      r.sells += c.ns || 0;
      r.buyVol += c.vb || 0;
      r.sellVol += c.vs || 0;
      r.volume += c.v || 0;
      r.buyers += c.br || 0;
      r.sellers += c.sr || 0;
    }
    return r;
  };

  /** Топ холдеров по стоимости позиции. */
  Sim.prototype.topHolders = function (limit) {
    var price = this.price();
    var list = [];
    for (var i = 0; i < this.traders.length; i++) {
      var tr = this.traders[i];
      if (tr.tokens <= 0) continue;
      var value_ = tr.tokens * price;
      list.push({
        trader: tr,
        tokens: tr.tokens,
        value: value_,
        pnl: value_ - tr.costUsd,
        pnlPct: tr.costUsd > 0 ? (value_ / tr.costUsd - 1) * 100 : 0,
        avgMc: tr.costUsd > 0 ? (tr.costUsd / tr.tokens) * this.supply : this.mc,
        avgPrice: tr.tokens > 0 ? tr.costUsd / tr.tokens : price,
        holdMs: Math.max(1000, this.simNow - tr.firstSeen)
      });
    }
    list.sort(function (a, b) { return b.value - a.value; });
    return list.slice(0, limit || 30);
  };

  Sim.prototype.ageMs = function () {
    return CFG.token.ageMinutes * 60000 + (this.simNow - this.openedAt);
  };

  /** Момент «создания» токена — для подписей вида «Aug 29, 2026, 8:49 PM». */
  Sim.prototype.createdAt = function () {
    return this.startWall - CFG.token.ageMinutes * 60000;
  };

  /**
   * Слепок «как у dexscreener»: все цифры страницы считаются одним пакетом
   * и обновляются не чаще, чем раз в CFG.uiMs (по умолчанию — раз в секунду).
   * Шапка, блок About и таблицы читают именно его, поэтому значения на
   * странице всегда согласованы между собой.
   */
  Sim.prototype.dexFeed = function (nowReal) {
    if (nowReal == null) nowReal = Date.now();
    if (this._feed && (nowReal - this._feedAt) < CFG.uiMs) return this._feed;
    this._feedAt = nowReal;

    var day = this.flowWindow('1D');
    this.volume24h = day.volume;

    this._feed = {
      at: nowReal,
      mc: this.mc,
      fdv: this.mc,
      price: this.price(),
      liquidity: this.liquidity(),
      volume24h: day.volume,
      holders: this.holders(),
      top10: this.top10(),
      ageMs: this.ageMs(),
      change: {
        '5M': this.changeWindow('5M'),
        '1H': this.changeWindow('1H'),
        '4H': this.changeWindow('4H'),
        '1D': this.changeWindow('1D')
      },
      flow: {
        '5M': this.flowWindow('5M'),
        '1H': this.flowWindow('1H'),
        '4H': this.flowWindow('4H'),
        '1D': day
      }
    };
    return this._feed;
  };

  /** Слепок для графика. */
  Sim.prototype.chartSnapshot = function () {
    var out = [];
    var from = Math.max(0, this.candles.length - 400);
    for (var i = from; i < this.candles.length; i++) {
      var c = this.candles[i];
      out.push([c.t, c.o, c.h, c.l, c.c, c.v]);
    }
    return {
      type: 'fomo:chart',
      candles: out,
      candleMs: this.candleMs,
      supply: this.supply,
      simNow: this.simNow,
      mc: this.mc,
      change24h: this.change24h()
    };
  };

  global.FomoSim = Sim;
  global.FomoUtil = {
    rnd: rnd, rndInt: rndInt, pick: pick, clamp: clamp, gauss: gauss,
    value: value, randomAddress: randomAddress, shortAddress: shortAddress
  };
})(typeof window !== 'undefined' ? window : this);
