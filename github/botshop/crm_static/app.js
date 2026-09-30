(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor("#07111f");
    tg.setBackgroundColor("#07111f");
  }

  const elements = {
    adminName: document.getElementById("adminName"),
    generatedAt: document.getElementById("generatedAt"),
    loadingState: document.getElementById("loadingState"),
    errorState: document.getElementById("errorState"),
    errorTitle: document.getElementById("errorTitle"),
    errorText: document.getElementById("errorText"),
    dashboard: document.getElementById("dashboard"),
    periodCards: document.getElementById("periodCards"),
    profitChartSummary: document.getElementById("profitChartSummary"),
    profitChart: document.getElementById("profitChart"),
    summaryGrid: document.getElementById("summaryGrid"),
    orderHighlights: document.getElementById("orderHighlights"),
    statusTable: document.getElementById("statusTable"),
    providerTables: document.getElementById("providerTables"),
    franchiseGrid: document.getElementById("franchiseGrid"),
    recentDeliveries: document.getElementById("recentDeliveries"),
  };

  function formatMoney(value) {
    return `${Number(value || 0).toFixed(2)} $`;
  }

  function formatSignedMoney(value) {
    const numericValue = Number(value || 0);
    const sign = numericValue > 0 ? "+" : numericValue < 0 ? "-" : "";
    return `${sign}${Math.abs(numericValue).toFixed(2)} $`;
  }

  function formatPercent(value) {
    return `${Number(value || 0).toFixed(2)}%`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDateLabel(dateValue, options) {
    const parsedDate = new Date(`${dateValue || ""}T00:00:00`);
    if (Number.isNaN(parsedDate.getTime())) {
      return dateValue || "-";
    }
    return new Intl.DateTimeFormat("ru-RU", options).format(parsedDate);
  }

  function buildLinePath(points) {
    if (!points.length) {
      return "";
    }
    return points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(" ");
  }

  function buildAreaPath(points, baselineY) {
    if (!points.length) {
      return "";
    }
    const linePath = buildLinePath(points);
    const firstPoint = points[0];
    const lastPoint = points[points.length - 1];
    return `${linePath} L ${lastPoint.x.toFixed(2)} ${baselineY.toFixed(2)} L ${firstPoint.x.toFixed(2)} ${baselineY.toFixed(2)} Z`;
  }

  function renderCards(container, cards) {
    container.innerHTML = cards
      .map(
        (card) => `
          <article class="${card.className || "summary-card"}" ${card.tone ? `data-tone="${card.tone}"` : ""}>
            <span>${escapeHtml(card.label)}</span>
            <strong>${escapeHtml(card.value)}</strong>
            ${card.note ? `<small>${escapeHtml(card.note)}</small>` : ""}
          </article>
        `
      )
      .join("");
  }

  function renderTable(title, rows, headerLeft, headerRight) {
    const body = rows.length
      ? rows
          .map(
            (row) => `
              <tr>
                <td>${escapeHtml(row.left)}</td>
                <td>${escapeHtml(row.right)}</td>
              </tr>
            `
          )
          .join("")
      : `
          <tr>
            <td colspan="2">Нет данных</td>
          </tr>
        `;

    return `
      <div class="table-wrap">
        <table>
          <caption class="hidden">${escapeHtml(title)}</caption>
          <thead>
            <tr>
              <th>${escapeHtml(headerLeft)}</th>
              <th>${escapeHtml(headerRight)}</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    `;
  }

  function renderProviderTables(dashboard) {
    const orderRows = (dashboard.orders.payment_breakdown || []).map((item) => ({
      left: item.provider,
      right: `${item.count}`,
    }));
    const topupRows = (dashboard.topups.payment_breakdown || []).map((item) => ({
      left: item.provider,
      right: `${item.count}`,
    }));

    elements.providerTables.innerHTML = `
      <div>
        <p class="label-muted">Оплаты заказов</p>
        ${renderTable("Заказы", orderRows, "Провайдер", "Доставок")}
      </div>
      <div>
        <p class="label-muted">Пополнения</p>
        ${renderTable("Пополнения", topupRows, "Провайдер", "Оплат")}
      </div>
    `;
  }

  function renderRecentDeliveries(items) {
    if (!items.length) {
      elements.recentDeliveries.innerHTML = `
        <article class="delivery-item">
          <strong>Пока нет доставленных заказов</strong>
          <p>Как только появятся выдачи, они отобразятся здесь.</p>
        </article>
      `;
      return;
    }

    elements.recentDeliveries.innerHTML = items
      .map(
        (item) => `
          <article class="delivery-item">
            <div class="delivery-head">
              <strong>#${escapeHtml(item.id)}</strong>
              <span class="pill ${item.is_partner_order ? "is-partner" : ""}">
                ${escapeHtml(item.is_partner_order ? "Franchise" : item.payment_provider || "unknown")}
              </span>
            </div>
            <p>Выручка: ${escapeHtml(formatMoney(item.revenue))}</p>
            <p>Валовая прибыль: ${escapeHtml(formatMoney(item.gross_profit))}</p>
            <div class="delivery-meta">
              <strong>Платформа: ${escapeHtml(formatMoney(item.platform_profit))}</strong>
              <span>${escapeHtml(item.updated_at || "-")}</span>
            </div>
          </article>
        `
      )
      .join("");
  }

  function renderProfitSummary(points) {
    const totalNet = points.reduce((sum, point) => sum + Number(point.net_profit || 0), 0);
    const totalRefunds = points.reduce((sum, point) => sum + Number(point.refund_amount || 0), 0);
    const bestDay = points.reduce(
      (best, point) => (best == null || Number(point.net_profit || 0) > Number(best.net_profit || 0) ? point : best),
      null
    );

    renderCards(elements.profitChartSummary, [
      {
        className: "summary-card chart-card",
        label: "Чистая прибыль",
        value: formatSignedMoney(totalNet),
        note: "Сумма за последние 14 дней",
      },
      {
        className: "summary-card chart-card",
        label: "Возвраты",
        value: formatMoney(totalRefunds),
        note: "Заказы и ручные возвраты",
      },
      {
        className: "summary-card chart-card",
        label: "Лучший день",
        value: bestDay ? formatSignedMoney(bestDay.net_profit) : formatMoney(0),
        note: bestDay ? formatDateLabel(bestDay.date, { day: "2-digit", month: "short" }) : "Нет данных",
      },
    ]);
  }

  function renderProfitChart(dashboard) {
    const points = (dashboard.profit_timeline || []).map((point) => ({
      ...point,
      platform_profit: Number(point.platform_profit || 0),
      refund_amount: Number(point.refund_amount || 0),
      net_profit: Number(point.net_profit || 0),
      delivered_orders: Number(point.delivered_orders || 0),
      refund_orders: Number(point.refund_orders || 0),
      manual_refund_amount: Number(point.manual_refund_amount || 0),
      manual_refund_count: Number(point.manual_refund_count || 0),
    }));

    renderProfitSummary(points);

    if (!points.length) {
      elements.profitChart.innerHTML = `
        <div class="chart-empty">
          <strong>Пока не из чего строить график</strong>
          <p>Когда появятся выдачи и возвраты, тут появится динамика чистой прибыли.</p>
        </div>
      `;
      return;
    }

    const allValues = points.flatMap((point) => [point.platform_profit, point.net_profit, 0]);
    const maxValue = Math.max(...allValues, 1);
    const minValue = Math.min(...allValues, 0);
    const range = maxValue - minValue || 1;
    const baselineY = 100 - ((0 - minValue) / range) * 100;
    const plotPoints = points.map((point, index) => ({
      ...point,
      x: points.length === 1 ? 50 : (index * 100) / (points.length - 1),
      yNet: 100 - ((point.net_profit - minValue) / range) * 100,
      yPlatform: 100 - ((point.platform_profit - minValue) / range) * 100,
    }));
    const platformPath = buildLinePath(plotPoints.map((point) => ({ x: point.x, y: point.yPlatform })));
    const netPath = buildLinePath(plotPoints.map((point) => ({ x: point.x, y: point.yNet })));
    const areaPath = buildAreaPath(plotPoints.map((point) => ({ x: point.x, y: point.yNet })), baselineY);
    const tickValues = Array.from({ length: 4 }, (_, index) => maxValue - (range / 3) * index);
    const axisIndexes = Array.from(new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]));

    elements.profitChart.innerHTML = `
      <div class="chart-layout">
        <div class="chart-stage">
          <div class="chart-scale">
            ${tickValues
              .map((value) => {
                const top = 100 - ((value - minValue) / range) * 100;
                return `<span class="chart-scale-label" style="top:${top.toFixed(2)}%">${escapeHtml(formatMoney(value))}</span>`;
              })
              .join("")}
          </div>
          <div class="chart-canvas" data-chart-canvas>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="График чистой прибыли за 14 дней">
              <defs>
                <linearGradient id="netAreaGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="#5fe3a1" stop-opacity="0.34"></stop>
                  <stop offset="100%" stop-color="#5fe3a1" stop-opacity="0.02"></stop>
                </linearGradient>
              </defs>
              ${tickValues
                .map((value) => {
                  const y = 100 - ((value - minValue) / range) * 100;
                  return `<line class="chart-grid-line" x1="0" y1="${y.toFixed(2)}" x2="100" y2="${y.toFixed(2)}"></line>`;
                })
                .join("")}
              <line class="chart-zero-line" x1="0" y1="${baselineY.toFixed(2)}" x2="100" y2="${baselineY.toFixed(2)}"></line>
              <path class="chart-area" d="${areaPath}"></path>
              <path class="chart-line platform" d="${platformPath}"></path>
              <path class="chart-line net" d="${netPath}"></path>
              ${plotPoints
                .map(
                  (point, index) => `
                    <circle
                      class="chart-point ${point.net_profit < 0 ? "is-negative" : ""}"
                      cx="${point.x.toFixed(2)}"
                      cy="${point.yNet.toFixed(2)}"
                      r="1.55"
                      data-point-index="${index}"
                    ></circle>
                  `
                )
                .join("")}
              <line class="chart-active-line" x1="0" y1="0" x2="0" y2="100" data-active-line></line>
              <circle class="chart-active-dot" cx="0" cy="0" r="1.95" data-active-dot></circle>
            </svg>
            <div class="chart-tooltip" data-chart-tooltip></div>
          </div>
        </div>
        <div class="chart-axis">
          ${axisIndexes
            .map((index) => {
              const point = plotPoints[index];
              return `
                <span class="chart-axis-label" style="left:${point.x.toFixed(2)}%">
                  ${escapeHtml(formatDateLabel(point.date, { day: "2-digit", month: "short" }))}
                </span>
              `;
            })
            .join("")}
        </div>
        <div class="chart-legend">
          <span class="chart-legend-item"><i class="chart-legend-swatch"></i>Чистая прибыль</span>
          <span class="chart-legend-item"><i class="chart-legend-swatch is-platform"></i>Платформа до возвратов</span>
        </div>
      </div>
    `;

    const canvas = elements.profitChart.querySelector("[data-chart-canvas]");
    const tooltip = elements.profitChart.querySelector("[data-chart-tooltip]");
    const activeLine = elements.profitChart.querySelector("[data-active-line]");
    const activeDot = elements.profitChart.querySelector("[data-active-dot]");
    const pointNodes = Array.from(elements.profitChart.querySelectorAll("[data-point-index]"));
    let activeIndex = plotPoints.length - 1;

    function updateActivePoint(index) {
      const safeIndex = Math.max(0, Math.min(plotPoints.length - 1, index));
      const point = plotPoints[safeIndex];
      activeIndex = safeIndex;
      activeLine.setAttribute("x1", point.x.toFixed(2));
      activeLine.setAttribute("x2", point.x.toFixed(2));
      activeDot.setAttribute("cx", point.x.toFixed(2));
      activeDot.setAttribute("cy", point.yNet.toFixed(2));
      activeDot.classList.toggle("is-negative", point.net_profit < 0);

      pointNodes.forEach((node, nodeIndex) => {
        node.classList.toggle("is-active", nodeIndex === safeIndex);
      });

      tooltip.innerHTML = `
        <strong>${escapeHtml(formatDateLabel(point.date, { day: "2-digit", month: "long" }))}</strong>
        <span>Чистая прибыль: ${escapeHtml(formatSignedMoney(point.net_profit))}</span>
        <span>Платформа до возвратов: ${escapeHtml(formatMoney(point.platform_profit))}</span>
        <span>Возвраты: ${escapeHtml(formatMoney(point.refund_amount))}</span>
        <span>Ручные возвраты: ${escapeHtml(formatMoney(point.manual_refund_amount))} | ${escapeHtml(String(point.manual_refund_count))} шт.</span>
        <span>Доставок: ${escapeHtml(String(point.delivered_orders))} | Возвратов по заказам: ${escapeHtml(String(point.refund_orders))}</span>
      `;
      tooltip.classList.add("is-visible");
      tooltip.style.left = `${point.x.toFixed(2)}%`;
      tooltip.style.bottom = `${Math.max(8, 100 - point.yNet + 6).toFixed(2)}%`;
      if (point.x < 24) {
        tooltip.dataset.align = "start";
      } else if (point.x > 76) {
        tooltip.dataset.align = "end";
      } else {
        tooltip.dataset.align = "center";
      }
    }

    function updateFromPointer(clientX) {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width) {
        return;
      }
      const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      const nextIndex = Math.round(ratio * (plotPoints.length - 1));
      if (nextIndex !== activeIndex) {
        updateActivePoint(nextIndex);
      }
    }

    canvas.addEventListener("pointermove", (event) => updateFromPointer(event.clientX));
    canvas.addEventListener("pointerdown", (event) => updateFromPointer(event.clientX));
    canvas.addEventListener("pointerleave", () => updateActivePoint(plotPoints.length - 1));

    updateActivePoint(activeIndex);
  }

  function renderDashboard(payload) {
    const { admin, dashboard } = payload;
    elements.adminName.textContent =
      [admin.first_name, admin.last_name].filter(Boolean).join(" ") || admin.username || `#${admin.id}`;
    elements.generatedAt.textContent = dashboard.generated_at || "-";

    renderCards(elements.periodCards, [
      {
        className: "metric-card",
        tone: "profit",
        label: "Чистая прибыль сегодня",
        value: formatSignedMoney(dashboard.today.net_profit),
        note: `Платформа: ${formatMoney(dashboard.today.platform_profit)}`,
      },
      {
        className: "metric-card",
        tone: "profit",
        label: "Чистая прибыль 7 дней",
        value: formatSignedMoney(dashboard.week.net_profit),
        note: `Возвраты: ${formatMoney(dashboard.week.refund_amount)}`,
      },
      {
        className: "metric-card",
        tone: "revenue",
        label: "Выручка сегодня",
        value: formatMoney(dashboard.today.delivered_revenue),
        note: `Создано заказов: ${dashboard.today.created_orders}`,
      },
      {
        className: "metric-card",
        tone: "revenue",
        label: "Выручка 7 дней",
        value: formatMoney(dashboard.week.delivered_revenue),
        note: `Доставок: ${dashboard.week.delivered_orders}`,
      },
      {
        className: "metric-card",
        tone: "users",
        label: "Новые пользователи",
        value: `${dashboard.today.new_users}`,
        note: `За 7 дней: ${dashboard.week.new_users}`,
      },
      {
        className: "metric-card",
        tone: "users",
        label: "Пополнения 7 дней",
        value: formatMoney(dashboard.week.topups_amount),
        note: `Оплат: ${dashboard.week.paid_topups}`,
      },
    ]);

    renderProfitChart(dashboard);

    renderCards(elements.summaryGrid, [
      { label: "Всего пользователей", value: `${dashboard.summary.total_users}`, note: `Покупателей: ${dashboard.summary.buyers}` },
      { label: "Конверсия в покупку", value: formatPercent(dashboard.summary.conversion_rate), note: `Повторные: ${dashboard.summary.repeat_buyers}` },
      { label: "Повторные покупки", value: formatPercent(dashboard.summary.repeat_rate), note: `Средний чек: ${formatMoney(dashboard.summary.average_check)}` },
      { label: "Баланс пользователей", value: formatMoney(dashboard.summary.balances_total), note: "Суммарно на аккаунтах" },
      { label: "Всего пополнений", value: formatMoney(dashboard.summary.topups_total), note: `Оплачено: ${formatMoney(dashboard.summary.paid_topups_amount)}` },
      { label: "Всего покупок", value: formatMoney(dashboard.summary.purchases_total), note: `Рефералов: ${dashboard.summary.referred_users}` },
      { label: "Реферальные выплаты", value: formatMoney(dashboard.summary.referral_earnings_total), note: "Накоплено по базе" },
      {
        label: "Возвраты сегодня",
        value: formatMoney(dashboard.today.refund_amount),
        note: `Ручные: ${formatMoney(dashboard.today.manual_refund_amount)} | Заказов: ${dashboard.today.refund_orders}`,
      },
      { label: "Чистая прибыль базы", value: formatSignedMoney(dashboard.summary.net_profit_total), note: `Все возвраты: ${formatMoney(dashboard.summary.refund_amount_total)}` },
    ]);

    renderCards(elements.orderHighlights, [
      { label: "Всего заказов", value: `${dashboard.orders.total_orders}`, note: `Доставлено: ${dashboard.orders.delivered_orders}` },
      { label: "Общая выручка", value: formatMoney(dashboard.orders.delivered_revenue), note: `Со всеми статусами: ${formatMoney(dashboard.orders.gross_revenue)}` },
      {
        label: "Чистая прибыль",
        value: formatSignedMoney(dashboard.orders.net_profit),
        note: `Валовая: ${formatMoney(dashboard.orders.gross_profit)} | Франчайзи: ${formatMoney(dashboard.orders.partner_profit)}`,
      },
      {
        label: "Возвраты",
        value: formatMoney(dashboard.orders.refund_amount),
        note: `Заказы: ${dashboard.orders.credited_orders} | Ручные: ${dashboard.orders.manual_refund_count}`,
      },
    ]);

    elements.statusTable.innerHTML = renderTable(
      "Статусы",
      (dashboard.orders.status_breakdown || []).map((item) => ({
        left: item.status,
        right: `${item.count}`,
      })),
      "Статус",
      "Количество"
    );

    renderProviderTables(dashboard);

    renderCards(elements.franchiseGrid, [
      { label: "Всего франшиз", value: `${dashboard.franchises.total_franchises}`, note: `Активных: ${dashboard.franchises.active_franchises}` },
      { label: "Пользователи сети", value: `${dashboard.franchises.total_users}`, note: `Покупатели: ${dashboard.franchises.total_buyers}` },
      { label: "Оборот сети", value: formatMoney(dashboard.franchises.total_revenue), note: `Прибыль сети: ${formatMoney(dashboard.franchises.total_profit)}` },
      { label: "Средняя прибыль", value: formatMoney(dashboard.franchises.average_profit_per_franchise), note: "На одну франшизу" },
    ]);

    renderRecentDeliveries(dashboard.recent_deliveries || []);
  }

  function showError(title, message) {
    elements.loadingState.classList.add("hidden");
    elements.dashboard.classList.add("hidden");
    elements.errorTitle.textContent = title;
    elements.errorText.textContent = message;
    elements.errorState.classList.remove("hidden");
  }

  async function init() {
    try {
      const headers = tg && tg.initData ? { "X-Telegram-Init-Data": tg.initData } : {};
      const response = await fetch("./api/bootstrap", {
        method: "GET",
        headers,
        credentials: "same-origin",
      });

      if (!response.ok) {
        let errorMessage = "Не удалось загрузить CRM.";
        try {
          const errorPayload = await response.json();
          errorMessage = errorPayload.error || errorMessage;
        } catch (error) {
          errorMessage = response.status === 403 ? "Этот mini app доступен только ADMIN_ID." : errorMessage;
        }
        throw new Error(errorMessage);
      }

      const payload = await response.json();
      renderDashboard(payload);
      elements.loadingState.classList.add("hidden");
      elements.errorState.classList.add("hidden");
      elements.dashboard.classList.remove("hidden");
    } catch (error) {
      showError("Нет доступа", error instanceof Error ? error.message : "Не удалось открыть CRM.");
    }
  }

  init();
})();
