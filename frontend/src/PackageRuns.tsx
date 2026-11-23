/**
 * What each package run made for a Work item took and how well it went
 * (run_report.py, work-list.md item 38), on the item's Overview: per
 * package and run, each step's seconds (a step over a few minutes marked
 * too slow), its calls, pages, retries, limits and tokens; then the
 * quality report's passes, repeats, his reader's questions, facts sourced
 * and his verdict. Read from `GET /api/work/{id}/package-runs`, which the
 * brain's `package_run_reports` reads too.
 */
import { useEffect, useState } from "react";

import { getJson } from "./fetchJson";
import { formatNumber, useT } from "./locale";

interface Step {
  step: string; seconds: number; too_slow: boolean; calls: unknown[]; pages_asked: number;
  retries: number; time_limits: number; rate_limited: number; app_server_starts: number;
  tokens: { in: number; cached: number; out: number };
}
interface Quality {
  result?: string; passes_to_done?: number; repeated?: number; fixed?: number;
  readers_questions?: { raised: number; answered: number };
  facts?: { checked?: number; unanswered?: number };
  verdict?: { answer: string; labels?: string[] | null } | null;
}
interface Run { job: string; run: string; state: string; timing: { steps: Step[] } | null;
  quality: Quality | null }
interface Pack { package: string; runs: Run[];
  summed: { timing: { seconds: number; calls: number; tokens: { in: number; out: number } } } }

export function PackageRuns({ itemId }: { itemId: string }) {
  const t = useT();
  const [found, setFound] = useState<Pack[] | null>(null);
  useEffect(() => {
    let live = true;
    getJson<{ packages: Pack[] }>(`/api/work/${encodeURIComponent(itemId)}/package-runs`)
      .then((r) => { if (live) setFound(r.packages); })
      .catch(() => { if (live) setFound([]); });
    return () => { live = false; };
  }, [itemId]);
  if (!found || found.length === 0) return null;
  return (
    <section className="work-package-runs">
      <h3 className="work-section-head">{t("main.work.runs.head")}</h3>
      {found.map((p) => (
        <div key={p.package}>
          <p>{t("main.work.runs.package", {
            id: p.package, runs: p.runs.length, seconds: formatNumber(p.summed.timing.seconds),
            calls: p.summed.timing.calls, tin: formatNumber(p.summed.timing.tokens.in),
            tout: formatNumber(p.summed.timing.tokens.out) })}</p>
          {p.runs.map((r) => (
            <div key={r.job}>
              <p className="work-path">{t("main.work.runs.run", { run: r.run, state: r.state })}</p>
              <ul>
                {(r.timing?.steps ?? []).map((s, n) => (
                  <li key={`${s.step}-${n}`} data-too-slow={s.too_slow ? "true" : undefined}>
                    {t("main.work.runs.step", {
                      step: s.step, seconds: formatNumber(s.seconds), calls: s.calls.length,
                      pages: s.pages_asked, retries: s.retries, limits: s.time_limits,
                      rate: s.rate_limited, servers: s.app_server_starts,
                      tin: formatNumber(s.tokens.in), cached: formatNumber(s.tokens.cached),
                      tout: formatNumber(s.tokens.out) })}
                    {s.too_slow && <strong> {t("main.work.runs.tooSlow")}</strong>}
                  </li>
                ))}
              </ul>
              {r.quality && <p>{t("main.work.runs.quality", {
                result: r.quality.result ?? "", passes: r.quality.passes_to_done ?? 0,
                repeated: r.quality.repeated ?? 0, fixed: r.quality.fixed ?? 0,
                raised: r.quality.readers_questions?.raised ?? 0,
                answered: r.quality.readers_questions?.answered ?? 0,
                sourced: r.quality.facts?.checked ?? 0,
                unanswered: r.quality.facts?.unanswered ?? 0 })}</p>}
              {r.quality?.verdict && <p>{t("main.work.runs.verdict", {
                answer: r.quality.verdict.answer,
                labels: (r.quality.verdict.labels ?? []).join(", ") })}</p>}
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}
