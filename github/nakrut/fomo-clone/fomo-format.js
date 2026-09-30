/*
 * fomo-format.js — форматирование чисел «как на fomo.family».
 * Правила восстановлены по реальному снимку страницы.
 */
(function (global) {
  'use strict';

  var GREEN = 'rgb(33, 201, 94)';
  var RED = 'rgb(255, 98, 46)';
  var GRAY = 'rgb(152, 153, 163)';

  function commas(s) {
    var parts = String(s).split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }

  function stripZero(s) {
    return s.replace(/\.0$/, '');
  }

  /** $25 · $25.3K · $2.5M · $209.3M · $1.2B · $1.58T */
  function money(v) {
    if (v == null || !isFinite(v)) return '--';
    var sign = v < 0 ? '-' : '';
    v = Math.abs(v);
    if (v === 0) return '$0';
    if (v >= 1e12) return sign + '$' + stripZero((v / 1e12).toFixed(2)) + 'T';
    if (v >= 1e9) return sign + '$' + stripZero((v / 1e9).toFixed(1)) + 'B';
    if (v >= 1e6) return sign + '$' + stripZero((v / 1e6).toFixed(1)) + 'M';
    if (v >= 1e3) return sign + '$' + stripZero((v / 1e3).toFixed(1)) + 'K';
    if (v >= 0.5) return sign + '$' + commas(Math.round(v));
    return '$0';
  }

  /** $1,578,780.74 — полная сумма с двумя знаками */
  function moneyFull(v) {
    if (v == null || !isFinite(v)) return '--';
    var sign = v < 0 ? '-' : '';
    return sign + '$' + commas(Math.abs(v).toFixed(2));
  }

  /** 4 · 980 · 37.7K · 1.2M */
  function count(v) {
    v = Math.round(v || 0);
    if (v >= 1e6) return stripZero((v / 1e6).toFixed(1)) + 'M';
    if (v >= 1e3) return stripZero((v / 1e3).toFixed(1)) + 'K';
    return commas(v);
  }

  /** 7.5M BREAKING */
  function tokens(v) {
    if (v >= 1e9) return stripZero((v / 1e9).toFixed(1)) + 'B';
    if (v >= 1e6) return stripZero((v / 1e6).toFixed(1)) + 'M';
    if (v >= 1e3) return stripZero((v / 1e3).toFixed(1)) + 'K';
    return v.toFixed(0);
  }

  /** 103,630.91% */
  function pct(v) {
    if (v == null || !isFinite(v)) return '--';
    return commas(Math.abs(v).toFixed(2)) + '%';
  }

  /**
   * Цена с подстрочным счётчиком нулей: $0.0<sub>7</sub>2531
   * Для значений >= 0.001 — три значащие цифры: $0.209 / $0.0158
   */
  function priceHtml(p) {
    if (!isFinite(p) || p <= 0) return '$0';
    if (p >= 1000) return '$' + commas(p.toFixed(2));
    if (p >= 1) return '$' + p.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
    if (p >= 0.001) {
      // три значащие цифры
      var exp = Math.floor(Math.log10(p));
      var digits = Math.min(8, 2 - exp);
      return '$' + p.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
    }
    var s = p.toFixed(20);
    var frac = s.split('.')[1] || '';
    var zeros = 0;
    while (zeros < frac.length && frac[zeros] === '0') zeros++;
    var sig = frac.slice(zeros, zeros + 4);
    return '$0.0<sub>' + zeros + '</sub>' + sig;
  }

  function priceText(p) {
    return priceHtml(p).replace(/<sub>(\d+)<\/sub>/, function (m, n) {
      return String.fromCharCode(0x2080 + parseInt(n, 10) % 10);
    });
  }

  /** 11h · 1d 22h · 3mo */
  function age(ms) {
    var s = Math.floor(ms / 1000);
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    if (d >= 30) return Math.floor(d / 30) + 'mo';
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h';
    var m = Math.floor(s / 60);
    if (m > 0) return m + 'm';
    return s + 's';
  }

  /** «11 hrs ago» для блока About */
  function ageLong(ms) {
    var s = Math.floor(ms / 1000);
    var d = Math.floor(s / 86400);
    var h = Math.floor(s / 3600);
    var m = Math.floor(s / 60);
    if (d >= 1) return d + (d === 1 ? ' day ago' : ' days ago');
    if (h >= 1) return h + (h === 1 ? ' hr ago' : ' hrs ago');
    if (m >= 1) return m + ' min ago';
    return 'just now';
  }

  /** Относительное время строки в ленте свапов: 3s / 12m / 2h */
  function ago(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) return s + 's';
    var m = Math.floor(s / 60);
    if (m < 60) return m + 'm';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h';
    return Math.floor(h / 24) + 'd';
  }

  /** «2d 13h avg. hold» */
  function holdSpan(ms) {
    var s = Math.floor(ms / 1000);
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h ' + m + 'm';
    return Math.max(1, m) + 'm';
  }

  function clock(ms, offsetHours) {
    var d = new Date(ms + (offsetHours || 0) * 3600000);
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
  }

  function hhmm(ms, offsetHours) {
    var d = new Date(ms + (offsetHours || 0) * 3600000);
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
  }

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function dayLabel(ms, offsetHours) {
    var d = new Date(ms + (offsetHours || 0) * 3600000);
    return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()];
  }

  /** «Aug 31, 2026, 4:38 PM» — как в title у возраста токена */
  function stamp(ms) {
    var d = new Date(ms);
    var h = d.getHours();
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12;
    if (h === 0) h = 12;
    var m = d.getMinutes();
    return MONTHS[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear() +
      ', ' + h + ':' + (m < 10 ? '0' + m : m) + ' ' + ampm;
  }

  function colorFor(v) {
    if (v == null || !isFinite(v)) return GRAY;
    return v >= 0 ? GREEN : RED;
  }

  /**
   * Пара «стрелка + процент» точно в разметке fomo.
   * size: 'sm' (в блоках 12px) | 'md' (в шапке 14px)
   */
  function deltaHtml(v, size) {
    var big = size === 'md';
    var arrowSize = big ? '8px' : '6px';
    var textSize = big ? '14px' : '12px';
    var col = colorFor(v);
    var arrow = (v == null || !isFinite(v) || v >= 0) ? '\u25B2' : '\u25BC';
    var label = (v == null || !isFinite(v)) ? '--' : pct(v);
    return '<div style="color: ' + col + '; font-weight: 400; font-size: ' + arrowSize + ';">' + arrow + '</div>' +
      '<div style="font-size: ' + textSize + '; font-weight: 500; color: ' + col + ';">' + label + '</div>';
  }

  global.F = {
    GREEN: GREEN, RED: RED, GRAY: GRAY,
    commas: commas, money: money, moneyFull: moneyFull, count: count,
    tokens: tokens, pct: pct, priceHtml: priceHtml, priceText: priceText,
    age: age, ageLong: ageLong, ago: ago, holdSpan: holdSpan,
    clock: clock, hhmm: hhmm, dayLabel: dayLabel, stamp: stamp,
    colorFor: colorFor, deltaHtml: deltaHtml
  };
})(typeof window !== 'undefined' ? window : this);
