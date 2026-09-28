import { useState } from "react";

const EXAMPLES = [
  "Найди 20 товаров категории наушники до 5000 ₽, сравни их, выбери 5 лучших по цене и отзывам, составь таблицу и отправь мне отчёт",
  "Найди 10 товаров категории кофемашина до 30000 ₽, выбери 3 лучших с капучинатором и сохрани csv",
  "Подбери 6 рюкзаков до 5000 ₽ для ноутбука, покажи страницу лучшего и пришли таблицу",
];

export function TaskForm({ onSubmit, busy }: { onSubmit: (goal: string, autoApprove: boolean) => void; busy: boolean }) {
  const [goal, setGoal] = useState(EXAMPLES[0]);
  const [autoApprove, setAutoApprove] = useState(false);

  return (
    <form
      className="form"
      onSubmit={(event) => {
        event.preventDefault();
        if (goal.trim().length >= 3) onSubmit(goal.trim(), autoApprove);
      }}
    >
      <label htmlFor="goal">Что нужно сделать</label>
      <textarea
        id="goal"
        value={goal}
        rows={4}
        onChange={(event) => setGoal(event.target.value)}
        placeholder="Опишите задачу обычными словами: что найти, по каким условиям, что сделать с результатом"
      />
      <div className="examples">
        {EXAMPLES.map((example) => (
          <button type="button" key={example} className="chip" onClick={() => setGoal(example)}>
            {example.slice(0, 38)}…
          </button>
        ))}
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} />
        Без подтверждения отправки (автоматический режим)
      </label>
      <button type="submit" disabled={busy || goal.trim().length < 3}>
        {busy ? "Отправляю…" : "Поставить задачу"}
      </button>
    </form>
  );
}
