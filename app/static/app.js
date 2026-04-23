/**
 * US Portfolio Backtester — 프론트 로직
 * 수정: 2026-04-23 — 포트폴리오 비교 모드, 다중 시리즈 차트(교집합 거래일), 비교 요약 표 추가
 * 수정: 2026-04-23 — 실행 모드를 셀렉트 대신 탭 버튼으로 바꿔 비교 기능이 한눈에 보이게 함
 */

const positionsContainer = document.getElementById("positions");
const positionTemplate = document.getElementById("position-template");
const scenarioCardTemplate = document.getElementById("scenario-card-template");
const addPositionButton = document.getElementById("add-position-btn");
const addScenarioButton = document.getElementById("add-scenario-btn");
const scenariosContainer = document.getElementById("scenarios-container");
const form = document.getElementById("backtest-form");
const statusText = document.getElementById("status-text");
const runModeButtons = [...document.querySelectorAll(".run-mode-btn")];
const runModeHint = document.getElementById("run-mode-hint");
const setupHint = document.getElementById("setup-hint");
const submitButton = document.getElementById("submit-btn");
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
const compareSummaryWrap = document.getElementById("compare-summary-wrap");
const compareSummaryHead = document.getElementById("compare-summary-head");
const compareSummaryBody = document.getElementById("compare-summary-body");
const holdingsBody = document.getElementById("holdings-body");
const eventsBody = document.getElementById("events-body");
const canvas = document.getElementById("equity-chart");
const chartRange = document.getElementById("chart-range");
const chartNote = document.getElementById("chart-note");
const chartLegend = document.getElementById("chart-legend");
const chartModeButtons = [...document.querySelectorAll("[data-chart-mode]")];
const scenarioDetailLabel = document.getElementById("scenario-detail-label");
const scenarioDetailSelect = document.getElementById("scenario-detail-select");
const resultsHint = document.getElementById("results-hint");
const canvasContext = canvas.getContext("2d");

const COMPARE_MAX_SCENARIOS = 8;
const COMPARE_MIN_SCENARIOS = 2;
/** 비교 차트 시리즈 색상 (최대 8개) */
const SERIES_COLORS = ["#0e8f70", "#6b4c9a", "#c45c26", "#1d6fb8", "#b83280", "#5c6f5c", "#c49a00", "#2a6b7c"];

let activeChartMode = "nominal";
/** 단일 모드용: 서버가 준 nominal/real 곡선 */
let chartSeries = { nominal: [], real: [] };
let currentInflationRatePct = 3;
/** 'single' | 'compare' — 결과 패널 표시 방식 */
let resultViewMode = "single";
/** 비교 API 응답 runs 배열 캐시 (차트 모드 전환 시 재계산) */
let lastCompareRuns = [];

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

function getActiveRunMode() {
  const active = runModeButtons.find((btn) => btn.classList.contains("active"));
  return active?.dataset.runMode === "compare" ? "compare" : "single";
}

function isCompareMode() {
  return getActiveRunMode() === "compare";
}

/** 상단 탭: 단일 ↔ 비교 */
function setRunMode(mode) {
  if (mode !== "single" && mode !== "compare") {
    return;
  }
  runModeButtons.forEach((btn) => {
    const isActive = btn.dataset.runMode === mode;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
  });
  applyRunModeUi();
  syncChromeAfterRunModeChange();
}

/** FastAPI 검증 오류 등 detail 필드 정규화 */
function formatApiDetail(detail) {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => (typeof item?.msg === "string" ? item.msg : JSON.stringify(item))).join("; ");
  }
  return "Request failed.";
}

