import { useEffect, useState } from "preact/hooks";
import type { AuditRow } from "../lib/selectors/audit";

export type ResolveEvidence = (uri: string) => Promise<string | null>;

function Evidence(props: { uri: string; resolve: ResolveEvidence }) {
  const [href, setHref] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    let alive = true;
    setHref(null);
    setDone(false);
    props
      .resolve(props.uri)
      .catch(() => null)
      .then((h) => {
        if (!alive) return;
        setHref(h);
        setDone(true);
      });
    return () => {
      alive = false;
    };
  }, [props.uri, props.resolve]);
  if (href) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        View evidence
      </a>
    );
  }
  return <span class="muted">{done ? "Kept private" : "Checking…"}</span>;
}

/**
 * What the owner, AIRA and the system did, newest first, with the evidence each step relied on.
 * limit: on the overview, show only the newest actions and link to the full trail.
 */
export function AuditPage(props: { rows: AuditRow[]; resolveEvidence: ResolveEvidence; limit?: number }) {
  const shown = props.limit !== undefined ? props.rows.slice(0, props.limit) : props.rows;
  return (
    <section class="card card--audit" aria-labelledby="audit-title">
      <h2 id="audit-title" class="card__title">Audit trail</h2>
      {props.rows.length === 0 ? (
        <p class="empty">No actions recorded yet.</p>
      ) : (
        <div class="table-wrap">
          <table class="table table--audit">
            <caption>Recent actions by the owner, AIRA and the system, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col" class="wide-only">Who</th>
                <th scope="col">Action</th>
                <th scope="col" class="wide-only">Outcome</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id}>
                  <td class="time">{r.timeText}</td>
                  <td class="wide-only">
                    <span class={`who who--${r.actorLabel.toLowerCase()}`}>{r.actorLabel}</span>
                  </td>
                  <td>
                    <span class="cell__main">{r.actionText}</span>
                    <span class="narrow-only muted">
                      By {r.actorLabel}. Outcome: {r.outcome}
                    </span>
                  </td>
                  <td class="wide-only">{r.outcome}</td>
                  <td>{r.evidenceUri ? <Evidence uri={r.evidenceUri} resolve={props.resolveEvidence} /> : <span class="muted">None</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {shown.length < props.rows.length && (
        <a class="more" href="#/audit">See all {props.rows.length} actions</a>
      )}
    </section>
  );
}
