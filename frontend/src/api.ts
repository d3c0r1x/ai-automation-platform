/** Тонкий клиент API: те же эндпоинты, что описаны в docs/DEVELOPMENT.md. */

export type TaskStatus =
  | "queued"
  | "planning"
  | "running"
  | "waiting_approval"
  | "done"
  | "failed"
  | "cancelled";

export interface PlanStep {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  reason: string;
  depends_on: string[];
  requires_approval: boolean;
  optional: boolean;
}

export interface Plan {
  goal: string;
  steps: PlanStep[];
  expected_output: string;
  source: "llm" | "deterministic";
  notes: string;
}

export interface StepResult {
  step_id: string;
  tool: string;
  ok: boolean;
  attempts: number;
  duration_ms: number;
  output: Record<string, unknown>;
  error: string | null;
}

export interface TaskResult {
  summary: string;
  table_markdown: string;
  rows: Record<string, unknown>[];
  artifacts: Record<string, string>;
  data: Record<string, unknown>;
  delivery: Record<string, unknown>;
}

export interface Task {
  id: string;
  goal: string;
  source: string;
  status: TaskStatus;
  plan: Plan | null;
  result: TaskResult | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskEvent {
  task_id: string;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
  at: string;
  seq: number;
}

export interface TaskView {
  task: Task;
  steps: StepResult[];
  events: TaskEvent[];
}

const BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail.slice(0, 300)}`);
  }
  return (await response.json()) as T;
}

export const api = {
  listTasks: (limit = 50) => request<{ count: number; tasks: Task[] }>(`/api/tasks?limit=${limit}`),
  getTask: (id: string) => request<TaskView>(`/api/tasks/${id}`),
  createTask: (goal: string, autoApprove: boolean) =>
    request<{ task_id: string }>("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ goal, source: "form", auto_approve: autoApprove }),
    }),
  approve: (id: string, stepId?: string) =>
    request<{ approved_step: string }>(`/api/tasks/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ step_id: stepId ?? null }),
    }),
  cancel: (id: string) => request<{ status: string }>(`/api/tasks/${id}/cancel`, { method: "POST" }),
  retry: (id: string) => request<{ status: string }>(`/api/tasks/${id}/retry`, { method: "POST" }),
  stats: () => request<{ tasks: number; by_status: Record<string, number>; steps: number }>("/api/stats"),
};

/**
 * Поток событий задачи. Штатный путь — SSE; если поток оборвался (прокси,
 * перезапуск API), подписка переезжает на опрос REST: прогресс важнее способа
 * его доставки.
 */
export function subscribe(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  onEnd: (status?: string) => void,
): () => void {
  let closed = false;
  const source = new EventSource(`${BASE}/api/tasks/${taskId}/events/stream`);
  const kinds = [
    "task_created",
    "plan_ready",
    "step_started",
    "step_finished",
    "step_failed",
    "approval_required",
    "approved",
    "cancelled",
    "task_finished",
  ];
  kinds.forEach((kind) =>
    source.addEventListener(kind, (event) => {
      onEvent(JSON.parse((event as MessageEvent).data) as TaskEvent);
    }),
  );
  source.addEventListener("stream_end", (event) => {
    const data = JSON.parse((event as MessageEvent).data) as { status?: string };
    source.close();
    if (!closed) onEnd(data.status);
  });
  source.onerror = () => {
    source.close();
    if (!closed) onEnd();
  };
  return () => {
    closed = true;
    source.close();
  };
}