/** 단일/비교 폼 영역 표시 전환 */
function applyRunModeUi() {
  const compare = isCompareMode();
  document.querySelectorAll(".single-only").forEach((el) => el.classList.toggle("hidden", compare));
  document.querySelectorAll(".compare-only").forEach((el) => el.classList.toggle("hidden", !compare));
  setupHint.textContent = compare
    ? "각 시나리오 카드에서 비중 합계 100%를 맞추세요. 공통 기간·자금은 아래에서 설정합니다."
    : "Target weights must add up to 100%.";
  if (runModeHint) {
    runModeHint.textContent = compare
      ? "시나리오 카드(최소 2개)마다 다른 티커·비중·리밸런스를 넣고, 아래 공통 기간·자본을 맞춘 뒤 \"비교 실행\"을 누르세요. (최대 8개)"
      : "한 가지 포트폴리오만 설정합니다. 여러 조합을 겹쳐 보고 싶으면 위에서 \"여러 포트폴리오 비교\"를 선택하세요.";
  }
  submitButton.textContent = compare ? "비교 실행" : "Run Backtest";

  if (compare && scenariosContainer.children.length < COMPARE_MIN_SCENARIOS) {
    while (scenariosContainer.children.length < COMPARE_MIN_SCENARIOS) {
      addScenarioCard();
    }
  }
}

