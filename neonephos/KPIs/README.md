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
| `20260910-neonephos-contributors.csv` | One row per contributor per project per window: person, GitHub account, organisation, aggregated organisation. |
| `neonephos-contributors.py` | Regenerates that CSV for any time frame and any set of projects. |

## Sources

- **LFX Insights API** (`insights.linuxfoundation.org/api`) — health scores, organisation and
  contributor leaderboards, commit and pull-request activity, licences. Covers 6 of the 7
  projects; time windows use the `startDate` / `endDate` parameters.
- **Git history and the GitHub API** — commit time zones (the GitHub API discards the author's
  UTC offset, so these come from cloned history across all 335 repositories), and every figure
  for Garden Linux, which is not a tracked LFX Insights project.

## Contributor export

The report exports contributors as CSV: the button beside the window switcher writes every
contributor in the selected window, and the numbers in the *People* column export a single
project. To regenerate outside the page, for any window:

```bash
python3 neonephos-contributors.py --all-windows -o contributors.csv
python3 neonephos-contributors.py --since 2026-01-01 --until 2026-04-01 --projects luigi,chantico
```

The person list and the contribution counts come from LFX, so they match the table on the page.
The organisation does not: LFX exposes no per-contributor affiliation in its API, so each person
is resolved from the e-mail domains in their commits, a work address they use elsewhere on
GitHub, their profile company, then public org membership. The `org_source` column records which
of those applied, and people none of them reach are listed as **Individual** — a floor on the
real company share, not a finding that the person is unaffiliated. Read the rows as inference,
not as employment records.

Figures are point-in-time as of 10 September 2026. Method notes and caveats are on the page
itself, under "How to read this".
