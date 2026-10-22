/**
 * US Portfolio Backtester — 프론트 로직
 * 수정: 2026-04-23 — 포트폴리오 비교 모드, 다중 시리즈 차트(교집합 거래일), 비교 요약 표 추가
 * 수정: 2026-04-23 — 실행 모드를 셀렉트 대신 탭 버튼으로 바꿔 비교 기능이 한눈에 보이게 함
 * 수정: 2026-04-23 — 리밸런스 모드에 밴드(드리프트) 옵션 및 폼 필드 연동
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
const bandFields = document.getElementById("band-fields");
const rsiFields = document.getElementById("rsi-fields");
const rsiScopeFields = document.getElementById("rsi-scope-fields");
const guardFields = document.getElementById("guard-fields");
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
const riskProfileForm = document.getElementById("risk-profile-form");
const riskPeriodModeSelect = document.getElementById("risk-period-mode");
const riskPeriodRangeFields = document.getElementById("risk-period-range-fields");
const riskPeriodLookbackFields = document.getElementById("risk-period-lookback-fields");
const riskWeightAInput = document.querySelector('input[name="riskWeightA"]');
const riskWeightBInput = document.querySelector('input[name="riskWeightB"]');
const riskStatusText = document.getElementById("risk-status-text");
const riskSummaryGrid = document.getElementById("risk-summary-grid");
const riskMetricsWrap = document.getElementById("risk-metrics-wrap");
const riskMetricsBody = document.getElementById("risk-metrics-body");
const riskAllocationCanvas = document.getElementById("risk-allocation-chart");
const riskScatterCanvas = document.getElementById("risk-scatter-chart");
const riskNormalizedCanvas = document.getElementById("risk-normalized-chart");
const riskAllocationContext = riskAllocationCanvas.getContext("2d");
const riskScatterContext = riskScatterCanvas.getContext("2d");
const riskNormalizedContext = riskNormalizedCanvas.getContext("2d");
const riskAllocationRange = document.getElementById("risk-allocation-range");
const riskScatterRange = document.getElementById("risk-scatter-range");
const riskNormalizedRange = document.getElementById("risk-normalized-range");
const riskAllocationLegend = document.getElementById("risk-allocation-legend");
const riskChartLegend = document.getElementById("risk-chart-legend");

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
let resultsStale = false;

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
  const mode = rebalanceModeSelect.value;
  const isCalendar = mode === "calendar";
  const isRsi = mode === "rsi";
  const isBand = mode === "band";
  const isGuard = mode === "etf_guard";
  frequencyField.classList.toggle("hidden", !isCalendar);
  bandFields.classList.toggle("hidden", !isBand);
  rsiFields.classList.toggle("hidden", !isRsi);
  rsiScopeFields.classList.toggle("hidden", !isRsi);
  guardFields.classList.toggle("hidden", !isGuard);
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
    : "목표 비중의 합계를 100%로 맞춰 주세요.";
  if (runModeHint) {
    runModeHint.textContent = compare
      ? "시나리오 카드(최소 2개)마다 다른 티커·비중·리밸런스를 넣고, 아래 공통 기간·자본을 맞춘 뒤 \"비교 실행\"을 누르세요. (최대 8개)"
      : "종목과 투자 조건을 정하면 과거 성과를 계산합니다.";
  }
  submitButton.textContent = compare ? "비교 실행" : "분석하기";

  if (compare && scenariosContainer.children.length < COMPARE_MIN_SCENARIOS) {
    while (scenariosContainer.children.length < COMPARE_MIN_SCENARIOS) {
      addScenarioCard();
    }
  }
}

/** 모드 전환 직후 결과 패널·차트 표시만 정리 (초기 로드에서도 호출 가능) */
function syncChromeAfterRunModeChange() {
  markResultsStale();
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
  const annual = summary.xirrPct ?? summary.cagrPct;
  const items = [
    ["최종 평가금액", currency(summary.finalValue)],
    ["투자 손익", currency(summary.finalValue - summary.totalContributed)],
    [summary.xirrPct !== null ? "연환산 수익률 · XIRR" : "연평균 성장률 · CAGR", annual === null ? "계산 불가" : percent(annual)],
    ["최대 낙폭 · MDD", percent(summary.mddPct)],
    ["총 납입금", currency(summary.totalContributed)],
    ["납입 대비 수익률", percent(summary.totalReturnPct)],
  ];
  summaryGrid.classList.remove("empty-state", "hidden");
  summaryGrid.innerHTML = items.map(([label, value], index) => {
    const number = [null, summary.finalValue - summary.totalContributed, annual, summary.mddPct, null, summary.totalReturnPct][index];
    const tone = number > 0 ? "positive" : number < 0 ? "negative" : "";
    return `<article class="metric-card ${tone}"><span>${label}</span><strong>${value}</strong></article>`;
  }).join("");
  document.getElementById("summary-details").classList.remove("hidden");
  document.getElementById("summary-details-body").innerHTML = `
    <p>시작 투자금 ${currency(summary.initialCapital)} · 매월 추가 투자금 ${currency(summary.monthlyContribution)}</p>
    <p>납입 대비 수익률 ${percent(summary.totalReturnPct)} · 누적 추가 보수 ${currency(summary.totalExpensePaid ?? 0)}</p>
    <p>연평균 성장률(CAGR) ${summary.cagrPct === null ? "추가 납입 시 미제공" : percent(summary.cagrPct)}</p>
    <p>물가 조정 평가금액 ${currency(summary.realFinalValue)} · 물가상승률 연 ${percent(summary.inflationRatePct)} 가정</p>
    <p>XIRR은 납입 시점을 반영한 연환산 수익률입니다. MDD는 추가 납입 효과를 제외한 고점 대비 최대 하락률입니다.</p>`;
}