/** 모드 전환 직후 결과 패널·차트 표시만 정리 (초기 로드에서도 호출 가능) */
function syncChromeAfterRunModeChange() {
  if (!isCompareMode()) {
    resultViewMode = "single";
    compareSummaryWrap.classList.add("hidden");
    scenarioDetailLabel.classList.add("hidden");
    summaryGrid.classList.remove("hidden");
    if (!summaryGrid.querySelector(".metric-card")) {
      summaryGrid.classList.add("empty-state");
      summaryGrid.textContent = "Run a backtest to populate the result cards.";
    }
    resultsHint.textContent = "Review summary metrics, the equity curve, and the executed rebalance events.";
    renderActiveChart();
  } else {
    resultViewMode = lastCompareRuns.length ? "compare" : "single";
    renderActiveChart();
  }
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
    ["누적 운영보수(TER)", currency(summary.totalExpensePaid ?? 0)],
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

  summaryGrid.classList.remove("empty-state", "hidden");
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

/** 교집합 거래일 기준으로 정렬된 시리즈 배열 생성 */
function buildIntersectionSeriesList(runs, mode) {
  const key = mode === "real" ? "realEquityCurve" : "equityCurve";
  const curves = runs.map((r) => r.result[key]);
  if (!curves.every((c) => c.length)) {
    return [];
  }

  let commonDates = new Set(curves[0].map((p) => p.date));
  for (let i = 1; i < curves.length; i += 1) {
    const nextSet = new Set(curves[i].map((p) => p.date));
    commonDates = new Set([...commonDates].filter((d) => nextSet.has(d)));
  }

  const sortedDates = [...commonDates].sort();
  if (!sortedDates.length) {
    return [];
  }

  return runs.map((run, idx) => {
    const curve = run.result[key];
    const byDate = new Map(curve.map((p) => [p.date, p.value]));
    const points = sortedDates.map((date) => ({ date, value: byDate.get(date) }));
    return {
      label: run.label,
      color: SERIES_COLORS[idx % SERIES_COLORS.length],
      points,
    };
  });
}

function renderChartLegend(seriesList) {
  if (seriesList.length <= 1) {
    chartLegend.classList.add("hidden");
    chartLegend.innerHTML = "";
    chartLegend.setAttribute("aria-hidden", "true");
    return;
  }
  chartLegend.classList.remove("hidden");
  chartLegend.setAttribute("aria-hidden", "false");
  chartLegend.innerHTML = seriesList
    .map(
      (s) => `
      <span class="chart-legend-item">
        <span class="chart-legend-swatch" style="background:${s.color}"></span>
        ${escapeHtml(s.label)}
      </span>
    `,
    )
    .join("");
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * 하나 이상의 자산 곡선을 동일 x 인덱스(이미 날짜 정렬·교집합)로 그립니다.
 * @param {Array<{ label: string, color: string, points: { date: string, value: number }[] }>} seriesList
 */
function drawEquitySeries(seriesList) {
  const width = canvas.width;
  const height = canvas.height;
  const padding = { top: 24, right: 20, bottom: 36, left: 58 };

  canvasContext.clearRect(0, 0, width, height);
  canvasContext.fillStyle = "#fffdf8";
  canvasContext.fillRect(0, 0, width, height);

  if (!seriesList.length) {
    canvasContext.fillStyle = "#6c6d72";
    canvasContext.font = "16px Segoe UI";
    canvasContext.fillText("No chart data available.", 36, 60);
    chartRange.textContent = "";
    return;
  }

  const firstSeries = seriesList[0];
  if (!firstSeries || !firstSeries.points.length) {
    canvasContext.fillStyle = "#6c6d72";
    canvasContext.font = "16px Segoe UI";
    canvasContext.fillText("No chart data available.", 36, 60);
    chartRange.textContent = "";
    return;
  }

  const allValues = seriesList.flatMap((s) => s.points.map((p) => p.value));
  const minValue = Math.min(...allValues);
  const maxValue = Math.max(...allValues);
  const valueSpan = Math.max(maxValue - minValue, 1);
  const pointCount = firstSeries.points.length;

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

  const xAt = (index) => padding.left + (innerWidth * index) / Math.max(pointCount - 1, 1);
  const yAt = (value) => padding.top + ((maxValue - value) / valueSpan) * innerHeight;

  if (seriesList.length === 1) {
    const points = firstSeries.points;
    const pathPoints = points.map((point, index) => ({
      x: xAt(index),
      y: yAt(point.value),
    }));
    const gradient = canvasContext.createLinearGradient(0, padding.top, 0, height - padding.bottom);
    gradient.addColorStop(0, "rgba(14, 143, 112, 0.30)");
    gradient.addColorStop(1, "rgba(14, 143, 112, 0.02)");
    canvasContext.beginPath();
    canvasContext.moveTo(pathPoints[0].x, height - padding.bottom);
    pathPoints.forEach((pt) => canvasContext.lineTo(pt.x, pt.y));
    canvasContext.lineTo(pathPoints[pathPoints.length - 1].x, height - padding.bottom);
    canvasContext.closePath();
    canvasContext.fillStyle = gradient;
    canvasContext.fill();
    canvasContext.beginPath();
    pathPoints.forEach((pt, index) => {
      if (index === 0) {
        canvasContext.moveTo(pt.x, pt.y);
      } else {
        canvasContext.lineTo(pt.x, pt.y);
      }
    });
    canvasContext.strokeStyle = firstSeries.color;
    canvasContext.lineWidth = 3;
    canvasContext.stroke();
  } else {
    seriesList.forEach((s) => {
      const pathPoints = s.points.map((point, index) => ({
        x: xAt(index),
        y: yAt(point.value),
      }));
      canvasContext.beginPath();
      pathPoints.forEach((pt, index) => {
        if (index === 0) {
          canvasContext.moveTo(pt.x, pt.y);
        } else {
          canvasContext.lineTo(pt.x, pt.y);
        }
      });
      canvasContext.strokeStyle = s.color;
      canvasContext.lineWidth = 2.5;
      canvasContext.stroke();
    });
  }

  const pts = firstSeries.points;
  chartRange.textContent = `${pts[0].date} ~ ${pts[pts.length - 1].date}`;
}

function updateChartModeButtons() {
  chartModeButtons.forEach((button) => {
    const isActive = button.dataset.chartMode === activeChartMode;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function renderActiveChart() {
  if (resultViewMode === "compare" && lastCompareRuns.length) {
    const seriesList = buildIntersectionSeriesList(lastCompareRuns, activeChartMode);
    if (!seriesList.length || !seriesList[0].points.length) {
      renderChartLegend([]);
      drawEquitySeries([]);
      chartNote.textContent =
        "공통 거래일(교집합)이 없어 차트를 그릴 수 없습니다. 기간·티커를 확인하세요.";
      return;
    }
    renderChartLegend(seriesList);
    drawEquitySeries(seriesList);
    const baseNote =
      activeChartMode === "real"
        ? `${currentInflationRatePct.toFixed(1)}% 물가상승 가정(연), 시작일 구매력 기준.`
        : "명목 포트폴리오 가치.";
    chartNote.textContent =
      seriesList.length > 1
        ? `${baseNote} 차트는 모든 시나리오에 공통으로 존재하는 거래일(교집합)만 표시합니다.`
        : baseNote;
    return;
  }

  const points = chartSeries[activeChartMode] || [];
  renderChartLegend([{ label: "", color: "#0e8f70", points }]);
  drawEquitySeries(points.length ? [{ label: "", color: "#0e8f70", points }] : []);
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

function buildPeriodFromFormData(formData) {
  return periodModeSelect.value === "range"
    ? {
        startDate: formData.get("startDate"),
        endDate: formData.get("endDate"),
      }
    : {
        lookbackYears: Number(formData.get("lookbackYears")),
      };
}

function buildExecutionFromFormData(formData) {
  return {
    fractionalShares: formData.get("fractionalShares") === "on",
    dividendReinvestment: formData.get("dividendReinvestment") === "on",
    dividendTaxRate: Number(formData.get("dividendTaxRate") ?? 0),
    feeRate: Number(formData.get("feeRate")),
    slippageRate: Number(formData.get("slippageRate")),
  };
}

function buildRebalanceFromMainForm(formData) {
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
  return rebalance;
}

function buildPayload() {
  const formData = new FormData(form);
  const positions = [...positionsContainer.querySelectorAll(".position-row")].map((row) => {
    const terInput = row.querySelector('input[name="annualExpenseRatio"]');
    return {
      ticker: row.querySelector('input[name="ticker"]').value.trim().toUpperCase(),
      targetWeight: Number(row.querySelector('input[name="targetWeight"]').value),
      annualExpenseRatio: terInput ? Number(terInput.value) || 0 : 0,
    };
  });

  return {
    positions,
    initialCapital: Number(formData.get("initialCapital")),
    monthlyContribution: Number(formData.get("monthlyContribution")),
    period: buildPeriodFromFormData(formData),
    rebalance: buildRebalanceFromMainForm(formData),
    execution: buildExecutionFromFormData(formData),
  };
}

function getTickerOptionsFromContainer(container) {
  return [...container.querySelectorAll('.position-row input[name="ticker"]')]
    .map((input) => input.value.trim().toUpperCase())
    .filter((ticker, index, allTickers) => ticker && allTickers.indexOf(ticker) === index);
}

function syncScenarioRsiTriggerOptions(card) {
  const select = card.querySelector(".scenario-rsi-trigger-ticker");
  const tickers = getTickerOptionsFromContainer(card.querySelector(".scenario-positions"));
  const previousValue = select.value;
  select.innerHTML = "";
  tickers.forEach((ticker) => {
    const option = document.createElement("option");
    option.value = ticker;
    option.textContent = ticker;
    select.appendChild(option);
  });
  if (tickers.length) {
    select.value = tickers.includes(previousValue) ? previousValue : tickers[0];
  }
}

function toggleScenarioRsiScope(card) {
  const modeSelect = card.querySelector(".scenario-rebalance-mode");
  const scopeSelect = card.querySelector(".scenario-rsi-signal-scope");
  const triggerField = card.querySelector(".scenario-rsi-trigger-field");
  const triggerSelect = card.querySelector(".scenario-rsi-trigger-ticker");
  const showTrigger = modeSelect.value === "rsi" && scopeSelect.value === "single";
  triggerField.classList.toggle("hidden", !showTrigger);
  triggerSelect.disabled = !showTrigger || !triggerSelect.options.length;
}

function toggleScenarioRebalance(card) {
  const modeSelect = card.querySelector(".scenario-rebalance-mode");
  const isCalendar = modeSelect.value === "calendar";
  card.querySelector(".scenario-frequency-field").classList.toggle("hidden", !isCalendar);
  card.querySelector(".scenario-rsi-fields").classList.toggle("hidden", isCalendar);
  card.querySelector(".scenario-rsi-scope-fields").classList.toggle("hidden", isCalendar);
  toggleScenarioRsiScope(card);
}

function getPositionsFromContainer(container) {
  return [...container.querySelectorAll(".position-row")].map((row) => {
    const terInput = row.querySelector('input[name="annualExpenseRatio"]');
    return {
      ticker: row.querySelector('input[name="ticker"]').value.trim().toUpperCase(),
      targetWeight: Number(row.querySelector('input[name="targetWeight"]').value),
      annualExpenseRatio: terInput ? Number(terInput.value) || 0 : 0,
    };
  });
}

function buildRebalanceFromScenarioCard(card) {
  const modeSelect = card.querySelector(".scenario-rebalance-mode");
  const scopeSelect = card.querySelector(".scenario-rsi-signal-scope");
  const triggerSelect = card.querySelector(".scenario-rsi-trigger-ticker");

  if (modeSelect.value === "calendar") {
    return {
      mode: "calendar",
      frequency: card.querySelector(".scenario-frequency").value,
    };
  }

  const rebalance = {
    mode: "rsi",
    rsiPeriod: Number(card.querySelector(".scenario-rsi-period").value),
    lower: Number(card.querySelector(".scenario-rsi-lower").value),
    upper: Number(card.querySelector(".scenario-rsi-upper").value),
    rsiSignalScope: scopeSelect.value,
  };
  if (rebalance.rsiSignalScope === "single") {
    rebalance.rsiTriggerTicker = triggerSelect.value?.trim().toUpperCase();
  }
  return rebalance;
}

function buildComparePayload() {
  const formData = new FormData(form);
  const period = buildPeriodFromFormData(formData);
  const execution = buildExecutionFromFormData(formData);
  const initialCapital = Number(formData.get("initialCapital"));
  const monthlyContribution = Number(formData.get("monthlyContribution"));

  const runs = [...scenariosContainer.querySelectorAll(".scenario-card")].map((card) => {
    const label = card.querySelector(".scenario-label-input").value.trim();
    const positions = getPositionsFromContainer(card.querySelector(".scenario-positions"));
    const rebalance = buildRebalanceFromScenarioCard(card);
    return {
      label,
      request: {
        positions,
        initialCapital,
        monthlyContribution,
        period,
        rebalance,
        execution,
      },
    };
  });

  return { runs };
}

function renderCompareSummaryTable(runs) {
  const labels = runs.map((r) => r.label);
  compareSummaryHead.innerHTML = `<tr><th scope="col">지표</th>${labels
    .map((l) => `<th scope="col">${escapeHtml(l)}</th>`)
    .join("")}</tr>`;

  const row = (title, getter) =>
    `<tr><th scope="row">${title}</th>${runs
      .map((r) => `<td>${getter(r.result.summary)}</td>`)
      .join("")}</tr>`;

  compareSummaryBody.innerHTML = [
    row("최종 평가액", (s) => currency(s.finalValue)),
    row("누적 운영보수(TER)", (s) => currency(s.totalExpensePaid ?? 0)),
    row("실질 최종 평가액", (s) => currency(s.realFinalValue)),
    row("총 수익률", (s) => percent(s.totalReturnPct)),
    row("실질 총 수익률", (s) => percent(s.realTotalReturnPct)),
    row("CAGR", (s) => (s.cagrPct === null ? "-" : percent(s.cagrPct))),
    row("XIRR", (s) => (s.xirrPct === null ? "-" : percent(s.xirrPct))),
    row("MDD", (s) => percent(s.mddPct)),
    row("리밸런스 횟수", (s) => String(s.rebalanceCount)),
  ].join("");

  compareSummaryWrap.classList.remove("hidden");
}

function wireScenarioPositionRow(card, row) {
  const positionsEl = card.querySelector(".scenario-positions");
  row.querySelector(".remove-position-btn").addEventListener("click", () => {
    if (positionsEl.children.length === 1) {
      statusText.textContent = "각 시나리오는 최소 한 종목이 필요합니다.";
      return;
    }
    row.remove();
    syncScenarioRsiTriggerOptions(card);
    toggleScenarioRsiScope(card);
  });
  row.querySelector('input[name="ticker"]').addEventListener("input", () => {
    syncScenarioRsiTriggerOptions(card);
    toggleScenarioRsiScope(card);
  });
}

function addScenarioPositionRow(card, ticker = "", targetWeight = "", annualExpenseRatio = "") {
  const positionsEl = card.querySelector(".scenario-positions");
  const fragment = positionTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".position-row");
  row.querySelector('input[name="ticker"]').value = ticker;
  row.querySelector('input[name="targetWeight"]').value = targetWeight;
  const terEl = row.querySelector('input[name="annualExpenseRatio"]');
  if (terEl) {
    terEl.value = annualExpenseRatio === "" ? "0" : String(annualExpenseRatio);
  }
  wireScenarioPositionRow(card, row);
  positionsEl.appendChild(fragment);
  syncScenarioRsiTriggerOptions(card);
  toggleScenarioRsiScope(card);
}

function wireScenarioCard(card) {
  card.querySelector(".scenario-rebalance-mode").addEventListener("change", () => toggleScenarioRebalance(card));
  card.querySelector(".scenario-rsi-signal-scope").addEventListener("change", () => {
    syncScenarioRsiTriggerOptions(card);
    toggleScenarioRsiScope(card);
  });
  card.querySelector(".add-scenario-position-btn").addEventListener("click", () => addScenarioPositionRow(card, "", ""));
  card.querySelector(".remove-scenario-btn").addEventListener("click", () => {
    if (scenariosContainer.children.length <= COMPARE_MIN_SCENARIOS) {
      statusText.textContent = `비교 모드에서는 시나리오를 최소 ${COMPARE_MIN_SCENARIOS}개 유지해야 합니다.`;
      return;
    }
    card.remove();
    updateAddScenarioButtonState();
  });
}

function updateAddScenarioButtonState() {
  addScenarioButton.disabled = scenariosContainer.children.length >= COMPARE_MAX_SCENARIOS;
}

function addScenarioCard(preset) {
  if (scenariosContainer.children.length >= COMPARE_MAX_SCENARIOS) {
    return;
  }
  const fragment = scenarioCardTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".scenario-card");
  const labelInput = card.querySelector(".scenario-label-input");
  labelInput.value = preset?.label ?? `시나리오 ${scenariosContainer.children.length + 1}`;

  wireScenarioCard(card);
  scenariosContainer.appendChild(card);

  const positions = preset?.positions ?? [
    { ticker: "SCHD", weight: 50 },
    { ticker: "TQQQ", weight: 50 },
  ];
  positions.forEach((p) =>
    addScenarioPositionRow(card, p.ticker, p.weight, p.annualExpenseRatio !== undefined ? p.annualExpenseRatio : ""),
  );

  if (preset?.rebalanceMode === "rsi") {
    card.querySelector(".scenario-rebalance-mode").value = "rsi";
    toggleScenarioRebalance(card);
  } else {
    toggleScenarioRebalance(card);
  }
  updateAddScenarioButtonState();
}

function populateScenarioDetailSelect(runs) {
  scenarioDetailSelect.innerHTML = "";
  runs.forEach((run, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = run.label;
    scenarioDetailSelect.appendChild(option);
  });
  scenarioDetailLabel.classList.remove("hidden");
}

function renderDetailForCompareIndex(index) {
  const run = lastCompareRuns[index];
  if (!run) {
    return;
  }
  renderHoldings(run.result.holdingsSnapshot);
  renderEvents(run.result.rebalanceEvents);
}

async function runBacktest(event) {
  event.preventDefault();

  if (isCompareMode()) {
    await runCompareBacktest();
    return;
  }

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
      throw new Error(body.detail !== undefined ? formatApiDetail(body.detail) : "Backtest request failed.");
    }

    resultViewMode = "single";
    lastCompareRuns = [];
    compareSummaryWrap.classList.add("hidden");
    scenarioDetailLabel.classList.add("hidden");
    summaryGrid.classList.remove("hidden");
    resultsHint.textContent = "Review summary metrics, the equity curve, and the executed rebalance events.";

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

async function runCompareBacktest() {
  const payload = buildComparePayload();
  const emptyLabel = payload.runs.find((r) => !r.label);
  if (emptyLabel) {
    statusText.textContent = "모든 시나리오에 이름을 입력하세요.";
    return;
  }

  statusText.textContent = "비교 백테스트 실행 중...";

  try {
    const response = await fetch("/api/backtests/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail !== undefined ? formatApiDetail(body.detail) : "Compare request failed.");
    }

    resultViewMode = "compare";
    lastCompareRuns = body.runs;
    summaryGrid.classList.add("hidden");
    compareSummaryWrap.classList.remove("hidden");
    renderCompareSummaryTable(lastCompareRuns);
    populateScenarioDetailSelect(lastCompareRuns);
    scenarioDetailSelect.value = "0";
    currentInflationRatePct = Number(lastCompareRuns[0].result.summary.inflationRatePct);
    renderDetailForCompareIndex(0);
    renderActiveChart();
    resultsHint.textContent = "표와 차트로 시나리오를 비교합니다. 홀딩·이벤트는 상단에서 시나리오를 선택하세요.";
    statusText.textContent = `비교 완료: ${lastCompareRuns.length}개 시나리오`;
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

function addPositionRow(ticker = "", targetWeight = "", annualExpenseRatio = "") {
  const fragment = positionTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".position-row");
  row.querySelector('input[name="ticker"]').value = ticker;
  row.querySelector('input[name="targetWeight"]').value = targetWeight;
  const terEl = row.querySelector('input[name="annualExpenseRatio"]');
  if (terEl) {
    terEl.value = annualExpenseRatio === "" ? "0" : String(annualExpenseRatio);
  }

  wirePositionRow(row);
  positionsContainer.appendChild(fragment);
  syncRsiTriggerTickerOptions();
  toggleRsiScopeFields();
}

addPositionButton.addEventListener("click", () => addPositionRow("", ""));
addScenarioButton.addEventListener("click", () => {
  addScenarioCard({
    label: `시나리오 ${scenariosContainer.children.length + 1}`,
    positions: [{ ticker: "VT", weight: 100 }],
  });
});
runModeButtons.forEach((btn) => {
  btn.addEventListener("click", () => setRunMode(btn.dataset.runMode));
});
periodModeSelect.addEventListener("change", togglePeriodFields);
rebalanceModeSelect.addEventListener("change", toggleRebalanceFields);
rsiSignalScopeSelect.addEventListener("change", toggleRsiScopeFields);
chartModeButtons.forEach((button) => {
  button.addEventListener("click", () => setChartMode(button.dataset.chartMode));
});
scenarioDetailSelect.addEventListener("change", () => {
  renderDetailForCompareIndex(Number(scenarioDetailSelect.value));
});
form.addEventListener("submit", runBacktest);

addPositionRow("AAPL", 40);
addPositionRow("MSFT", 35);
addPositionRow("QQQ", 25);
togglePeriodFields();
syncRsiTriggerTickerOptions();
toggleRebalanceFields();
applyRunModeUi();
syncChromeAfterRunModeChange();
setChartMode("nominal");
