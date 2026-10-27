import type { Profile, ServerInfo, TraceEntry } from "../types";

interface Props {
  examples: string[];
  onExample: (text: string) => void;
  busy: boolean;
  profile: Profile | null;
  trace: TraceEntry[];
  info: ServerInfo | null;
}

export function SidePanel({ examples, onExample, busy, profile, trace, info }: Props) {
  return (
    <aside className="side">
      <section className="examples">
        <h2>Try</h2>
        {examples.map((t) => (
          <button type="button" key={t} onClick={() => onExample(t)} disabled={busy}>
            {t}
          </button>
        ))}
      </section>

      <section>
        <h2>Profile (what the safety engine sees)</h2>
        {profile ? <ProfileView p={profile} /> : <p className="muted">-</p>}
      </section>

      <section className="grow">
        <h2>Last turn trace</h2>
        {trace.length ? (
          <ul className="trace">
            {trace.map((e, i) => (
              <TraceLine key={i} e={e} />
            ))}
          </ul>
        ) : (
          <p className="muted">-</p>
        )}
      </section>

      {info && (
        <footer className="muted">
          {info.backend === "claude-code" ? "Claude Code (setup-token)" : "Anthropic API"} · {info.agent_model} ·
          guards {info.guard_model}
        </footer>
      )}
    </aside>
  );
}

const show = (v: string[] | string | number | null) => (v === null ? "?" : Array.isArray(v) ? v.join(", ") || "none" : String(v));

function ProfileView({ p }: { p: Profile }) {
  const rows: [string, string][] = [
    ["Screening", p.screening_complete ? "complete" : "incomplete"],
    ["Risk factors", p.risk_factors.join(", ") || "none"],
    ["Age / sex", `${show(p.age)} / ${show(p.sex)}`],
    ["Pregnancy", show(p.pregnancy_status)],
    ["Conditions", show(p.conditions)],
    ["Medications", show(p.medications)],
    ["Allergies", show(p.allergies)],
  ];
  const unknown = Object.entries(p.unrecognised);
  return (
    <dl className="profile">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd className={k === "Screening" && !p.screening_complete ? "warn-text" : undefined}>{v}</dd>
        </div>
      ))}
      {unknown.length > 0 && (
        <div>
          <dt>Unrecognised</dt>
          <dd className="warn-text">{unknown.map(([k, v]) => `${k}: ${v.join(", ")}`).join("; ")}</dd>
        </div>
      )}
    </dl>
  );
}

function TraceLine({ e }: { e: TraceEntry }) {
  if ("guard" in e) {
    if (e.guard === "triage") {
      return <li className={e.route.startsWith("emergency") ? "bad" : "ok"}>triage: {e.route}</li>;
    }
    return (
      <li className={e.verdict === "revise" ? "bad" : "ok"}>
        review #{e.attempt}: {e.verdict}
        {e.problems.length > 0 && ` - ${e.problems.join("; ")}`}
      </li>
    );
  }
  return (
    <li className={e.error ? "bad" : undefined}>
      <code>{e.tool}</code>({JSON.stringify(e.input).slice(0, 80)}) → {e.summary}
    </li>
  );
}
