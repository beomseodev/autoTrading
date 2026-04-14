const positionsContainer = document.getElementById("positions");
const positionTemplate = document.getElementById("position-template");
const addPositionButton = document.getElementById("add-position-btn");
const form = document.getElementById("backtest-form");
const statusText = document.getElementById("status-text");
const periodModeSelect = document.getElementById("period-mode");
const periodRangeFields = document.getElementById("period-range-fields");
const periodLookbackFields = document.getElementById("period-lookback-fields");
const rebalanceModeSelect = document.getElementById("rebalance-mode");
const frequencyField = document.getElementById("frequency-field");
const rsiFields = document.getElementById("rsi-fields");
const rsiScopeFields = document.getElementById("rsi-scope-fields");
const rsiSignalScopeSelect = document.getElementById("rsi-signal-scope");
const rsiTriggerField = document.getElementById("rsi-trigger-field");
const rsiTriggerTickerSelect = document.getElementById("rsi-trigger-ticker");
const summaryGrid = document.getElementById("summary-grid");
const holdingsBody = document.getElementById("holdings-body");
const eventsBody = document.getElementById("events-body");
const canvas = document.getElementById("equity-chart");
const chartRange = document.getElementById("chart-range");
const chartNote = document.getElementById("chart-note");
const chartModeButtons = [...document.querySelectorAll("[data-chart-mode]")];
const canvasContext = canvas.getContext("2d");
let activeChartMode = "nominal";
let chartSeries = { nominal: [], real: [] };
let currentInflationRatePct = 3;

function getTickerOptions() {
  return [...positionsContainer.querySelectorAll('.position-row input[name="ticker"]')]
    .map((input) => input.value.trim().toUpperCase())
    .filter((ticker, index, allTickers) => ticker && allTickers.indexOf(ticker) === index);
}

function syncRsiTriggerTickerOptions() {
  const tickers = getTickerOptions();
  const previousValue = rsiTriggerTickerSelect.value;

  rsiTriggerTickerSelect.innerHTML = "";
  tickers.forEach((ticker) => {
    const option = document.createElement("option");
    option.value = ticker;
    option.textContent = ticker;
    rsiTriggerTickerSelect.appendChild(option);
  });

  if (!tickers.length) {
    return;
  }

  rsiTriggerTickerSelect.value = tickers.includes(previousValue) ? previousValue : tickers[0];
}

function togglePeriodFields() {
  const isRange = periodModeSelect.value === "range";
  periodRangeFields.classList.toggle("hidden", !isRange);
  periodLookbackFields.classList.toggle("hidden", isRange);
}

function toggleRsiScopeFields() {
  const showTriggerTicker =
    rebalanceModeSelect.value === "rsi" && rsiSignalScopeSelect.value === "single";

  rsiTriggerField.classList.toggle("hidden", !showTriggerTicker);
  rsiTriggerTickerSelect.disabled = !showTriggerTicker || !rsiTriggerTickerSelect.options.length;
}

function toggleRebalanceFields() {
  const isCalendar = rebalanceModeSelect.value === "calendar";
  frequencyField.classList.toggle("hidden", !isCalendar);
  rsiFields.classList.toggle("hidden", isCalendar);
  rsiScopeFields.classList.toggle("hidden", isCalendar);
  toggleRsiScopeFields();
}

