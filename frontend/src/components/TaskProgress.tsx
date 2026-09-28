import type { TaskEvent, TaskView } from "../api";

/**
 * План и ход его выполнения.
 *
 * Ключевая мысль: человек должен видеть не «агент думает», а конкретный список
 * шагов, что уже сделано, что делается сейчас и что ждёт подтверждения.
 */
export function TaskProgress({
  view,
  events,
  busy,
  onApprove,
  onCancel,
  onRetry,
}: {
  view: TaskView;
  events: TaskEvent[];
  busy: boolean;
  onApprove: () => void;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const { task, steps } = view;
  const runningStep = events.filter((e) => e.kind === "step_started").at(-1)?.payload?.step_id as string | undefined;
  const waiting = events.filter((e) => e.kind === "approval_required").at(-1)?.payload?.step_id as
    | string
    | undefined;

  return (
    <div className="progress">
      <div className="head">
        <h2>{task.goal}</h2>
        <span className={`status ${task.status}`}>{task.status}</span>
      </div>

      <div className="actions">
        {task.status === "waiting_approval" && (
          <button type="button" className="primary" onClick={onApprove} disabled={busy}>
            Подтвердить отправку
          </button>
        )}
        {["failed", "cancelled"].includes(task.status) && (
          <button type="button" onClick={onRetry} disabled={busy}>
            Запустить заново
          </button>
        )}
        {!["done", "failed", "cancelled"].includes(task.status) && (
          <button type="button" onClick={onCancel} disabled={busy}>
            Отменить
          </button>
        )}
      </div>

      {task.error && <div className="error">Ошибка задачи: {task.error}</div>}

      {task.plan && (
        <>
          <p className="muted">
            План: {task.plan.source === "llm" ? "построен моделью" : "построен кодом"} ·{" "}
            {task.plan.notes}
          </p>
          <ol className="steps">
            {task.plan.steps.map((step) => {
              const done = steps.find((s) => s.step_id === step.id && s.ok);
              const failed = steps.find((s) => s.step_id === step.id && !s.ok);
              const state = failed ? "failed" : done ? "done" : step.id === runningStep ? "running" : "pending";
              return (
                <li key={step.id} className={`step ${state}`}>
                  <div className="row">
                    <code>{step.tool}</code>
                    {step.requires_approval && <span className="gate">подтверждение</span>}
                    {step.optional && <span className="gate">необязательный</span>}
                    {done && (
                      <span className="timing">
                        {done.duration_ms} мс{done.attempts > 1 ? ` · попыток: ${done.attempts}` : ""}
                      </span>
                    )}
                  </div>
                  <p>{step.reason}</p>
                  {failed?.error && <p className="error">{failed.error}</p>}
                </li>
              );
            })}
          </ol>
        </>
      )}

      {waiting && task.status === "waiting_approval" && (
        <p className="notice">
          Шаг <code>{waiting}</code> отправляет результат наружу — платформа ждёт вашего решения.
        </p>
      )}

      {events.length > 0 && (
        <details className="events" open={task.status !== "done"}>
          <summary>Журнал событий ({events.length})</summary>
          <ul>
            {events.slice(-40).map((event) => (
              <li key={event.seq}>
                <span className="seq">{event.seq}</span>
                <code>{event.kind}</code>
                <span>{event.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
