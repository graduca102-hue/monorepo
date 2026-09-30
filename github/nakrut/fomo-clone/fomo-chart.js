/*
 * fomo-chart.js — рисует свечи внутри сохранённого фрейма TradingView.
 *
 * Разметка чарт-библиотеки взята из снимка как есть (тулбар, оси, легенда),
 * но её canvas'ы пустые — этот скрипт берёт на себя раскладку и отрисовку:
 * сетка, свечи, шкалы, курсор, легенда, часы.
 *
 * Данные приходят из родительского окна через postMessage.
 */
(function () {
  'use strict';

  var CFG = window.FOMO_CFG;
  var C = CFG.chart;
  var F = window.F;

  /* ------------------------------------------------------------------- узлы */

  var q = function (s) { return document.querySelector(s); };

  var el = {
    root: q('.js-rootresizer__contents'),
    top: q('.layout__area--top'),
    left: q('.layout__area--left'),
    center: q('.layout__area--center'),
    right: q('.layout__area--right'),
    analysis: q('.layout__area--analysis'),
    container: q('.chart-container'),
    widget: q('.chart-widget'),
    controls: q('.chart-controls-bar'),
    pane: q('.pane'),
    gui: q('.chart-gui-wrapper'),
    paneCanvas: q('canvas[data-name="pane-canvas"]'),
    paneTop: q('canvas[data-qa-id="pane-top-canvas"]'),
    priceAxis: q('.price-axis'),
    timeAxis: q('.time-axis'),
    legend: q('.chart-gui-wrapper__legend'),
    clock: q('button[data-name="time-zone-menu"] .js-button-text'),
    tzBtn: q('button[data-name="time-zone-menu"]'),
    modeBtn: q('.customButton-qqNP9X6e')
  };

  if (!el.pane || !el.paneCanvas) return;

  /* ------------------------------------------------- тулбар и легенда графика */

  /**
   * Таймфрейм в тулбаре и легенде + скрытие часов в нижней панели.
   * Разметка из снимка не менялась — правим только текст.
   */
  (function applyChrome() {
    var label = C.intervalLabel || '1m';

    var vals = document.querySelectorAll('.value-nkSYIvDw');
    for (var i = 0; i < vals.length; i++) vals[i].textContent = label;

    var btn = q('#header-toolbar-intervals button');
    if (btn) {
      var human = label === '1s' ? '1 second' : label;
      btn.setAttribute('data-tooltip', human);
      btn.setAttribute('aria-label', human);
    }

    var symTitle = q('.title-YTFIJ62h[aria-label="Change symbol"]');
    if (symTitle) symTitle.textContent = CFG.token.name;

    var intTitle = q('.title-YTFIJ62h[aria-label="Change interval"]');
    if (intTitle) intTitle.textContent = label;

    // часы «18:47:03 UTC+3» в нижней панели — по просьбе убраны
    if (!C.showClock && el.tzBtn) {
      var wrap = el.tzBtn.closest('.inline-BXXUwft2');
      var outer = wrap && wrap.parentElement &&
        wrap.parentElement.classList.contains('inline-BXXUwft2') ? wrap.parentElement : wrap;
      if (outer) {
        var next = outer.nextElementSibling;
        // следом идёт разделитель — без часов он висел бы в воздухе
        if (next && next.querySelector && next.querySelector('[class*="separator-"]')) next.remove();
        outer.remove();
      }
      el.clock = null;
      el.tzBtn = null;
    }
  })();

  var markup = el.widget ? el.widget.querySelector('.chart-markup-table') : null;
  var priceAxisBox = el.priceAxis ? el.priceAxis.parentElement : null;
  var priceCanvases = el.priceAxis ? el.priceAxis.querySelectorAll('canvas') : [];
  var timeCanvases = el.timeAxis ? el.timeAxis.querySelectorAll('canvas') : [];
  // правый нижний угол (под шкалой цены, справа от шкалы времени)
  var cornerBox = (function () {
    if (!el.timeAxis) return null;
    var row = el.timeAxis.parentElement;
    return row ? row.querySelector('.price-axis-container:last-child') : null;
  })();
  var cornerCanvases = cornerBox ? cornerBox.querySelectorAll('canvas') : [];

  var legendVals = {};
  if (el.legend) {
    ['O', 'H', 'L', 'C'].forEach(function (k) {
      var item = el.legend.querySelector('[data-test-id-value-title="' + k + '"] .valueValue-YTFIJ62h');
      if (item) legendVals[k] = item;
    });
    // последний элемент без подписи — «$14 (+117.03%)»
    var items = el.legend.querySelectorAll('.valueItem-YTFIJ62h');
    for (var i = items.length - 1; i >= 0; i--) {
      if (!items[i].classList.contains('blockHidden-LcYdo2N6')) {
        var v = items[i].querySelector('.valueValue-YTFIJ62h');
        if (v && !legendVals.change && items[i].getAttribute('data-test-id-value-title') === '') {
          legendVals.change = v;
          break;
        }
      }
    }
    // отдельный источник легенды — индикатор Volume
    var study = el.legend.querySelector('.item-YTFIJ62h.study-YTFIJ62h .valueValue-YTFIJ62h');
    if (study) legendVals.volume = study;
  }

  /* ------------------------------------------------------------- состояние */

  var state = {
    candles: [],
    candleMs: CFG.candleSeconds * 1000,
    supply: CFG.token.supply,
    simNow: Date.now(),
    mode: C.mode,
    hover: null,          // {x, y}
    layout: null
  };

  /* --------------------------------------------------------------- утилиты */

  var dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));

  function sizeCanvas(cv, w, h) {
    if (!cv) return null;
    var pw = Math.max(1, Math.round(w * dpr));
    var ph = Math.max(1, Math.round(h * dpr));
    if (cv.width !== pw) cv.width = pw;
    if (cv.height !== ph) cv.height = ph;
    cv.style.width = w + 'px';
    cv.style.height = h + 'px';
    var ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return ctx;
  }

  function px(v) { return Math.round(v) + 0.5; }

  function niceStep(raw) {
    if (!(raw > 0)) return 1;
    var exp = Math.floor(Math.log10(raw));
    var base = Math.pow(10, exp);
    var f = raw / base;
    var mult = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return mult * base;
  }

  // шаг подписей на шкале времени, в секундах (годится и для 1s, и для 1m)
  var TIME_STEPS_SEC = [
    1, 2, 5, 10, 15, 30,
    60, 120, 180, 300, 600, 900, 1800,
    3600, 7200, 10800, 14400, 21600, 43200, 86400
  ];

  function niceTimeStep(rawSec) {
    for (var i = 0; i < TIME_STEPS_SEC.length; i++) {
      if (TIME_STEPS_SEC[i] >= rawSec) return TIME_STEPS_SEC[i];
    }
    return TIME_STEPS_SEC[TIME_STEPS_SEC.length - 1];
  }

  /** На секундных таймфреймах подписи идут с секундами. */
  function fmtTime(ms) {
    return state.candleMs < 60000
      ? F.clock(ms, C.tzOffsetHours)
      : F.hhmm(ms, C.tzOffsetHours);
  }

  function toDisplay(mc) {
    return state.mode === 'price' ? mc / state.supply : mc;
  }

  function fmtAxis(mc) {
    var v = toDisplay(mc);
    return state.mode === 'price' ? F.priceText(v) : F.money(v);
  }

  /* --------------------------------------------------------------- раскладка */

  /** Ширина шкалы цены подстраивается под самую длинную подпись. */
  function measureAxisWidth() {
    var ctx = el.paneCanvas.getContext('2d');
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.font = C.axisFont;
    var w = 0;
    var bars = visibleSlice();
    var samples = ['$00.0M'];
    if (bars.length) {
      var hi = -Infinity;
      for (var i = 0; i < bars.length; i++) if (bars[i].h > hi) hi = bars[i].h;
      samples = [fmtAxis(hi), fmtAxis(bars[bars.length - 1].c)];
    }
    for (var j = 0; j < samples.length; j++) {
      w = Math.max(w, ctx.measureText(samples[j]).width);
    }
    ctx.restore();
    return Math.max(C.priceAxisMinWidth, Math.ceil(w) + 16);
  }

  function relayout() {
    var W = document.documentElement.clientWidth;
    var H = document.documentElement.clientHeight;
    if (!W || !H) return null;

    var TOP_H = 38, TOP_GAP = 4, LEFT_W = 5, LEFT_GAP = 9;
    var areaTop = TOP_H + TOP_GAP;
    var areaH = Math.max(80, H - areaTop);

    if (el.top) { el.top.style.width = W + 'px'; el.top.style.height = TOP_H + 'px'; }
    if (el.left) { el.left.style.top = areaTop + 'px'; el.left.style.width = LEFT_W + 'px'; el.left.style.height = areaH + 'px'; }
    if (el.right) { el.right.style.top = areaTop + 'px'; el.right.style.height = areaH + 'px'; }
    if (el.analysis) { el.analysis.style.top = areaTop + 'px'; el.analysis.style.width = W + 'px'; el.analysis.style.height = areaH + 'px'; }

    var centerW = W - LEFT_GAP;
    if (el.center) {
      el.center.style.top = areaTop + 'px';
      el.center.style.left = LEFT_GAP + 'px';
      el.center.style.width = centerW + 'px';
      el.center.style.height = areaH + 'px';
    }

    var controlsH = el.controls ? (el.controls.offsetHeight || 39) : 0;
    var chartH = Math.max(60, areaH - controlsH);

    if (el.container) { el.container.style.width = centerW + 'px'; el.container.style.height = chartH + 'px'; }
    if (el.widget) { el.widget.style.width = centerW + 'px'; el.widget.style.height = chartH + 'px'; }
    if (markup) { markup.style.width = centerW + 'px'; markup.style.height = chartH + 'px'; }

    var axisW = measureAxisWidth();
    var timeH = C.timeAxisHeight;
    var paneW = Math.max(40, centerW - axisW);
    var paneH = Math.max(40, chartH - timeH);

    if (el.pane) { el.pane.style.width = paneW + 'px'; el.pane.style.height = paneH + 'px'; }
    if (el.gui) { el.gui.style.width = paneW + 'px'; el.gui.style.height = paneH + 'px'; }

    if (priceAxisBox) {
      priceAxisBox.style.width = axisW + 'px';
      priceAxisBox.style.minWidth = axisW + 'px';
      priceAxisBox.style.height = paneH + 'px';
    }
    if (el.priceAxis) {
      el.priceAxis.style.width = axisW + 'px';
      el.priceAxis.style.minWidth = axisW + 'px';
      el.priceAxis.style.height = paneH + 'px';
    }
    if (el.timeAxis) { el.timeAxis.style.width = paneW + 'px'; el.timeAxis.style.height = timeH + 'px'; }
    if (cornerBox) {
      cornerBox.style.width = axisW + 'px';
      cornerBox.style.minWidth = axisW + 'px';
      cornerBox.style.height = timeH + 'px';
    }
    var timeRowLeft = el.timeAxis ? el.timeAxis.parentElement.querySelector('.price-axis-container') : null;
    if (timeRowLeft && timeRowLeft !== cornerBox) timeRowLeft.style.height = timeH + 'px';

    state.layout = { W: W, H: H, paneW: paneW, paneH: paneH, axisW: axisW, timeH: timeH };
    return state.layout;
  }

  /* --------------------------------------------------------------- отрисовка */

  function visibleSlice() {
    var n = C.visibleBars;
    var all = state.candles;
    var from = Math.max(0, all.length - n);
    return all.slice(from);
  }

  function draw() {
    var L = state.layout || relayout();
    if (!L) return;
    // цифры растут — шкала цены может стать шире
    if (L.axisW !== measureAxisWidth()) {
      L = relayout();
      if (!L) return;
    }

    var bars = visibleSlice();
    var slots = C.visibleBars + C.rightOffsetBars;
    var spacing = L.paneW / slots;
    var bodyW = Math.max(1, Math.floor(spacing * 0.68));
    if (bodyW % 2 === 0 && bodyW > 1) bodyW -= 1;

    /* --- диапазон --- */
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < bars.length; i++) {
      if (bars[i].l < lo) lo = bars[i].l;
      if (bars[i].h > hi) hi = bars[i].h;
    }
    if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
    if (hi === lo) { hi = lo * 1.05 + 1; lo = lo * 0.95; }
    var pad = (hi - lo) * 0.12;
    lo -= pad; hi += pad;
    if (lo < 0) lo = 0;

    var yOf = function (v) { return L.paneH - ((v - lo) / (hi - lo)) * L.paneH; };
    var xOf = function (idx) { return (idx + 0.5) * spacing; };

    var pane = sizeCanvas(el.paneCanvas, L.paneW, L.paneH);
    var priceCtx = priceCanvases[0] ? sizeCanvas(priceCanvases[0], L.axisW, L.paneH) : null;
    var timeCtx = timeCanvases[0] ? sizeCanvas(timeCanvases[0], L.paneW, L.timeH) : null;
    var overlay = sizeCanvas(el.paneTop, L.paneW, L.paneH);
    var priceOv = priceCanvases[1] ? sizeCanvas(priceCanvases[1], L.axisW, L.paneH) : null;
    var timeOv = timeCanvases[1] ? sizeCanvas(timeCanvases[1], L.paneW, L.timeH) : null;
    if (!pane) return;

    // правый нижний угол между осями: без заливки сквозь него просвечивает
    // серый фон самой чарт-библиотеки
    for (var ci = 0; ci < cornerCanvases.length; ci++) {
      var cc = sizeCanvas(cornerCanvases[ci], L.axisW, L.timeH);
      if (cc && ci === 0) { cc.fillStyle = C.bg; cc.fillRect(0, 0, L.axisW, L.timeH); }
    }

    pane.fillStyle = C.bg;
    pane.fillRect(0, 0, L.paneW, L.paneH);

    /* --- сетка + шкала цены --- */
    var targetRows = Math.max(2, Math.round(L.paneH / 52));
    var step = niceStep((hi - lo) / targetRows);
    var first = Math.ceil(lo / step) * step;

    if (priceCtx) {
      priceCtx.fillStyle = C.bg;
      priceCtx.fillRect(0, 0, L.axisW, L.paneH);
      priceCtx.font = C.axisFont;
      priceCtx.fillStyle = C.axisText;
      priceCtx.textAlign = 'left';
      priceCtx.textBaseline = 'middle';
    }

    pane.strokeStyle = C.grid;
    pane.lineWidth = 1;
    for (var v = first; v <= hi; v += step) {
      var y = yOf(v);
      if (y < 0 || y > L.paneH) continue;
      pane.beginPath();
      pane.moveTo(0, px(y));
      pane.lineTo(L.paneW, px(y));
      pane.stroke();
      // подписи у самых кромок обрезались бы — их не рисуем
      if (priceCtx && y > 8 && y < L.paneH - 8) {
        priceCtx.fillText(fmtAxis(v), 6, Math.round(y));
      }
    }

    /* --- сетка + шкала времени --- */
    var barSec = state.candleMs / 1000;
    var barsPerLabel = Math.max(1, Math.round(90 / spacing));
    var stepSec = niceTimeStep(barSec * barsPerLabel);
    var stepMs = stepSec * 1000;
    var tzMs = C.tzOffsetHours * 3600000;

    if (timeCtx) {
      timeCtx.fillStyle = C.bg;
      timeCtx.fillRect(0, 0, L.paneW, L.timeH);
      timeCtx.font = C.axisFont;
      timeCtx.fillStyle = C.axisText;
      timeCtx.textAlign = 'center';
      timeCtx.textBaseline = 'middle';
    }

    var prevDay = null;
    for (var b = 0; b < bars.length; b++) {
      var t = bars[b].t;
      if (((t + tzMs) % stepMs) !== 0) continue;
      var x = xOf(b);
      pane.beginPath();
      pane.moveTo(px(x), 0);
      pane.lineTo(px(x), L.paneH);
      pane.stroke();
      if (timeCtx) {
        var d = new Date(t + tzMs);
        var day = d.getUTCDate();
        var label = (prevDay !== null && day !== prevDay) || stepSec >= 86400
          ? F.dayLabel(t, C.tzOffsetHours)
          : fmtTime(t);
        prevDay = day;
        // подписи, которые не влезают целиком, TradingView не рисует
        var half = timeCtx.measureText(label).width / 2;
        if (x - half >= 2 && x + half <= L.paneW - 2) {
          timeCtx.fillText(label, Math.round(x), L.timeH / 2);
        }
      }
    }

    /* --- гистограмма объёма (индикатор Volume) --- */
    var volMax = 0;
    for (var vi = 0; vi < bars.length; vi++) if (bars[vi].v > volMax) volMax = bars[vi].v;
    if (volMax > 0) {
      var volH = L.paneH * C.volumeFrac;
      for (var vj = 0; vj < bars.length; vj++) {
        var bv = bars[vj];
        if (!bv.v) continue;
        var bh = Math.max(1, (bv.v / volMax) * volH);
        pane.fillStyle = bv.c >= bv.o ? C.volUp : C.volDown;
        pane.fillRect(Math.round(xOf(vj) - bodyW / 2), Math.round(L.paneH - bh), bodyW, Math.ceil(bh));
      }
    }

    /* --- свечи --- */
    for (var k = 0; k < bars.length; k++) {
      var c = bars[k];
      var up = c.c >= c.o;
      var col = up ? C.up : C.down;
      var cx = xOf(k);
      var yo = yOf(c.o), yc = yOf(c.c), yh = yOf(c.h), yl = yOf(c.l);

      pane.strokeStyle = col;
      pane.fillStyle = col;
      pane.lineWidth = 1;
      pane.beginPath();
      pane.moveTo(px(cx), Math.round(yh));
      pane.lineTo(px(cx), Math.round(yl));
      pane.stroke();

      var top = Math.min(yo, yc);
      var hgt = Math.max(1, Math.abs(yc - yo));
      pane.fillRect(Math.round(cx - bodyW / 2), Math.round(top), bodyW, Math.round(hgt));
    }

    /* --- линия последней цены + бейдж --- */
    var last = bars[bars.length - 1];
    if (last) {
      var lastUp = last.c >= last.o;
      var lastCol = lastUp ? C.up : C.down;
      var ly = yOf(last.c);
      pane.save();
      pane.strokeStyle = lastCol;
      pane.lineWidth = 1;
      pane.setLineDash([1, 2]);
      pane.beginPath();
      pane.moveTo(0, px(ly));
      pane.lineTo(L.paneW, px(ly));
      pane.stroke();
      pane.restore();

      if (priceCtx) {
        var label = fmtAxis(last.c);
        var bh = 16;
        var by = Math.max(0, Math.min(L.paneH - bh, ly - bh / 2));
        priceCtx.fillStyle = lastCol;
        priceCtx.fillRect(0, Math.round(by), L.axisW, bh);
        priceCtx.fillStyle = C.bg;
        priceCtx.fillText(label, 6, Math.round(by + bh / 2));
      }
    }

    /* --- курсор --- */
    if (state.hover && overlay) {
      var hx = state.hover.x, hy = state.hover.y;
      overlay.save();
      overlay.strokeStyle = 'rgba(150, 150, 165, 0.65)';
      overlay.setLineDash([4, 3]);
      overlay.lineWidth = 1;
      overlay.beginPath();
      overlay.moveTo(px(hx), 0); overlay.lineTo(px(hx), L.paneH);
      overlay.moveTo(0, px(hy)); overlay.lineTo(L.paneW, px(hy));
      overlay.stroke();
      overlay.restore();

      if (priceOv) {
        var hv = lo + (1 - hy / L.paneH) * (hi - lo);
        var t2 = fmtAxis(hv);
        priceOv.font = C.axisFont;
        priceOv.textBaseline = 'middle';
        priceOv.fillStyle = 'rgb(40, 40, 52)';
        priceOv.fillRect(0, Math.round(hy - 8), L.axisW, 16);
        priceOv.fillStyle = 'rgb(240, 240, 240)';
        priceOv.fillText(t2, 6, Math.round(hy));
      }
      if (timeOv) {
        var idx = Math.floor(hx / spacing);
        var tt = bars[idx] ? bars[idx].t : (bars.length ? bars[bars.length - 1].t + (idx - bars.length + 1) * state.candleMs : 0);
        var tl = fmtTime(tt);
        timeOv.font = C.axisFont;
        timeOv.textAlign = 'center';
        timeOv.textBaseline = 'middle';
        var tw = timeOv.measureText(tl).width + 12;
        timeOv.fillStyle = 'rgb(40, 40, 52)';
        timeOv.fillRect(Math.round(hx - tw / 2), 4, tw, 18);
        timeOv.fillStyle = 'rgb(240, 240, 240)';
        timeOv.fillText(tl, Math.round(hx), 13);
      }
    }

    /* --- легенда --- */
    var shown = last;
    if (state.hover) {
      var hi2 = Math.floor(state.hover.x / spacing);
      if (bars[hi2]) shown = bars[hi2];
    }
    if (shown) {
      var lc = shown.c >= shown.o ? C.up : C.down;
      ['O', 'H', 'L', 'C'].forEach(function (k2) {
        var node = legendVals[k2];
        if (!node) return;
        var val = k2 === 'O' ? shown.o : k2 === 'H' ? shown.h : k2 === 'L' ? shown.l : shown.c;
        node.textContent = fmtAxis(val);
        node.style.color = lc;
      });
      if (legendVals.change) {
        var diff = shown.c - shown.o;
        var pctv = shown.o ? (diff / shown.o) * 100 : 0;
        legendVals.change.textContent = fmtAxis(Math.abs(diff)) +
          ' (' + (pctv >= 0 ? '+' : '-') + Math.abs(pctv).toFixed(2) + '%)';
        legendVals.change.style.color = lc;
      }
      if (legendVals.volume) {
        legendVals.volume.textContent = F.money(shown.v).replace('$', '');
        legendVals.volume.style.color = lc;
      }
    }

    /* --- часы в нижней панели --- */
    if (el.clock) el.clock.textContent = F.clock(state.simNow, C.tzOffsetHours) + ' ' + C.tzLabel;
  }

  /* ----------------------------------------------------------------- события */

  var pending = false;
  /** Отложенная перерисовка — для мыши и ресайза, где кадры точно идут. */
  function schedule() {
    if (pending) return;
    pending = true;
    var run = function () { pending = false; draw(); };
    if (window.requestAnimationFrame) requestAnimationFrame(run);
    else setTimeout(run, 16);
  }

  window.addEventListener('resize', function () { relayout(); schedule(); });

  if (el.pane) {
    el.pane.addEventListener('mousemove', function (e) {
      var r = el.pane.getBoundingClientRect();
      state.hover = { x: e.clientX - r.left, y: e.clientY - r.top };
      schedule();
    });
    el.pane.addEventListener('mouseleave', function () {
      state.hover = null;
      schedule();
    });
  }

  if (el.modeBtn) {
    el.modeBtn.style.cursor = 'pointer';
    el.modeBtn.addEventListener('click', function () {
      state.mode = state.mode === 'mcap' ? 'price' : 'mcap';
      var blue = 'color:#516AF6;font-weight:500';
      el.modeBtn.innerHTML = state.mode === 'mcap'
        ? 'Price / <span style="' + blue + '">MCap</span>'
        : '<span style="' + blue + '">Price</span> / MCap';
      relayout();
      schedule();
    });
  }

  window.addEventListener('message', function (e) {
    var d = e.data;
    if (!d || d.type !== 'fomo:chart') return;
    state.candleMs = d.candleMs;
    state.supply = d.supply;
    state.simNow = d.simNow;
    state.candles = d.candles.map(function (a) {
      return { t: a[0], o: a[1], h: a[2], l: a[3], c: a[4], v: a[5] };
    });
    if (!state.layout) relayout();
    // рисуем сразу, не дожидаясь кадра: данные приходят раз в секунду,
    // а rAF в фоновой вкладке не вызывается вовсе
    draw();
  });

  relayout();
  draw();

  function announce() {
    try { parent.postMessage({ type: 'fomo:chart-ready' }, '*'); } catch (err) { }
  }
  announce();
  // родитель может подключиться позже
  var tries = 0;
  var iv = setInterval(function () {
    if (state.candles.length || ++tries > 40) { clearInterval(iv); return; }
    announce();
  }, 150);
})();
