import "./screen-parts.css";
import type { Lang } from "@aira/contracts";
import { STRINGS } from "../i18n/strings";
import type { TaskCard as TaskCardModel, TaskTone } from "./screenState";
import { progressOf, type CountProgress } from "./view";

export type { TaskCardModel, TaskTone };

type Props = {
  task: TaskCardModel | null;
  lang: Lang;
  /** A live delivery count: draws a progress bar under the detail. */
  progress?: CountProgress | null;
  /** True while a confirm is waiting for a gesture: shows the yes/no hint. */
  confirmPending?: boolean;
};

// 24 x 24 stroke marks, so the state reads by shape as well as colour:
// in progress, done, needs a look, failed, and waiting for yes/no.
const MARK: Record<TaskTone | "confirm", string> = {
  neutral: "M6.5 12h.01M12 12h.01M17.5 12h.01",
  ok: "M5 12.5l4.5 4.5L19 7.5",
  warn: "M12 6v7.5M12 18h.01",
  danger: "M7 7l10 10M17 7L7 17",
  confirm: "M9.2 9.2a2.8 2.8 0 1 1 3.9 2.6c-.7.3-1.1.9-1.1 1.6v.4M12 18h.01",
};

/**
 * Bottom card for the current task, e.g. "Sale: 2 × Sakthi curd 1 L — ₹120",
 * "Delivery: 28 / 30 counted" or "Order sent to Sakthi vendor". Phrasing content only.
 */
export function TaskCard({ task, lang, progress, confirmPending = false }: Props) {
  if (!task) return null;
  const pct = progress ? progressOf(progress.counted, progress.expected) : null;
  return (
    <span class="task-card" data-tone={task.tone} data-confirm={confirmPending ? "true" : undefined}>
      <span class="task-card__mark">
        <svg class="task-card__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d={MARK[confirmPending ? "confirm" : task.tone]} />
        </svg>
      </span>
      <span class="task-card__body">
        <span class="task-card__title">{task.title}</span>
        <span class="task-card__detail">{task.detail}</span>
        {pct !== null ? (
          <span class="task-card__bar" aria-hidden="true">
            <span class="task-card__fill" style={{ "--progress": pct.toFixed(3) }} />
          </span>
        ) : null}
        {confirmPending ? <span class="task-card__hint">{STRINGS.confirmPending[lang]}</span> : null}
      </span>
    </span>
  );
}