function currency(value) {
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function percent(value) {
  return `${value.toFixed(2)}%`;
}

function renderSummary(summary) {
  const items = [
    ["Initial capital", currency(summary.initialCapital)],
    ["Monthly contribution", currency(summary.monthlyContribution)],
    ["Total contributed", currency(summary.totalContributed)],
    ["Inflation rate", percent(summary.inflationRatePct)],
    ["Deployed capital", currency(summary.deployedCapital)],
    ["Final value", currency(summary.finalValue)],
    ["Real final value", currency(summary.realFinalValue)],
    ["Total return", percent(summary.totalReturnPct)],
    ["Real total return", percent(summary.realTotalReturnPct)],
    ["XIRR", summary.xirrPct === null ? "-" : percent(summary.xirrPct)],
    ["MDD", percent(summary.mddPct)],
  ];
  if (summary.cagrPct !== null) {
    items.splice(9, 0, ["CAGR", percent(summary.cagrPct)]);
  }

  summaryGrid.classList.remove("empty-state");
  summaryGrid.innerHTML = items
    .map(
      ([label, value]) => `
        <article class="metric-card">
          <span>${label}</span>
          <strong>${value}</strong>
        </article>
      `,
    )
    .join("");
}

function renderHoldings(items) {
  if (!items.length) {
    holdingsBody.innerHTML = '<tr><td colspan="4" class="empty-row">No holdings data available.</td></tr>';
    return;
  }

  holdingsBody.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td>${item.ticker}</td>
          <td>${Number(item.shares).toLocaleString("ko-KR", { maximumFractionDigits: 6 })}</td>
          <td>${currency(item.value)}</td>
          <td>${percent(item.weight)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderEvents(events) {
  if (!events.length) {
    eventsBody.innerHTML = '<tr><td colspan="3" class="empty-row">No rebalance events were triggered.</td></tr>';
    return;
  }

  eventsBody.innerHTML = events
    .map(
      (event) => `
        <tr>
          <td>${event.date}</td>
          <td>${event.reason}</td>
          <td>${percent(event.turnoverPct)}</td>
        </tr>
      `,
    )
    .join("");
}

function drawChart(points) {
  const width = canvas.width;
  const height = canvas.height;
  const padding = { top: 24, right: 20, bottom: 36, left: 58 };

  canvasContext.clearRect(0, 0, width, height);
  canvasContext.fillStyle = "#fffdf8";
  canvasContext.fillRect(0, 0, width, height);

  if (!points.length) {
    canvasContext.fillStyle = "#6c6d72";
    canvasContext.font = "16px Segoe UI";
    canvasContext.fillText("No chart data available.", 36, 60);
    chartRange.textContent = "";
    return;
  }

  const values = points.map((point) => point.value);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const valueSpan = Math.max(maxValue - minValue, 1);

  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;

  canvasContext.strokeStyle = "rgba(29, 36, 51, 0.12)";
  canvasContext.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const y = padding.top + (innerHeight / 4) * step;
    canvasContext.beginPath();
    canvasContext.moveTo(padding.left, y);
    canvasContext.lineTo(width - padding.right, y);
    canvasContext.stroke();
  }

  canvasContext.fillStyle = "#6c6d72";
  canvasContext.font = "12px Segoe UI";
  for (let step = 0; step <= 4; step += 1) {
    const y = padding.top + (innerHeight / 4) * step;
    const labelValue = maxValue - (valueSpan / 4) * step;
    canvasContext.fillText(currency(labelValue), 8, y + 4);
  }

  const pathPoints = points.map((point, index) => {
    const x = padding.left + (innerWidth * index) / Math.max(points.length - 1, 1);
    const y = padding.top + ((maxValue - point.value) / valueSpan) * innerHeight;
    return { x, y };
  });

  const gradient = canvasContext.createLinearGradient(0, padding.top, 0, height - padding.bottom);
  gradient.addColorStop(0, "rgba(14, 143, 112, 0.30)");
  gradient.addColorStop(1, "rgba(14, 143, 112, 0.02)");

  canvasContext.beginPath();
  canvasContext.moveTo(pathPoints[0].x, height - padding.bottom);
  pathPoints.forEach((point) => canvasContext.lineTo(point.x, point.y));
  canvasContext.lineTo(pathPoints[pathPoints.length - 1].x, height - padding.bottom);
  canvasContext.closePath();
  canvasContext.fillStyle = gradient;
  canvasContext.fill();

  canvasContext.beginPath();
  pathPoints.forEach((point, index) => {
    if (index === 0) {
      canvasContext.moveTo(point.x, point.y);
    } else {
      canvasContext.lineTo(point.x, point.y);
    }
  });
  canvasContext.strokeStyle = "#0e8f70";
  canvasContext.lineWidth = 3;
  canvasContext.stroke();

  chartRange.textContent = `${points[0].date} ~ ${points[points.length - 1].date}`;
}

function updateChartModeButtons() {
  chartModeButtons.forEach((button) => {
    const isActive = button.dataset.chartMode === activeChartMode;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function renderActiveChart() {
  const points = chartSeries[activeChartMode] || [];
  drawChart(points);
  chartNote.textContent =
    activeChartMode === "real"
      ? `${currentInflationRatePct.toFixed(1)}% annual inflation, start-date purchasing power.`
      : "Nominal portfolio value.";
}

function setChartMode(mode) {
  activeChartMode = mode;
  updateChartModeButtons();
  renderActiveChart();
}

function buildPayload() {
  const formData = new FormData(form);
  const positions = [...positionsContainer.querySelectorAll(".position-row")].map((row) => ({
    ticker: row.querySelector('input[name="ticker"]').value.trim().toUpperCase(),
    targetWeight: Number(row.querySelector('input[name="targetWeight"]').value),
  }));

  const period =
    periodModeSelect.value === "range"
      ? {
          startDate: formData.get("startDate"),
          endDate: formData.get("endDate"),
        }
      : {
          lookbackYears: Number(formData.get("lookbackYears")),
        };

  const rebalance =
    rebalanceModeSelect.value === "calendar"
      ? {
          mode: "calendar",
          frequency: formData.get("frequency"),
        }
      : {
          mode: "rsi",
          rsiPeriod: Number(formData.get("rsiPeriod")),
          lower: Number(formData.get("lower")),
          upper: Number(formData.get("upper")),
          rsiSignalScope: formData.get("rsiSignalScope"),
        };

  if (rebalance.mode === "rsi" && rebalance.rsiSignalScope === "single") {
    rebalance.rsiTriggerTicker = formData.get("rsiTriggerTicker")?.toString().trim().toUpperCase();
  }

  return {
    positions,
    initialCapital: Number(formData.get("initialCapital")),
    monthlyContribution: Number(formData.get("monthlyContribution")),
    period,
    rebalance,
    execution: {
      fractionalShares: formData.get("fractionalShares") === "on",
      dividendReinvestment: formData.get("dividendReinvestment") === "on",
      feeRate: Number(formData.get("feeRate")),
      slippageRate: Number(formData.get("slippageRate")),
    },
  };
}

async function runBacktest(event) {
  event.preventDefault();

  const payload = buildPayload();
  statusText.textContent = "Running backtest...";

  try {
    const response = await fetch("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "Backtest request failed.");
    }

    renderSummary(body.summary);
    renderHoldings(body.holdingsSnapshot);
    renderEvents(body.rebalanceEvents);
    currentInflationRatePct = Number(body.summary.inflationRatePct);
    chartSeries = {
      nominal: body.equityCurve,
      real: body.realEquityCurve,
    };
    renderActiveChart();
    statusText.textContent = `Completed: ${body.summary.rebalanceCount} rebalance events`;
  } catch (error) {
    statusText.textContent = error.message;
  }
}

function wirePositionRow(row) {
  row.querySelector(".remove-position-btn").addEventListener("click", () => {
    if (positionsContainer.children.length === 1) {
      statusText.textContent = "At least one ticker is required.";
      return;
    }

    row.remove();
    syncRsiTriggerTickerOptions();
    toggleRsiScopeFields();
  });

  row.querySelector('input[name="ticker"]').addEventListener("input", () => {
    syncRsiTriggerTickerOptions();
    toggleRsiScopeFields();
  });
}

function addPositionRow(ticker = "", targetWeight = "") {
  const fragment = positionTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".position-row");
  row.querySelector('input[name="ticker"]').value = ticker;
  row.querySelector('input[name="targetWeight"]').value = targetWeight;

  wirePositionRow(row);
  positionsContainer.appendChild(fragment);
  syncRsiTriggerTickerOptions();
  toggleRsiScopeFields();
}

addPositionButton.addEventListener("click", () => addPositionRow("", ""));
periodModeSelect.addEventListener("change", togglePeriodFields);
rebalanceModeSelect.addEventListener("change", toggleRebalanceFields);
rsiSignalScopeSelect.addEventListener("change", toggleRsiScopeFields);
chartModeButtons.forEach((button) => {
  button.addEventListener("click", () => setChartMode(button.dataset.chartMode));
});
form.addEventListener("submit", runBacktest);

addPositionRow("AAPL", 40);
addPositionRow("MSFT", 35);
addPositionRow("QQQ", 25);
togglePeriodFields();
syncRsiTriggerTickerOptions();
toggleRebalanceFields();
setChartMode("nominal");
