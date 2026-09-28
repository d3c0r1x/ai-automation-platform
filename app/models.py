from typing import Literal

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=5, max_length=2000)


class PlanStep(BaseModel):
    id: str
    tool: Literal["catalog_search", "browser_read", "rank_products", "build_report", "external_action"]
    description: str
    args: dict
    requires_approval: bool = False


class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]


class Task(BaseModel):
    id: str
    prompt: str
    status: Literal["planned", "queued", "running", "waiting_approval", "completed", "failed"]
    plan: Plan
    result: dict | None = None