function renderResultContext(result, label) {
  const badge = document.getElementById("result-badge");
  badge.textContent = resultsStale ? "이전 결과" : "분석 완료";
  badge.className = resultsStale ? "result-badge stale" : "result-badge ready";
  const points = result.equityCurve;
  document.getElementById("result-context").textContent = `${label} · 실제 거래일 ${points[0].date} ~ ${points.at(-1).date} · 총 납입 ${currency(result.summary.totalContributed)}`;
  const allocations = result.holdingsSnapshot.map(item => ({ticker: item.ticker, weight: item.weight}));
  const cash = Math.max(0, result.summary.finalValue - result.holdingsSnapshot.reduce((sum, item) => sum + item.value, 0));
  if (cash > 0.01 && result.summary.finalValue > 0) allocations.push({ticker: "현금", weight: cash / result.summary.finalValue * 100});
  const target = document.getElementById("final-allocation");
  target.classList.remove("hidden");
  const totalWeight = allocations.reduce((sum, item) => sum + item.weight, 0);
  let angle = 0;
  const stops = allocations.map((item, index) => {
    const start = angle;
    angle += totalWeight > 0 ? item.weight / totalWeight * 100 : 0;
    return `${SERIES_COLORS[index % SERIES_COLORS.length]} ${start}% ${angle}%`;
  });
  target.innerHTML = `<h3>종료일 자산 구성 <small>${escapeHtml(label)} · 현금 포함</small></h3>
    <div class="allocation-donut" aria-hidden="true" style="background:${totalWeight > 0 ? `conic-gradient(${stops.join(",")})` : "#edf1f5"}"><div class="donut-center">구성 자산<b>${allocations.filter(item => item.weight > 0).length}</b></div></div>
    ${allocationMarkup(allocations)}`;
}

function allocationMarkup(items) {
  return `<div class="allocation-bar" aria-hidden="true">${items.map((item, index) =>
    `<span style="flex:${Math.max(0, item.weight)};background:${SERIES_COLORS[index % SERIES_COLORS.length]}"></span>`).join("")}</div>
    <div class="allocation-labels">${items.map((item, index) => `<span><i style="background:${SERIES_COLORS[index % SERIES_COLORS.length]}"></i>${escapeHtml(item.ticker || "미입력")} ${percent(item.weight)}</span>`).join("")}</div>`;
}

function refreshAllocations() {
  document.querySelectorAll(".allocation-preview").forEach(target => {
    const items = getPositionsFromContainer(target.previousElementSibling).map(item => ({ticker: item.ticker, weight: item.targetWeight}));
    const sum = items.reduce((total, item) => total + item.weight, 0);
    target.innerHTML = `<p class="${Math.abs(sum - 100) > 0.001 ? "allocation-warning" : "allocation-valid"}">비중 합계 ${percent(sum)} ${Math.abs(sum - 100) > 0.001 ? "· 100%로 맞춰 주세요" : "· 준비 완료"}</p>${allocationMarkup(items)}`;
  });
}

function markResultsStale() {
  if (chartSeries.nominal.length || lastCompareRuns.length) {
    resultsStale = true;
    resultsHint.textContent = "설정이 변경되었습니다. 아래는 이전 실행 결과입니다. 다시 분석하면 갱신됩니다.";
    document.getElementById("result-badge").textContent = "이전 결과";
    document.getElementById("result-badge").className = "result-badge stale";
  }
}

