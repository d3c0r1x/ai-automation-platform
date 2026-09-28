import { useCallback, useEffect, useState } from "react";
import { api, subscribe, type Task, type TaskEvent, type TaskView } from "./api";
import { TaskForm } from "./components/TaskForm";
import { TaskList } from "./components/TaskList";
import { TaskProgress } from "./components/TaskProgress";
import { ResultPanel } from "./components/ResultPanel";

/**
 * Один экран, три блока: постановка задачи, список задач и карточка выбранной.
 * Прогресс приходит потоком событий, поэтому видно не только «выполняется»,
 * но и какой именно шаг плана идёт сейчас.
 */
export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<TaskView | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<{ tasks: number; by_status: Record<string, number> } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [listing, counters] = await Promise.all([api.listTasks(), api.stats()]);
      setTasks(listing.tasks);
      setStats({ tasks: counters.tasks, by_status: counters.by_status });
    } catch (cause) {
      setError(String(cause));
    }
  }, []);

  const open = useCallback(async (taskId: string) => {
    const view = await api.getTask(taskId);
    setSelected(view);
    setEvents(view.events);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    const taskId = selected.task.id;
    const stop = subscribe(
      taskId,
      (event) => setEvents((current) => [...current, event]),
      (status) => {
        if (status && status !== "idle") void refresh();
      },
    );
    const poll = setInterval(() => void open(taskId), 4000);
    return () => {
      stop();
      clearInterval(poll);
    };
  }, [selected?.task.id, open, refresh]);

  const create = async (goal: string, autoApprove: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const { task_id } = await api.createTask(goal, autoApprove);
      await refresh();
      await open(task_id);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  const act = async (action: "approve" | "cancel" | "retry", taskId: string) => {
    setBusy(true);
    setError(null);
    try {
      if (action === "approve") await api.approve(taskId);
      if (action === "cancel") await api.cancel(taskId);
      if (action === "retry") await api.retry(taskId);
      await refresh();
      await open(taskId);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <header>
        <h1>AI Automation Platform</h1>
        <p className="subtitle">
          Задача → план агента → инструменты (API, браузер) → результат. Всё, что делает
          агент, видно по шагам; отправка наружу ждёт подтверждения человека.
        </p>
        {stats && (
          <p className="counters">
            задач: {stats.tasks} · готово: {stats.by_status.done ?? 0} · ошибок:{" "}
            {stats.by_status.failed ?? 0} · ждут подтверждения: {stats.by_status.waiting_approval ?? 0}
          </p>
        )}
      </header>

      {error && <div className="error">Ошибка: {error}</div>}

      <main>
        <section className="panel">
          <TaskForm onSubmit={create} busy={busy} />
          <TaskList tasks={tasks} selectedId={selected?.task.id ?? null} onSelect={open} />
        </section>

        <section className="panel wide">
          {selected ? (
            <>
              <TaskProgress
                view={selected}
                events={events}
                busy={busy}
                onApprove={() => act("approve", selected.task.id)}
                onCancel={() => act("cancel", selected.task.id)}
                onRetry={() => act("retry", selected.task.id)}
              />
              <ResultPanel view={selected} />
            </>
          ) : (
            <p className="muted">
              Выберите задачу слева или поставьте новую: платформа построит план, покажет шаги
              и вернёт таблицу с результатом.
            </p>
          )}
        </section>
      </main>

      <footer>
        Демо-режим работает без ключей LLM и без внешних API: инструменты используют
        встроенный каталог, а источник данных всегда указан в результате.
      </footer>
    </div>
  );
}
