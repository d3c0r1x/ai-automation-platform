from .models import Plan, PlanStep


def make_plan(prompt: str) -> Plan:
    # Deterministic portfolio/demo planner. It mirrors the interface an LLM
    # planner would implement and keeps the demo reproducible without keys.
    return Plan(
        goal=prompt,
        steps=[
            PlanStep(
                id="search",
                tool="catalog_search",
                description="Search the deterministic product catalog",
                args={"query": prompt},
            ),
            PlanStep(
                id="read",
                tool="browser_read",
                description="Read a safe browser page for additional context",
                args={"url": "https://example.com"},
            ),
            PlanStep(
                id="rank",
                tool="rank_products",
                description="Rank the collected products against the request",
                args={"limit": 5},
            ),
            PlanStep(
                id="report",
                tool="build_report",
                description="Build a structured final report",
                args={},
            ),
        ],
    )
