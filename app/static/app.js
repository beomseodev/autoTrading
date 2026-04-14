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
const summaryGrid = document.getElementById("summary-grid");
const holdingsBody = document.getElementById("holdings-body");
const eventsBody = document.getElementById("events-body");
const canvas = document.getElementById("equity-chart");
const chartRange = document.getElementById("chart-range");
const canvasContext = canvas.getContext("2d");

function addPositionRow(ticker = "", targetWeight = "") {
  const fragment = positionTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".position-row");
  row.querySelector('input[name="ticker"]').value = ticker;
  row.querySelector('input[name="targetWeight"]').value = targetWeight;

  row.querySelector(".remove-position-btn").addEventListener("click", () => {
    if (positionsContainer.children.length === 1) {
      statusText.textContent = "포지션은 최소 1개 이상 필요합니다.";
      return;
    }
    row.remove();
  });

  positionsContainer.appendChild(fragment);
}

function togglePeriodFields() {
  const isRange = periodModeSelect.value === "range";
  periodRangeFields.classList.toggle("hidden", !isRange);
  periodLookbackFields.classList.toggle("hidden", isRange);
}

function toggleRebalanceFields() {
  const isCalendar = rebalanceModeSelect.value === "calendar";
  frequencyField.classList.toggle("hidden", !isCalendar);
  rsiFields.classList.toggle("hidden", isCalendar);
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
    ["초기 투자금", currency(summary.initialCapital)],
    ["실투입 금액", currency(summary.deployedCapital)],
    ["최종 금액", currency(summary.finalValue)],
    ["총수익률", percent(summary.totalReturnPct)],
    ["CAGR", percent(summary.cagrPct)],
    ["MDD", percent(summary.mddPct)],
  ];

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
    holdingsBody.innerHTML = '<tr><td colspan="4" class="empty-row">보유 데이터가 없습니다.</td></tr>';
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
    eventsBody.innerHTML = '<tr><td colspan="3" class="empty-row">추가 리밸런싱 이벤트가 없습니다.</td></tr>';
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
    canvasContext.fillText("차트 데이터가 없습니다.", 36, 60);
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
        };

  return {
    positions,
    initialCapital: Number(formData.get("initialCapital")),
    period,
    rebalance,
    execution: {
      fractionalShares: formData.get("fractionalShares") === "on",
      feeRate: Number(formData.get("feeRate")),
      slippageRate: Number(formData.get("slippageRate")),
    },
  };
}

async function runBacktest(event) {
  event.preventDefault();

  const payload = buildPayload();
  statusText.textContent = "백테스트를 실행하고 있습니다...";

  try {
    const response = await fetch("/api/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || "백테스트 실행 중 오류가 발생했습니다.");
    }

    renderSummary(body.summary);
    renderHoldings(body.holdingsSnapshot);
    renderEvents(body.rebalanceEvents);
    drawChart(body.equityCurve);
    statusText.textContent = `완료: 리밸런싱 ${body.summary.rebalanceCount}회`;
  } catch (error) {
    statusText.textContent = error.message;
  }
}

addPositionButton.addEventListener("click", () => addPositionRow("", ""));
periodModeSelect.addEventListener("change", togglePeriodFields);
rebalanceModeSelect.addEventListener("change", toggleRebalanceFields);
form.addEventListener("submit", runBacktest);

addPositionRow("AAPL", 40);
addPositionRow("MSFT", 35);
addPositionRow("QQQ", 25);
togglePeriodFields();
toggleRebalanceFields();
drawChart([]);
