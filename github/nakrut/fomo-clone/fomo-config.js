/*
 * fomo-config.js — общие настройки фейкового пампа.
 * Файл подключается и на основной странице, и внутри iframe с графиком.
 * Меняйте значения здесь — перезагрузка страницы применит их сразу.
 */
(function (global) {
  'use strict';

  var CFG = {
    /* ---------- время ---------- */
    // 1 = реальное время: свеча 1s закрывается каждую реальную секунду,
    // возраст токена растёт на 1m каждую реальную минуту.
    speed: 1,
    // длительность одной свечи в секундах симуляции (график подписан как 1s)
    candleSeconds: 1,
    // частота пересчёта модели и перерисовки графика, мс
    tickMs: 100,
    // частота обновления «данных» (шапка, About, таблицы, футер) — ровно 1 раз в секунду
    uiMs: 1000,

    /* ---------- токен ---------- */
    token: {
      symbol: 'BREAKING',      // тикер — белым в шапке
      name: 'Breaking',        // название — серым под тикером
      supply: 1e9,
      network: 'Solana',
      // null => новый случайный Solana-адрес при каждой загрузке страницы
      address: null,
      // «возраст» токена на момент открытия, в минутах (1 => «1m»)
      ageMinutes: 1,
      // что стоит в самом снимке страницы: по этим строкам скрипт находит
      // и подменяет тикер / название / адрес в готовой вёрстке
      captureSymbol: 'BREAKING',
      captureName: 'Breaking',
      captureAddress: '6vRoghyPdwLuD6rqPeS26XfLEmvNJmDy3aViFWQ6BJbu'
    },

    /* ---------- траектория пампа (в капитализации, $) ----------
     * Памп уже случился к моменту открытия страницы, причём закончился
     * rampEndsAgoSeconds назад — дальше идёт «пила» на верхах с большими
     * свечами и стенкой объёма, как на скриншоте с $3.1M.
     *
     * Раскладка окна графика (history.bars свечей):
     *   dead → ramp (rampSeconds) → top (rampEndsAgoSeconds) → сейчас
     */
    pump: {
      mcStart: 25,                 // с чего начинался токен
      mcNow: [2400000, 3600000],   // капитализация «сейчас» (случайно из диапазона)
      rampSeconds: 32,             // сколько секунд заняла вертикаль
      rampEndsAgoSeconds: 56,      // сколько секунд назад волна выдохлась
      curve: 1.1,                  // >1 — параболический загиб вверх в конце
      driftPerHour: 3,             // дрейф после волны, доля в час

      // во время волны
      noise: 0.03,                 // амплитуда шума (доля цены)
      noiseTheta: 0.35,            // скорость возврата шума к нулю, 1/с
      pullbackChancePerSec: 0.05,  // вероятность начала откатa за секунду
      pullbackDepth: [0.03, 0.12], // глубина откатa

      // после волны: болтанка на верхах
      noiseTop: 0.055,
      noiseThetaTop: 0.6,
      pullbackChancePerSecTop: 0.12,
      pullbackDepthTop: [0.03, 0.12],

      pullbackSeconds: [2, 9]      // длительность откатa
    },

    /* ---------- история свечей ---------- */
    history: {
      bars: 96,        // сколько 1s-свечей нарисовано до «сейчас»
      subSteps: 8      // подшагов внутри свечи (тени)
    },

    /* ---------- поток сделок (лента Swaps + позиции холдеров) ---------- */
    trades: {
      // сделок в минуту симуляции: в начале волны и на пике
      ratePerMinStart: 6,
      ratePerMinPeak: 480,
      buyRatio: 0.76,       // доля покупок на росте
      buyRatioDip: 0.34,    // доля покупок во время откатa
      // размер сделки как доля капитализации (логнормально между границами)
      sizeFracMin: 0.0006,
      sizeFracMax: 0.02,
      feedLimit: 60,        // сколько строк держим в таблице Swaps
      holdersLimit: 40      // сколько строк держим в таблице Holders
    },

    /* ---------- статистика блока About (формулы из dexscreener) ----------
     * Порт функции buildTickStats из проекта dexscreener
     * (js/overlay-live-chart.js): за один «тик» набирается пачка сделок,
     * доля покупок привязана к цвету свечи, трейдеры — доля от числа сделок,
     * объём покупок ~половина. Эти счётчики (buys/sells/vol/buyers/sellers)
     * накапливаются по свечам и питают блок About и «24H Vol.» в шапке.
     * От ленты Swaps они не зависят — как и в оригинале dexscreener.
     */
    dex: {
      // сделок за тик — как в оригинале (randInt(10, 25))
      txnsPerTick: [10, 25],
      // сколько таких тиков приходится на одну 1s-свечу: в начале волны и на пике
      ticksPerCandleStart: 0.5,
      ticksPerCandlePeak: 6.0,
      // доля покупок: база из оригинала + перекос по цвету свечи + перекос fomo
      buyShareBase: [0.46, 0.54],   // оригинал
      buyShareDirBias: 0.035,       // зелёная свеча -> больше покупок (оригинал)
      buyShareExtraBias: 0.17,      // общий перекос в сторону покупок (fomo)
      buyShareClamp: [0.42, 0.76],
      // объём одной сделки как доля капитализации (логнормально)
      volumePerTxnFrac: [0.00006, 0.0016],
      buyVolJitter: 0.07,           // оригинал
      buyVolClamp: [0.38, 0.62],    // оригинал
      // трейдеры = доля от числа сделок (в оригинале 0.35..0.6; здесь выше,
      // чтобы buyers/sellers держались около 0.8 от buys/sells, как на fomo)
      tradersFrac: [0.74, 0.86],
      buyerJitter: 0.06,            // оригинал
      buyerClamp: [0.35, 0.65]      // оригинал
    },

    /* ---------- производные метрики ---------- */
    metrics: {
      liquidityFrac: 0.062, // ликвидность ≈ доля капитализации
      top10Start: 99.99,
      top10Floor: 21.5,

      /* Окна 5M / 1H / 4H / 1D — как у dexscreener: у каждого своя точка
       * отсчёта, поэтому четыре кнопки в блоке About показывают разные числа,
       * а не одно и то же. Значения — доли от капитализации «сейчас»,
       * выбираются случайно при загрузке. 1D используется и в плитке
       * «24H change», чтобы шапка и About совпадали.
       */
      windowRef: {
        '1H': [0.0013, 0.0022],
        '4H': [0.00075, 0.00105],
        '1D': [0.00070, 0.00092]
      },
      // окно 5M считается по настоящим свечам: сколько секунд смотреть назад.
      // Меньше «пилы на верхах», чтобы 5M было заметно меньше 1H.
      window5mSeconds: 20,
      // поток сделок за 4H / 1D — множители к тому, что есть в истории (1H)
      flowFactor: {
        '4H': [1.12, 1.26],
        '1D': [1.3, 1.5]
      },
      // холдеры считаются от капитализации: holdersMul * mc^holdersPow
      holdersMul: 1.55,
      holdersPow: 0.62,
      // плюс медленный набор холдеров со временем, доля в минуту
      holdersGrowthPerMin: 0.02
    },

    /* ---------- график ---------- */
    chart: {
      visibleBars: 96,
      rightOffsetBars: 8,
      intervalLabel: '1s',   // подпись таймфрейма в тулбаре и легенде
      showClock: false,      // часы «18:47:03 UTC+3» в нижней панели
      bg: '#060510',
      grid: 'rgb(20, 19, 29)',
      up: 'rgb(33, 201, 94)',
      down: 'rgb(255, 98, 46)',
      volUp: 'rgba(33, 201, 94, 0.55)',
      volDown: 'rgba(255, 98, 46, 0.55)',
      volumeFrac: 0.18,     // какую долю высоты панели занимает объём
      axisText: 'rgb(184, 184, 184)',
      axisFont: '12px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif',
      priceAxisMinWidth: 52,
      timeAxisHeight: 28,
      // стартовый режим шкалы: 'mcap' | 'price'
      mode: 'mcap',
      tzLabel: 'UTC+3',
      tzOffsetHours: 3,
      // растянуть область графика на всю высоту окна: таблица Holders уезжает
      // под сгиб, страница выглядит как на скриншоте с $3.1M
      fillViewport: true
    },

    /* ---------- криптовалюты ----------
     * Один список на две вещи: курсы в футере и вкладки Watchlist / Crypto
     * в левой панели. Цены, капитализация и суточный объём тянутся с
     * публичного API CoinGecko (обычный GET, ничего не отправляет) и
     * переспрашиваются каждые cryptoRefreshMs. Показываются как есть, с
     * микродрожанием cryptoJitter — чтобы цифры жили, но остались курсом.
     * fallback — снимок на 31.08.2026, если сети нет.
     */
    cryptoLive: true,
    cryptoUrl: 'https://api.coingecko.com/api/v3/simple/price' +
      '?ids=bitcoin,ethereum,solana,hyperliquid&vs_currencies=usd' +
      '&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true',
    cryptoRefreshMs: 15000,
    cryptoJitter: 0.00008,     // 0.008% — дрожание вокруг настоящей цены
    cryptoJitterMax: 0.00025,  // не дальше 0.025% от курса
    // что показывать в Watchlist / Crypto и в футере (порядок общий)
    crypto: [
      {
        id: 'bitcoin', symbol: 'BTC', img: 'bitcoin.png',
        href: 'https://fomo.family/tokens/solana/cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij',
        price: 78986, change: 0.48, mc: 1585862617428, vol: 32156486818
      },
      {
        id: 'ethereum', symbol: 'ETH', img: 'ethereum.png',
        href: 'https://fomo.family/tokens/solana/7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs',
        price: 2479.36, change: 0.04, mc: 299212594652, vol: 14619414238
      },
      {
        id: 'solana', symbol: 'SOL', img: '100010811.webp',
        href: 'https://fomo.family/tokens/solana/So11111111111111111111111111111111111111112',
        price: 103.74, change: -0.57, mc: 60715335520, vol: 3676005185
      },
      {
        id: 'hyperliquid', symbol: 'HYPE', img: 'hyperliquid.jpg',
        href: 'https://fomo.family/tokens/solana/98sMhvDwXj1RQi5c5Mndm3vPe9cBqPrbLaufMXFNMh5g',
        price: 84.93, change: 3.67, mc: 18891585273, vol: 1100171728
      }
    ]
  };

  global.FOMO_CFG = CFG;
})(typeof window !== 'undefined' ? window : this);
