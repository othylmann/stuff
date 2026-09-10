# Neonephos project KPIs

A one-page stats overview of the seven Neonephos Foundation projects: contributor
concentration, LFX health scores, development activity, licence and the commit
time zones behind each project.

**Live page:** https://othylmann.github.io/stuff/neonephos/KPIs/

## Files

| File | What it is |
|---|---|
| `index.html` | The report, served by GitHub Pages. Same content as the dated file below. |
| `20260910-neonephos-project-health.html` | Dated archive copy of the report. |
| `20260910-neonephos-data.json` | The underlying dataset, for re-analysis without re-querying. |

## Sources

- **LFX Insights API** (`insights.linuxfoundation.org/api`) — health scores, organisation and
  contributor leaderboards, commit and pull-request activity, licences. Covers 6 of the 7
  projects; time windows use the `startDate` / `endDate` parameters.
- **Git history and the GitHub API** — commit time zones (the GitHub API discards the author's
  UTC offset, so these come from cloned history across all 335 repositories), and every figure
  for Garden Linux, which is not a tracked LFX Insights project.

Figures are point-in-time as of 10 September 2026. Method notes and caveats are on the page
itself, under "How to read this".