function syncInactiveFields() {
  if (backtestBusy) return;
  form.querySelectorAll("input, select").forEach(input => { input.disabled = Boolean(input.closest(".hidden")); });
}

function lockControls(container, extras = []) {
  const controls = [...container.querySelectorAll("input, select, button"), ...extras];
  const states = controls.map(control => control.disabled);
  controls.forEach(control => { control.disabled = true; });
  container.setAttribute("aria-busy", "true");
  return () => {
    controls.forEach((control, index) => { control.disabled = states[index]; });
    container.removeAttribute("aria-busy");
  };
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
          <td>${escapeHtml(item.ticker)}</td>
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

  eventsBody.innerHTML = [...events].reverse()
    .map(
      (event) => `
        <tr>
          <td>${event.date}</td>
          <td>${escapeHtml(event.reason)}</td>
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
    canvasContext.fillText("분석 후 자산 추이가 표시됩니다.", 16, 60);
    chartRange.textContent = "";
    return;
  }

  const firstSeries = seriesList[0];
  if (!firstSeries || !firstSeries.points.length) {
    canvasContext.fillStyle = "#6c6d72";
    canvasContext.font = "16px Segoe UI";
    canvasContext.fillText("분석 후 자산 추이가 표시됩니다.", 16, 60);
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
    canvasContext.fillText(new Intl.NumberFormat("en-US", {notation: "compact", maximumFractionDigits: 1}).format(labelValue), 8, y + 4);
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
        ? `${baseNote} 동일한 실제 거래일 기준 비교입니다.`
        : baseNote;
    return;
  }

  const points = chartSeries[activeChartMode] || [];
  renderChartLegend([{ label: "", color: "#0e8f70", points }]);
  drawEquitySeries(points.length ? [{ label: "", color: "#0e8f70", points }] : []);
  chartNote.textContent =
    activeChartMode === "real"
      ? `연 ${currentInflationRatePct.toFixed(1)}% 물가상승 가정 · 시작일 구매력 기준`
      : "추가 납입금을 포함한 평가금액입니다.";
}

function setChartMode(mode) {
  activeChartMode = mode;
  updateChartModeButtons();
  renderActiveChart();
}

function toggleRiskPeriodFields() {
  const isRange = riskPeriodModeSelect.value === "range";
  riskPeriodRangeFields.classList.toggle("hidden", !isRange);
  riskPeriodLookbackFields.classList.toggle("hidden", isRange);

  riskPeriodRangeFields.querySelectorAll("input").forEach((input) => {
    input.disabled = !isRange;
    input.required = isRange;
  });
  riskPeriodLookbackFields.querySelectorAll("input").forEach((input) => {
    input.disabled = isRange;
    input.required = !isRange;
  });
}

function buildRiskProfilePayload() {
  const formData = new FormData(riskProfileForm);
  const period =
    riskPeriodModeSelect.value === "range"
      ? {
          startDate: formData.get("riskStartDate"),
          endDate: formData.get("riskEndDate"),
        }
      : {
          lookbackYears: Number(formData.get("riskLookbackYears")),
        };

  return {
    tickers: [
      formData.get("riskTickerA")?.toString().trim().toUpperCase(),
      formData.get("riskTickerB")?.toString().trim().toUpperCase(),
    ],
    weightsPct: [Number(formData.get("riskWeightA")), Number(formData.get("riskWeightB"))],
    period,
  };
}

function renderRiskSummary(profile) {
  const correlationValue = profile.correlation === null ? "-" : profile.correlation.toFixed(4);
  const allocationLabel = `${profile.tickers[0]} ${profile.weightsPct[0].toFixed(1)}% / ${profile.tickers[1]} ${profile.weightsPct[1].toFixed(1)}%`;
  const items = [
    ["초기 비중", allocationLabel],
    ["포트폴리오 누적 수익률", percent(profile.portfolioMetric.totalReturnPct)],
    ["최대 낙폭", percent(profile.portfolioMetric.mddPct)],
    ["연환산 변동성", percent(profile.portfolioMetric.annualizedVolatilityPct)],
    ["일간 수익률 상관계수", correlationValue],
  ];

  riskSummaryGrid.classList.remove("empty-state");
  riskSummaryGrid.innerHTML = items
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

function renderRiskMetrics(metrics) {
  riskMetricsBody.innerHTML = metrics
    .map(
      (metric) => `
        <tr>
          <td>${escapeHtml(metric.ticker)}</td>
          <td>${percent(metric.totalReturnPct)}</td>
          <td>${percent(metric.annualizedReturnPct)}</td>
          <td>${percent(metric.annualizedVolatilityPct)}</td>
          <td>${percent(metric.mddPct)}</td>
        </tr>
      `,
    )
    .join("");
  riskMetricsWrap.classList.remove("hidden");
}

function renderRiskAllocationLegend() {
  riskAllocationLegend.classList.remove("hidden");
  riskAllocationLegend.setAttribute("aria-hidden", "false");
  riskAllocationLegend.innerHTML = `
    <span class="chart-legend-item">
      <span class="chart-legend-swatch" style="background:#0e8f70"></span>
      Allocation point
    </span>
  `;
}

function drawRiskAllocationSweep(profile) {
  const width = riskAllocationCanvas.width;
  const height = riskAllocationCanvas.height;
  const padding = { top: 24, right: 22, bottom: 44, left: 58 };
  const points = profile.allocationProfiles || [];

  riskAllocationContext.clearRect(0, 0, width, height);
  riskAllocationContext.fillStyle = "#fffdf8";
  riskAllocationContext.fillRect(0, 0, width, height);

  if (!points.length) {
    riskAllocationContext.fillStyle = "#6c6d72";
    riskAllocationContext.font = "16px Segoe UI";
    riskAllocationContext.fillText("No allocation data available.", 32, 58);
    return;
  }

  const xValues = points.map((point) => Math.abs(point.metric.mddPct));
  const yValues = points.map((point) => point.metric.totalReturnPct);
  const minX = Math.min(0, ...xValues);
  const maxX = Math.max(1, ...xValues);
  const minY = Math.min(0, ...yValues);
  const maxY = Math.max(1, ...yValues);
  const xSpan = Math.max(maxX - minX, 1);
  const ySpan = Math.max(maxY - minY, 1);
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const xAt = (value) => padding.left + ((value - minX) / xSpan) * innerWidth;
  const yAt = (value) => padding.top + ((maxY - value) / ySpan) * innerHeight;

  riskAllocationContext.strokeStyle = "rgba(29, 36, 51, 0.12)";
  riskAllocationContext.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const x = padding.left + (innerWidth / 4) * step;
    riskAllocationContext.beginPath();
    riskAllocationContext.moveTo(x, padding.top);
    riskAllocationContext.lineTo(x, height - padding.bottom);
    riskAllocationContext.stroke();
  }
  for (let step = 0; step <= 4; step += 1) {
    const y = padding.top + (innerHeight / 4) * step;
    riskAllocationContext.beginPath();
    riskAllocationContext.moveTo(padding.left, y);
    riskAllocationContext.lineTo(width - padding.right, y);
    riskAllocationContext.stroke();
  }

  riskAllocationContext.fillStyle = "#6c6d72";
  riskAllocationContext.font = "12px Segoe UI";
  for (let step = 0; step <= 4; step += 1) {
    const xValue = minX + (xSpan / 4) * step;
    riskAllocationContext.fillText(`${xValue.toFixed(1)}%`, padding.left + (innerWidth / 4) * step - 14, height - 16);
  }
  for (let step = 0; step <= 4; step += 1) {
    const y = padding.top + (innerHeight / 4) * step;
    const labelValue = maxY - (ySpan / 4) * step;
    riskAllocationContext.fillText(`${labelValue.toFixed(1)}%`, 8, y + 4);
  }

  points.forEach((point) => {
    const x = xAt(Math.abs(point.metric.mddPct));
    const y = yAt(point.metric.totalReturnPct);
    const ratioLabel = `${Math.round(point.weightsPct[0] / 10)}:${Math.round(point.weightsPct[1] / 10)}`;

    riskAllocationContext.beginPath();
    riskAllocationContext.arc(x, y, 6, 0, Math.PI * 2);
    riskAllocationContext.fillStyle = "#0e8f70";
    riskAllocationContext.fill();
    riskAllocationContext.strokeStyle = "#fffdf8";
    riskAllocationContext.lineWidth = 2;
    riskAllocationContext.stroke();

    riskAllocationContext.fillStyle = "#1d2433";
    riskAllocationContext.font = "700 12px Segoe UI";
    riskAllocationContext.fillText(ratioLabel, Math.min(x + 8, width - padding.right - 28), Math.max(y - 8, padding.top + 12));
  });
  riskAllocationRange.textContent = `${profile.tickers[0]}:${profile.tickers[1]} allocation points`;
}

function drawRiskScatter(metrics) {
  const width = riskScatterCanvas.width;
  const height = riskScatterCanvas.height;
  const padding = { top: 28, right: 24, bottom: 46, left: 62 };
  riskScatterContext.clearRect(0, 0, width, height);
  riskScatterContext.fillStyle = "#fffdf8";
  riskScatterContext.fillRect(0, 0, width, height);

  if (!metrics.length) {
    riskScatterContext.fillStyle = "#6c6d72";
    riskScatterContext.font = "16px Segoe UI";
    riskScatterContext.fillText("No risk data available.", 32, 58);
    return;
  }

  const xValues = metrics.map((m) => m.annualizedVolatilityPct);
  const yValues = metrics.map((m) => m.annualizedReturnPct);
  const minX = Math.min(0, ...xValues);
  const maxX = Math.max(...xValues, 1);
  const minY = Math.min(0, ...yValues);
  const maxY = Math.max(...yValues, 1);
  const xSpan = Math.max(maxX - minX, 1);
  const ySpan = Math.max(maxY - minY, 1);
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const xAt = (value) => padding.left + ((value - minX) / xSpan) * innerWidth;
  const yAt = (value) => padding.top + ((maxY - value) / ySpan) * innerHeight;

  riskScatterContext.strokeStyle = "rgba(29, 36, 51, 0.12)";
  riskScatterContext.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const x = padding.left + (innerWidth / 4) * step;
    const y = padding.top + (innerHeight / 4) * step;
    riskScatterContext.beginPath();
    riskScatterContext.moveTo(x, padding.top);
    riskScatterContext.lineTo(x, height - padding.bottom);
    riskScatterContext.stroke();
    riskScatterContext.beginPath();
    riskScatterContext.moveTo(padding.left, y);
    riskScatterContext.lineTo(width - padding.right, y);
    riskScatterContext.stroke();
  }

  riskScatterContext.fillStyle = "#6c6d72";
  riskScatterContext.font = "12px Segoe UI";
  for (let step = 0; step <= 4; step += 1) {
    const xValue = minX + (xSpan / 4) * step;
    const yValue = maxY - (ySpan / 4) * step;
    riskScatterContext.fillText(`${xValue.toFixed(1)}%`, padding.left + (innerWidth / 4) * step - 12, height - 18);
    riskScatterContext.fillText(`${yValue.toFixed(1)}%`, 10, padding.top + (innerHeight / 4) * step + 4);
  }

  metrics.forEach((metric, index) => {
    const x = xAt(metric.annualizedVolatilityPct);
    const y = yAt(metric.annualizedReturnPct);
    riskScatterContext.beginPath();
    riskScatterContext.arc(x, y, 7, 0, Math.PI * 2);
    riskScatterContext.fillStyle = SERIES_COLORS[index % SERIES_COLORS.length];
    riskScatterContext.fill();
    riskScatterContext.fillStyle = "#1d2433";
    riskScatterContext.font = "700 13px Segoe UI";
    riskScatterContext.fillText(metric.ticker, Math.min(x + 10, width - padding.right - 44), Math.max(y - 10, padding.top + 12));
  });
}

function renderRiskChartLegend(tickers) {
  riskChartLegend.classList.remove("hidden");
  riskChartLegend.setAttribute("aria-hidden", "false");
  riskChartLegend.innerHTML = tickers
    .map(
      (ticker, index) => `
      <span class="chart-legend-item">
        <span class="chart-legend-swatch" style="background:${SERIES_COLORS[index % SERIES_COLORS.length]}"></span>
        ${escapeHtml(ticker)}
      </span>
    `,
    )
    .join("");
}

function drawRiskNormalizedChart(profile) {
  const width = riskNormalizedCanvas.width;
  const height = riskNormalizedCanvas.height;
  const padding = { top: 24, right: 20, bottom: 36, left: 52 };
  riskNormalizedContext.clearRect(0, 0, width, height);
  riskNormalizedContext.fillStyle = "#fffdf8";
  riskNormalizedContext.fillRect(0, 0, width, height);

  const points = profile.normalizedPriceCurve;
  if (!points.length) {
    riskNormalizedContext.fillStyle = "#6c6d72";
    riskNormalizedContext.font = "16px Segoe UI";
    riskNormalizedContext.fillText("No chart data available.", 32, 58);
    riskNormalizedRange.textContent = "";
    return;
  }

  const seriesLabels = ["Portfolio", ...profile.tickers];
  const allValues = points.flatMap((point) => seriesLabels.map((label) => point.values[label]));
  const minValue = Math.min(...allValues);
  const maxValue = Math.max(...allValues);
  const valueSpan = Math.max(maxValue - minValue, 1);
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const xAt = (index) => padding.left + (innerWidth * index) / Math.max(points.length - 1, 1);
  const yAt = (value) => padding.top + ((maxValue - value) / valueSpan) * innerHeight;

  riskNormalizedContext.strokeStyle = "rgba(29, 36, 51, 0.12)";
  riskNormalizedContext.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const y = padding.top + (innerHeight / 4) * step;
    riskNormalizedContext.beginPath();
    riskNormalizedContext.moveTo(padding.left, y);
    riskNormalizedContext.lineTo(width - padding.right, y);
    riskNormalizedContext.stroke();
  }

  riskNormalizedContext.fillStyle = "#6c6d72";
  riskNormalizedContext.font = "12px Segoe UI";
  for (let step = 0; step <= 4; step += 1) {
    const y = padding.top + (innerHeight / 4) * step;
    const labelValue = maxValue - (valueSpan / 4) * step;
    riskNormalizedContext.fillText(labelValue.toFixed(1), 8, y + 4);
  }

  seriesLabels.forEach((label, labelIndex) => {
    riskNormalizedContext.beginPath();
    points.forEach((point, pointIndex) => {
      const x = xAt(pointIndex);
      const y = yAt(point.values[label]);
      if (pointIndex === 0) {
        riskNormalizedContext.moveTo(x, y);
      } else {
        riskNormalizedContext.lineTo(x, y);
      }
    });
    riskNormalizedContext.strokeStyle = SERIES_COLORS[labelIndex % SERIES_COLORS.length];
    riskNormalizedContext.lineWidth = label === "Portfolio" ? 3.5 : 2.2;
    riskNormalizedContext.stroke();
  });

  riskNormalizedRange.textContent = `${points[0].date} ~ ${points[points.length - 1].date}`;
}

function renderRiskProfile(profile) {
  lastRiskProfile = profile;
  renderRiskSummary(profile);
  renderRiskMetrics([profile.portfolioMetric, ...profile.metrics]);
  renderRiskAllocationLegend();
  drawRiskAllocationSweep(profile);
  riskScatterRange.textContent = `${profile.startDate} ~ ${profile.endDate}`;
  drawRiskScatter([profile.portfolioMetric, ...profile.metrics]);
  renderRiskChartLegend(["Portfolio", ...profile.tickers]);
  drawRiskNormalizedChart(profile);
}

function syncRiskWeights(changedInput) {
  const source = changedInput === riskWeightAInput ? riskWeightAInput : riskWeightBInput;
  const target = changedInput === riskWeightAInput ? riskWeightBInput : riskWeightAInput;
  const value = Math.max(0, Math.min(100, Number(source.value) || 0));
  source.value = String(value);
  target.value = (100 - value).toFixed(1).replace(/\.0$/, "");
}

async function runRiskProfile(event) {
  event.preventDefault();
  if (riskProfileForm.getAttribute("aria-busy") === "true") return;
  const payload = buildRiskProfilePayload();
  const unlock = lockControls(riskProfileForm);
  riskStatusText.textContent = "수익과 위험을 분석하고 있습니다…";

  try {
    const response = await fetch("/api/risk-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail !== undefined ? formatApiDetail(body.detail) : "Risk profile request failed.");
    }

    renderRiskProfile(body);
    riskStatusText.textContent = "수익·위험 분석이 완료되었습니다.";
  } catch (error) {
    riskStatusText.textContent = error.message;
  } finally {
    unlock();
  }
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
  const mode = rebalanceModeSelect.value;
  if (mode === "calendar") {
    return { mode: "calendar", frequency: formData.get("frequency") };
  }
  if (mode === "band") {
    return { mode: "band", bandWidthPct: Number(formData.get("bandWidthPct")) };
  }
  if (mode === "etf_guard") {
    return {
      mode: "etf_guard",
      guardRiskTicker: formData.get("guardRiskTicker")?.toString().trim().toUpperCase(),
      guardCoreTicker: formData.get("guardCoreTicker")?.toString().trim().toUpperCase(),
      guardDefensiveTicker: formData.get("guardDefensiveTicker")?.toString().trim().toUpperCase(),
      guardSmaWindow: Number(formData.get("guardSmaWindow")),
      guardRsiPeriod: Number(formData.get("guardRsiPeriod")),
      guardRsiUpper: Number(formData.get("guardRsiUpper")),
      guardRiskMaxWeightPct: Number(formData.get("guardRiskMaxWeightPct")),
    };
  }

  const rebalance = {
    mode: "rsi",
    rsiPeriod: Number(formData.get("rsiPeriod")),
    lower: Number(formData.get("lower")),
    upper: Number(formData.get("upper")),
    rsiSignalScope: formData.get("rsiSignalScope"),
  };
  if (rebalance.rsiSignalScope === "single") {
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
  const mode = modeSelect.value;
  const isCalendar = mode === "calendar";
  const isRsi = mode === "rsi";
  const isBand = mode === "band";
  const isGuard = mode === "etf_guard";
  card.querySelector(".scenario-frequency-field").classList.toggle("hidden", !isCalendar);
  const bandField = card.querySelector(".scenario-band-field");
  if (bandField) {
    bandField.classList.toggle("hidden", !isBand);
  }
  card.querySelector(".scenario-rsi-fields").classList.toggle("hidden", !isRsi);
  card.querySelector(".scenario-rsi-scope-fields").classList.toggle("hidden", !isRsi);
  card.querySelector(".scenario-guard-fields").classList.toggle("hidden", !isGuard);
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

  if (modeSelect.value === "band") {
    return {
      mode: "band",
      bandWidthPct: Number(card.querySelector(".scenario-band-width")?.value),
    };
  }
  if (modeSelect.value === "etf_guard") {
    return {
      mode: "etf_guard",
      guardRiskTicker: card.querySelector(".scenario-guard-risk-ticker").value.trim().toUpperCase(),
      guardCoreTicker: card.querySelector(".scenario-guard-core-ticker").value.trim().toUpperCase(),
      guardDefensiveTicker: card.querySelector(".scenario-guard-defensive-ticker").value.trim().toUpperCase(),
      guardSmaWindow: Number(card.querySelector(".scenario-guard-sma-window").value),
      guardRsiPeriod: Number(card.querySelector(".scenario-guard-rsi-period").value),
      guardRsiUpper: Number(card.querySelector(".scenario-guard-rsi-upper").value),
      guardRiskMaxWeightPct: Number(card.querySelector(".scenario-guard-risk-max-weight").value),
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
    row("총 납입금", (s) => currency(s.totalContributed)),
    row("최종 평가액", (s) => currency(s.finalValue)),
    row("투자 손익", (s) => currency(s.finalValue - s.totalContributed)),
    row("누적 운영보수(TER)", (s) => currency(s.totalExpensePaid ?? 0)),
    row("실질 최종 평가액", (s) => currency(s.realFinalValue)),
    row("총 수익률", (s) => percent(s.totalReturnPct)),
    row("실질 총 수익률", (s) => percent(s.realTotalReturnPct)),
    row("연평균 성장률 · CAGR", (s) => (s.cagrPct === null ? "추가 납입 시 미제공" : percent(s.cagrPct))),
    row("연환산 수익률 · XIRR", (s) => (s.xirrPct === null ? "계산 불가" : percent(s.xirrPct))),
    row("최대 낙폭 · MDD", (s) => percent(s.mddPct)),
    row("거래·자금 이벤트 수", (s) => String(s.rebalanceCount)),
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
  } else if (preset?.rebalanceMode === "band") {
    card.querySelector(".scenario-rebalance-mode").value = "band";
    const bw = card.querySelector(".scenario-band-width");
    if (bw && preset.bandWidthPct != null) {
      bw.value = String(preset.bandWidthPct);
    }
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
  renderSummary(run.result.summary);
  renderResultContext(run.result, run.label);
  renderHoldings(run.result.holdingsSnapshot);
  renderEvents(run.result.rebalanceEvents);
}

let backtestBusy = false;
async function runBacktest(event) {
  event.preventDefault();
  if (backtestBusy) return;

  if (isCompareMode()) {
    await runCompareBacktest();
    return;
  }

  const payload = buildPayload();
  backtestBusy = true;
  const unlock = lockControls(form, [...runModeButtons, addPositionButton, addScenarioButton]);
  statusText.textContent = "과거 데이터를 분석하고 있습니다…";

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

    resultsStale = false;
    resultViewMode = "single";
    lastCompareRuns = [];
    compareSummaryWrap.classList.add("hidden");
    scenarioDetailLabel.classList.add("hidden");
    summaryGrid.classList.remove("hidden");
    resultsHint.textContent = "설정한 조건으로 계산한 가상 투자 결과입니다.";

    renderSummary(body.summary);
    renderResultContext(body, "내 포트폴리오");
    renderHoldings(body.holdingsSnapshot);
    renderEvents(body.rebalanceEvents);
    currentInflationRatePct = Number(body.summary.inflationRatePct);
    chartSeries = {
      nominal: body.equityCurve,
      real: body.realEquityCurve,
    };
    renderActiveChart();
    statusText.textContent = `분석 완료 · 거래·자금 이벤트 ${body.summary.rebalanceCount}건`;
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    unlock();
    backtestBusy = false;
  }
}

async function runCompareBacktest(payload = buildComparePayload()) {
  if (backtestBusy) return;
  const emptyLabel = payload.runs.find((r) => !r.label);
  if (emptyLabel) {
    statusText.textContent = "모든 시나리오에 이름을 입력하세요.";
    return;
  }

  backtestBusy = true;
  const unlock = lockControls(form, [...runModeButtons, addPositionButton, addScenarioButton]);
  statusText.textContent = "동일한 조건으로 비교하고 있습니다…";

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

    resultsStale = false;
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
    resultsHint.textContent = "차트는 모든 조합을 비교합니다. 지표·구성·보유 내역은 선택한 조합의 결과입니다.";
    statusText.textContent = `비교 완료: ${lastCompareRuns.length}개 시나리오`;
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    unlock();
    backtestBusy = false;
  }
}

function wirePositionRow(row) {
  row.querySelector(".remove-position-btn").addEventListener("click", () => {
    if (positionsContainer.children.length === 1) {
      statusText.textContent = "최소 한 종목이 필요합니다.";
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
if (riskPeriodModeSelect) {
  riskPeriodModeSelect.addEventListener("change", toggleRiskPeriodFields);
}
if (riskWeightAInput && riskWeightBInput) {
  riskWeightAInput.addEventListener("input", () => syncRiskWeights(riskWeightAInput));
  riskWeightBInput.addEventListener("input", () => syncRiskWeights(riskWeightBInput));
}
rebalanceModeSelect.addEventListener("change", toggleRebalanceFields);
rsiSignalScopeSelect.addEventListener("change", toggleRsiScopeFields);
chartModeButtons.forEach((button) => {
  button.addEventListener("click", () => setChartMode(button.dataset.chartMode));
});
scenarioDetailSelect.addEventListener("change", () => {
  renderDetailForCompareIndex(Number(scenarioDetailSelect.value));
});
form.addEventListener("submit", runBacktest);
document.addEventListener("invalid", (event) => {
  let parent = event.target.parentElement;
  while (parent) {
    if (parent.tagName === "DETAILS") parent.open = true;
    parent = parent.parentElement;
  }
}, true);
form.addEventListener("input", () => { refreshAllocations(); markResultsStale(); });
form.addEventListener("change", syncInactiveFields);
document.querySelector(".form-panel").addEventListener("click", (event) => { refreshAllocations(); syncInactiveFields(); if (event.target.closest(".remove-position-btn, .add-scenario-position-btn, .remove-scenario-btn, #add-position-btn, #add-scenario-btn")) markResultsStale(); });
document.getElementById("benchmark-btn").addEventListener("click", () => {
  if (backtestBusy || !form.reportValidity()) return;
  const request = buildPayload();
  runCompareBacktest({runs: [
    {label: "내 포트폴리오", request},
    {label: "S&P 500 추종 ETF · SPY", request: {...request, positions: [{ticker: "SPY", targetWeight: 100, annualExpenseRatio: 0}], rebalance: {mode: "calendar", frequency: "yearly"}}},
  ]});
});
if (riskProfileForm) {
  riskProfileForm.addEventListener("submit", runRiskProfile);
}

addPositionRow("AAPL", 40);
addPositionRow("MSFT", 35);
addPositionRow("QQQ", 25);
togglePeriodFields();
if (riskPeriodModeSelect) {
  toggleRiskPeriodFields();
}
syncRsiTriggerTickerOptions();
toggleRebalanceFields();
applyRunModeUi();
syncChromeAfterRunModeChange();
setChartMode("nominal");

refreshAllocations();
syncInactiveFields();

let lastRiskProfile = null;
function resizeCharts() {
  [canvas, riskAllocationCanvas, riskScatterCanvas, riskNormalizedCanvas].forEach(chart => {
    const width = Math.round(chart.getBoundingClientRect().width);
    if (width > 0) {
      chart.width = width;
      chart.height = chart === canvas ? 230 : width < 500 ? 280 : 360;
    }
  });
  renderActiveChart();
  if (lastRiskProfile) renderRiskProfile(lastRiskProfile);
}
window.addEventListener("resize", resizeCharts);
document.getElementById("risk-analysis").addEventListener("toggle", resizeCharts);
resizeCharts();

document.querySelectorAll('a[href="#risk-analysis"], a[href="#getting-started"], a[href="#measurement-guide"]').forEach(link => {
  link.addEventListener("click", () => { document.querySelector(link.getAttribute("href")).open = true; });
});
