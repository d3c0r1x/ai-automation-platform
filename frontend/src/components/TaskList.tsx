import type { Task, TaskStatus } from "../api";

const LABELS: Record<TaskStatus, string> = {
  queued: "в очереди",
  planning: "планируется",
  running: "выполняется",
  waiting_approval: "ждёт подтверждения",
  done: "готово",
  failed: "ошибка",
  cancelled: "отменена",
};

export function TaskList({
  tasks,
  selectedId,
  onSelect,
}: {
  tasks: Task[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (tasks.length === 0) {
    return <p className="muted">Задач пока нет.</p>;
  }
  return (
    <ul className="tasks">
      {tasks.map((task) => (
        <li key={task.id} className={task.id === selectedId ? "task active" : "task"}>
          <button type="button" onClick={() => onSelect(task.id)}>
            <span className={`status ${task.status}`}>{LABELS[task.status]}</span>
            <span className="goal">{task.goal}</span>
            <span className="meta">
              {task.source} · {new Date(task.created_at).toLocaleString("ru-RU")}
              {task.plan ? ` · шагов: ${task.plan.steps.length}` : ""}
              {task.plan ? ` · план: ${task.plan.source === "llm" ? "модель" : "код"}` : ""}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
