from calendar import monthrange
from datetime import date

from app.dashboard_schemas import (
    GoalProjectionRequest, GoalProjectionResponse, GoalScenario, ProjectionPoint,
)


def month_date(start: date, months: int) -> date:
    if months == 0:
        return start
    year, month = divmod(start.year * 12 + start.month - 1 + months, 12)
    return date(year, month + 1, monthrange(year, month + 1)[1])


def project_goal(request: GoalProjectionRequest, as_of: date | None = None) -> GoalProjectionResponse:
    start = as_of or date.today()
    horizon = 60 * 12
    scenarios = []
    for label, rate in [
        ("낮은 가정", max(-50, request.annualReturnPct - 3)),
        ("입력한 가정", request.annualReturnPct),
        ("높은 가정", min(50, request.annualReturnPct + 3)),
    ]:
        monthly_rate = (1 + rate / 100) ** (1 / 12) - 1
        value = request.initialAmount
        reached = 0 if value >= request.targetAmount else None
        points = [ProjectionPoint(date=start, value=round(value, 2))]
        for month in range(1, horizon + 1):
            value = value * (1 + monthly_rate) + request.monthlyContribution
            if reached is None and round(value, 2) >= request.targetAmount:
                reached = month
            points.append(ProjectionPoint(date=month_date(start, month), value=round(value, 2)))
        scenarios.append(GoalScenario(
            label=label, annualReturnPct=rate, monthsToGoal=reached,
            reachedDate=month_date(start, reached) if reached is not None else None, points=points,
        ))
    # Keep a common chart horizon; absence of attainment stays explicit at 60 years.
    end = max((s.monthsToGoal if s.monthsToGoal is not None else horizon) for s in scenarios)
    end = max(end, 12)
    for scenario in scenarios:
        scenario.points = [p for i, p in enumerate(scenario.points[:end + 1]) if i % 3 == 0 or i == end]
    return GoalProjectionResponse(asOf=start, horizonYears=60, scenarios=scenarios)
