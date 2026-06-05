---
name: analyze
description: Pull recent Garmin data, dispatch three domain-expert subagents in parallel, synthesize their outputs, and render an analysis.html dashboard. The core peak-lab workflow.
---

# peak-lab analyze

You are orchestrating a multi-agent training analysis. The workflow has four phases: **fetch → experts → synthesize → render**. Do not skip phases. Do not prescribe workouts yourself — delegate interpretation to the subagents.

## Phase 0: Preconditions

**peak-lab works in the current directory. That is the training project. No env vars, no overrides.** Before launching, the user should have `cd`'d into their training-project folder (the one with `profile.md` and `runs/`). If they ran `/peak-lab:analyze` from anywhere else, the right fix is for them to `cd` and retry — not for us to go hunting for their data.

Sanity-check the cwd in a single Bash call and capture the timestamp + prior-run pointer you'll need for later phases:

```
test -f profile.md || { echo "MISSING profile.md in $(pwd) — cd into your training project, or run /peak-lab:setup first"; exit 1; }
test ! -d .claude-plugin || { echo "ERROR: $(pwd) is the plugin source directory — cd into a separate training project folder"; exit 1; }
date -u +%Y%m%dT%H%M%SZ
readlink -f runs/latest/analysis.json 2>/dev/null || true
```

Remember from that output:

- **`TS`** = the first line (the UTC timestamp). Use it to build `RUN_DIR = runs/<TS>` (relative to cwd).
- **`PREV_ANALYSIS`** = the second line if non-empty. It's the **concrete dereferenced target** of `runs/latest/analysis.json`, captured now so Phase 4's symlink flip (and any concurrent run) cannot affect what Phase 3 reads. Empty means first run.
- **`WINDOW`** = `${CLAUDE_PLUGIN_OPTION_ANALYSIS_WINDOW_DAYS:-60}` (Claude Code delivers the plugin config via this env var; default 60).

Inline the literal values into every subsequent Bash call — each call is a fresh shell, so you cannot rely on exported variables carrying across.

## Phase 0.5: Note time-of-day context

Before Phase 1, compute and remember two facts — they shape how every "today" recommendation must be framed later:

1. **Current local time, classified.** Run `date` and classify into exactly one of: `morning` (before 11:00 local), `midday` (11:00–16:00), `evening` (after 16:00). This classification is non-optional — the executive summary headline in Phase 3 **must** open with the matching frame ("This morning…", "Mid-day check-in…", "By end of day…") so the reader immediately knows which horizon the recommendations apply to.
2. **Whether the athlete has already logged any activity today.** After Phase 1 pulls `raw.json`, scan `activities[]` for **every** entry whose `startTimeLocal` / `startTimeGMT` falls on the current date — running, cycling, strength, hiking, swimming, walking, anything. For each, capture: sport type (`activityType.typeKey`), Garmin's training-effect label (running uses easy/tempo/threshold/VO2/long; cycling uses similar aerobic-intensity zones; strength reports anaerobic / muscular load; non-aerobic sports may have no label and that's fine), duration, distance if relevant, avg HR if relevant. These sessions are already done — they cannot be un-prescribed and they're already reflected in today's TSB / readiness / recovery-time numbers from Garmin and in the per-sport totals in `training-metrics.json`.

Pass both facts into each expert's prompt in Phase 2, and incorporate them into the Phase 3 synthesis. The "today" recommendation horizon means **the remainder of today**, not "your entire day starting from morning."

## Phase 1: Fetch Garmin data

Shell out to the bundled fetcher via Bash. Substitute the `WINDOW` and `TS` values you captured in Phase 0. Always pass `--prev` pointing at `runs/latest/raw.json` — if that file does not exist (first run, or `runs/latest` missing), the fetcher silently falls back to a full fetch, so no conditional is needed. All paths are relative to the cwd from Phase 0:

```
"${CLAUDE_PLUGIN_ROOT}/bin/fetch-garmin" --days <WINDOW_VALUE> --prev runs/latest/raw.json --out runs/<TS_VALUE>/raw.json
```

Expect 1–3 minutes on a cold run (first time, or when `--prev` is absent / `--days` expanded past the prior window — uv also resolves garminconnect + curl_cffi deps on first run and then caches them). A warm daily run reuses closed historical days from the previous `raw.json` and only refetches a rolling tail (default 3 days) plus always-rolling blocks (plans, scheduled workouts, readiness, status, PRs, predictions, body battery, weigh-ins) — target ~15–30 seconds.

If you need to force a clean pull (e.g., suspect a Garmin-side retroactive edit outside the tail window), drop `--prev` or add `--no-incremental`. The output JSON carries a `fetch_mode` field (`"full"` or `"incremental"`) and a `data_sources` block with per-field refetched/reused date lists — the subagents can inspect it if a signal looks suspicious.

### Compute training metrics

Immediately after the fetch succeeds (same Phase 1, separate Bash call), derive the deterministic load metrics from `raw.json`. **This is non-optional.** All three experts read these precomputed numbers — they must not reimplement EWMA/ACWR math in fresh inline Python.

```
"${CLAUDE_PLUGIN_ROOT}/bin/training-metrics" \
  runs/<TS_VALUE>/raw.json \
  --out runs/<TS_VALUE>/training-metrics.json
```

`bin/training-metrics` is a stdlib-only script that aggregates `activities[]` **sport-agnostically** (running + cycling + strength + hiking + swimming + walking + … all count as load) and emits per-day CTL/ATL/TSB/ACWR/ramp/monotony/strain plus Monday-start weekly rollups grouped by sport. It also emits **per-sport rolling load metrics** (`per_day[<date>].by_sport.<sport>.{load,ctl,atl,tsb}`), a `sport_composition` block with 7d/28d/60d load fractions per sport, and a `sport_shift` flag indicating whether recent sport mix has diverged materially (>15 percentage points) from the 28-day baseline (the 28d window catches recent transitions; a longer baseline would dilute multi-week sport blocks). Output schema is documented in the script's docstring. Expect <2 seconds runtime; failure is unusual (only causes: malformed JSON, missing `activities` key) and means stop and surface the error.

A `ctl_warm` boolean on each per-day entry tells you whether CTL has had enough history to saturate (~126 days). For typical 60-day windows it stays `false`, which is a feature: the experts should treat absolute CTL/TSB values as biased low and rely on the trend.

`bin/fetch-garmin` is a self-contained Python script (PEP 723 inline deps via `uv run`). It reads OAuth tokens from `~/.garminconnect/garmin_tokens.json` and writes one JSON file containing:

**Per-day health & activity data over the window:**
- `activities` — activity summaries in the date range (`get_activities_by_date`)
- `stats` — daily stats per day (`get_stats`)
- `sleep` — nightly sleep per day (`get_sleep_data`)
- `hrv` — overnight HRV per day (`get_hrv_data`)
- `training_status` — training status + load per day (`get_training_status`)
- `training_readiness` — daily readiness per day (`get_training_readiness`)
- `heart_rates` — resting HR per day (`get_heart_rates`)
- `body_battery` — body battery, fetched in 7-day chunks to avoid a Garmin API range limit
- `weigh_ins` — body weight in the range (may be empty)

**Plan, PR, and prediction context (pulled fresh every run):**
- `training_plans` — list of active training plans (Garmin Coach or manual)
- `adaptive_plan_details` — for each plan, the full `taskList` with every scheduled workout (past = `COMPLETED`, future = `NOT_COMPLETE`), including workout name, description with prescribed paces, estimated duration, training effect label
- `scheduled_workouts` — 2 months back + 2 months forward of scheduled workouts grouped by year-month, so past plan adherence and upcoming block are both visible
- `personal_records` — athlete's PRs
- `race_predictions` — Garmin's current predicted times for 5K/10K/HM/M

Per-endpoint failures are captured in `fetch_errors` — the experts must handle partial data. If the script exits non-zero, the most likely cause is missing/expired tokens — tell the user to run `bin/garmin-auth` and stop.

### Optional: wider window for the HR/pace-vs-lab-zones section

If — and only if — the training project supplies `data/lab-zones.json` (the athlete's lab training zones + ramp table), the renderer in Phase 4 appends a "Heart rate vs pace, over your lab training zones" chart. That chart wants a full ~3 months of history regardless of the analysis window, so pull a dedicated 90-day raw alongside the main fetch:

```
test -f data/lab-zones.json && \
  "${CLAUDE_PLUGIN_ROOT}/bin/fetch-garmin" --days 90 --prev runs/latest/raw.json --out runs/<TS_VALUE>/raw-90d.json \
  || true
```

Skip this entirely when `data/lab-zones.json` is absent (the common case) — no lab-zones file means no chart, and the renderer simply omits the section. The 90-day fetch reuses the just-fetched `runs/latest/raw.json` as its `--prev`, so it is mostly incremental and fast. A non-zero exit here is non-fatal: the rest of the analysis still renders, just without the lab-zones chart.

## Phase 2: Dispatch three experts in parallel

Use the Task tool with `subagent_type` values **`peak-lab:metrics-expert`**, **`peak-lab:activity-expert`**, **`peak-lab:physiology-expert`** — plugin-loaded subagents are always namespaced with their plugin name. **Dispatch all three in a single message with three tool calls** so they run concurrently.

Each expert has access to `bin/garmin-call` via Bash and can pull additional data on its own if the bulk `raw.json` is insufficient for a specific question. You do not need to re-dispatch them to supply ad-hoc data — the dispatcher enforces a read-only method whitelist, so experts can drill down safely.

**Critical path-resolution rule.** Subagents run in their own isolated contexts. When you build each expert's prompt, inline **literal absolute paths** — no `${CLAUDE_PLUGIN_ROOT}` or other placeholders left in the dispatched text. The plugin root comes from Claude Code's runtime env; the training-project root is just the current working directory from Phase 0 (`$(pwd)` captured there).

Each subagent receives in the prompt:

1. The absolute path to `raw.json` — substitute `$(pwd)/runs/<TS>/raw.json` with the cwd and `TS` from Phase 0
2. The absolute path to `training-metrics.json` — `$(pwd)/runs/<TS>/training-metrics.json`. **This is the authoritative source for CTL/ATL/TSB/ACWR/ramp/monotony/strain and for per-sport weekly rollups.** Experts read these numbers; they do not recompute them.
3. The absolute path to `profile.md` — `$(pwd)/profile.md`, same substitution
4. The analysis window size
5. Today's UTC date and the time-of-day context from Phase 0.5 (was there a session already logged today? when is now in the user's day?)
6. **A reference block listing the absolute paths** they will need:
   - `${CLAUDE_PLUGIN_ROOT}/bin/garmin-call` — read-only Garmin dispatcher (run with `--list` to enumerate methods)
   - `${CLAUDE_PLUGIN_ROOT}/bin/vdot` — Daniels VDOT calculator
   - `${CLAUDE_PLUGIN_ROOT}/skills/garmin/SKILL.md` — method catalog, VDOT training-zone reference, shared expert output conventions
   - `$(pwd)/runs/` — every analyze run lives here as `runs/<TIMESTAMP>/`. `runs/latest/{raw,analysis,training-metrics}.json` is the most recent analyze run. Experts can compare against historical runs with `Read` / `Grep` when a "has this changed over the last month" question is relevant.

   All `${...}` and `$(...)` placeholders must be substituted with literal absolute paths before the prompt is dispatched.

Each subagent must return JSON with exactly these two keys:

```json
{
  "for_synthesis": "...",
  "for_weekly_planner": "..."
}
```

plus an optional `signals` array where each signal is `{ "signal": "...", "evidence": "...", "implication": "...", "uncertainty": "..." }`.

If any subagent returns malformed JSON, surface the error and stop — do not silently patch it.

## Phase 3: Synthesize

You (the main thread) now integrate the three expert outputs. Write the synthesis as a single JSON object to `<RUN_DIR_VALUE>/analysis.json` (use the `RUN_DIR` from Phase 0).

### Since-last-run delta (skip on first run)

If `PREV_ANALYSIS` from Phase 0 points at a file that exists, read it and compute what changed between that prior analysis and the one you are about to write. The point is to make recurring runs feel like a conversation — if you fire twice daily, the second read of the day should immediately tell the user *what's new* rather than repeating the first read verbatim.

Compute four things by comparing the prior `analysis.json` to your in-progress synthesis:

1. **Physiology traffic-light transition** — did `for_weekly_planner` flip GREEN ↔ AMBER ↔ RED?
2. **New signals** — signals appearing in this run's expert outputs that weren't in the prior run. Match loosely by signal label; small rewordings of the same observation don't count as new.
3. **Resolved signals** — signals that were in the prior run but are absent now, implying the situation has cleared.
4. **Activity deltas** — any new activity logged since the prior run's `generated_at`, by scanning `raw.json.activities[]` for entries with `startTimeLocal` after that timestamp.

Embed the result as a top-level `since_last_run` object using the schema below. Keep it **short** — this is a preamble, not a second synthesis. If nothing meaningful changed, the `headline` should say so and `bullets` should be empty or a single "no material change" line. If `PREV_ANALYSIS` is missing (first run), omit the field entirely or set it to `null` — do not fabricate a prior.

### Plan continuity (carry forward locked recommendations)

The prior `analysis.json` is not just a delta-detection input — it is the **default source for recommendations**. Each analyze run is one step in a continuous conversation, not a fresh synthesis from zero. The athlete and the assistant have already agreed to specific upcoming sessions, rest days, race tactics, and post-race resumption plans; those decisions must persist across runs unless something new actively overrides them.

After computing the `since_last_run` delta, do the following before writing `recommendations[]`:

1. **Read `PREV_ANALYSIS.recommendations[]` in full** (all four horizons: today, tomorrow, this_week, next_4_weeks).
2. **Treat each prior recommendation as the default for this run's same horizon.** Carry forward the prescribed sessions, rest days, paces, and race-week structure verbatim unless one of the override conditions below applies.
3. **Override conditions** (the only reasons to deviate):
   - **Physiology forces it** — an expert's `for_weekly_planner` traffic light worsened, or a signal in this run's expert outputs flags a load/recovery issue that the prior run did not see.
   - **Calendar has advanced** — today's horizon means a session already happened (collapse it into `today_logged`), and "tomorrow" becomes what was previously "day after tomorrow."
   - **The athlete spoke up between runs** — if conversation context or a new memory entry contradicts the prior plan, the new instruction wins.
   - **Experts surfaced new evidence** — e.g. a lab test result, a verified race time, or a recalibrated zone that changes pace prescriptions.
4. **When you do deviate, document it explicitly** in `since_last_run.new_signals` or `since_last_run.headline` with the reason. Silent drift is the failure mode this rule exists to prevent.
5. **Do not let bottom-up expert prescriptions overwrite a locked plan.** Experts emit `for_weekly_planner` text that suggests sessions, but they do not see the prior analysis. If an expert prescribes something that contradicts the locked plan (e.g. "run the tempo on a day" the locked plan has marked as rest), the locked plan wins unless an override condition applies. The expert's view becomes a candidate revision, not an automatic replacement.

This rule applies to every horizon, but it is most load-bearing for `this_week` and `next_4_weeks` — those carry race-week structure that should remain stable across daily check-ins.

### Plain-language mandate for all user-facing text

Every text field in `analysis.json` is rendered directly to a non-technical runner on the analysis dashboard. These rules are non-negotiable:

- **Never put in output text:** API field names (`lastNightAvg`, `dailyTrainingLoadAcute`, `hrvSummary.weeklyAvg`), function or method names (`get_activity_*`, any `get_*`), Garmin enum strings (`LACTATE_THRESHOLD`, `AEROBIC_LOW_SHORTAGE`, `FORCED_REST`, `MAINTAINING_2`, `OPTIMAL`, `VERY_GOOD`), clinical/anatomical jargon (e.g. "valgus/varus" → "knees drifting inward/outward"; honor any preferred plain wording the profile specifies), or academic jargon without a plain-language alternative.
- **Prefer plain-language vocabulary:** "Fitness" not CTL, "fatigue" not ATL, "form" not TSB, "load strain ratio" not ACWR, "heart rate variability" / "overnight recovery signal" not HRV in prose, "resting heart rate" not RHR, "blood oxygen" not SpO2, "easy/marathon/threshold/interval/repetition pace" not E/M/T/I/R codes in prose.
- Abbreviations belong in KPI `abbr` tooltip badges, NOT in narrative prose.
- The renderer has a safety net that humanizes enum strings and camelCase field names, but that's defense-in-depth — write cleanly at the source.

If an expert's output contains forbidden content (a code reference, an enum string, or "valgus"), rewrite it into plain language before embedding it into the analysis.json fields below.

### Sport-shift handling

Read `training-metrics.json.sport_shift.shifted`. When it's `false`, business as usual — multi-sport CTL/ATL/TSB is the right lens and nothing extra is required.

When it's `true`, the recent sport mix has diverged materially from the 60-day baseline, so multi-sport form no longer cleanly represents race-specific fitness (carried fatigue may live in a sport that didn't physically tax the race-day system). The synthesis MUST:

a) **Surface the shift.** If the shift is newly true vs. `PREV_ANALYSIS`, add a short label to `since_last_run.new_signals`. If it's persistent and material to the current block, add a bullet to `executive_summary.whats_concerning` (or `whats_working` if the shift is the intended taper into race-sport). Phrase in plain language — e.g. "sport balance has shifted toward running over the last week" — never "sport_composition" or "sport_shift" in prose.

b) **Add a race-sport-specific fitness KPI alongside the multi-sport one.** Source the value from `per_day[<today>].by_sport.<race_sport>.ctl`. Label it plainly, e.g. `"Running fitness"` with `abbr: "CTL"`, next to the existing multi-sport `"Fitness"` KPI. The `source` field should say something like "Calculated from running-only sessions over the window." Same treatment for race-sport-specific form (TSB) if it tells a different story than multi-sport form.

c) **Flag the projection caveat.** When `recommendations` discusses race-day form, freshness, or taper trajectory, mention that the projection uses multi-sport form and may overstate race-specific freshness because some carried fatigue is in a non-race sport. Don't attempt a conversion or correction — just flag it so the athlete reads the number with the right calibration.

### Schema

```json
{
  "generated_at": "<ISO8601>",
  "window_days": <integer, e.g. 60>,
  "since_last_run": {
    "prior_run_at": "<ISO8601 of the prior analysis's generated_at, or null if first run>",
    "hours_since": <float, e.g. 12.3>,
    "headline": "ONE sentence on what's different since the last run. 15-25 words. E.g. 'Since this morning, HRV dropped 8ms and physiology flipped from GREEN to AMBER; one new threshold session logged.' If nothing material changed, say so plainly.",
    "physiology_transition": "green_to_amber | amber_to_green | amber_to_red | red_to_amber | green_to_red | red_to_green | unchanged",
    "new_signals": ["Short one-line labels of signals that weren't in the prior run. 0-3 items."],
    "resolved_signals": ["Short one-line labels of prior signals no longer present. 0-3 items."],
    "new_activities": ["'YYYY-MM-DD easy run 45min' style one-liners for sessions logged since the prior run's generated_at. 0-3 items."]
  },
  "athlete": {
    "profile_excerpt": "Short plain-English description of the athlete, goals, and constraints."
  },
  "plan": {
    "name": "Plan name (plain)",
    "source": "Where the plan comes from (e.g. 'Garmin Coach adaptive running plan')",
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD",
    "current_week": "N of M",
    "avg_weekly_workouts": 5,
    "today_logged": {
      "name": "Session name if logged today",
      "prescribed": "What the plan said",
      "executed": "What actually happened, with numbers",
      "status": "completed today | upcoming | skipped"
    },
    "races": [
      {
        "name": "Race name (e.g. 'City Marathon')",
        "date": "YYYY-MM-DD",
        "priority": "A | B | C",
        "target_time": "e.g. '4:30:00' (optional)"
      }
    ]
  },
  "experts": {
    "metrics": { "for_synthesis": "...", "signals": [...] },
    "activity": { ... },
    "physiology": { ... }
  },
  "executive_summary": {
    "headline": "ONE sentence take on where you are right now. 20–35 words. Plain English. MUST open with the time-of-day frame from Phase 0.5 — 'This morning…', 'Mid-day check-in…', or 'By end of day…' — so the reader sees the horizon before the verdict.",
    "whats_working": [
      "Short bullet, 1–2 sentences, positive signal.",
      "Another positive signal.",
      "3–5 bullets total."
    ],
    "whats_concerning": [
      "Short bullet, 1–2 sentences, amber/red signal.",
      "3–5 bullets total."
    ]
  },
  "kpis": [
    {
      "label": "Plain-language label (e.g. 'Load Strain Ratio', 'Fitness', 'Form')",
      "abbr": "Optional formal abbreviation for the tooltip badge (e.g. 'ACWR', 'CTL', 'TSB'). Omit if none exists.",
      "value": "Plain string — the number or status. Never prepend a '+' to positive numbers (write '45', not '+45'). ALWAYS preserve the minus sign on negative numbers. Form (TSB) is negative whenever fatigue exceeds fitness — write '-32', not '32' or '+32'.",
      "status": "green|amber|red",
      "trend": "rising|steady|falling (optional). This is the direction of numeric change over the recent window, NOT a judgement of whether the value is good or bad. Form going from -50 to -20 is 'rising' (less negative). Fatigue going from 85 to 70 is 'falling'. The renderer turns this into an ↗/→/↘ arrow that the reader reads as 'change'.",
      "note": "Plain-English interpretation of what this number means for the athlete today.",
      "source": "Plain-English source attribution (e.g. 'From Garmin's fitness trend', 'Calculated from fitness minus fatigue', 'Based on Garmin's race predictions, not a verified race')."
    }
  ],
  "recommendations": [
    {
      "horizon": "today|tomorrow|this_week|next_4_weeks",
      "text_short": "REQUIRED. One sentence, 20–30 words max, plain English. The essential action.",
      "text": "OPTIONAL. Full detail — 2–5 sentences of explanation, conditions, and reasoning. Displayed behind a 'More detail' disclosure."
    }
  ],
  "open_questions": [
    "Plain-English pattern-level questions only. Not one-off event follow-ups. 3–5 items."
  ]
}
```

### Required fields and legacy fallback

- `executive_summary` MUST be the structured dict (headline + whats_working + whats_concerning). The renderer also accepts a legacy single string for backward compatibility, but the structured form is strongly preferred.
- `recommendations[].text_short` is REQUIRED — it's the headline the user sees first.
- `kpis[].source` is REQUIRED on every KPI — always tell the user where a number comes from so they can verify.
- `kpis[].abbr` is OPTIONAL — use it only for standard abbreviations (ACWR, CTL, TSB, HRV, VDOT, SpO2). Never invent new abbreviations.
- `since_last_run` is OPTIONAL and must be omitted (or set to `null`) when `PREV_ANALYSIS` from Phase 0 did not exist. When it IS present, `prior_run_at` and `headline` are required; the rest default to empty arrays or `"unchanged"`. Do not fabricate a prior run.
- `plan.races[]` is REQUIRED whenever the athlete profile mentions any race with a date — the A race AND every B/C race. The "Road to the race" timeline renders one pin per entry (red for A, amber for B, blue for C). If you leave a B race as prose only, it will not appear on the roadmap. Extract races from `profile.md` (A race, B race, additional races section) AND from `raw.json.scheduled_workouts` / Garmin Connect's race calendar. Always include every known race with a date, even if it's months away — the renderer clips to the visible window.

### Expert-output framing guidance

- For `open_questions`: pattern-level questions only. Each entry must describe a recurring signal or systemic gap whose root cause is uncertain AND whose resolution would materially change the training plan. Examples: "repeated two-night severe sleep truncations on multiple occasions this window — exogenous stressor or emerging load-response?", "long-run ceiling has not progressed over the window — deliberate plan design or capacity limit?". NOT one-off event follow-ups like "did Tuesday's descent cause symptoms" — those belong in an expert's `signals` array as a single signal with its own `uncertainty` field. Rule of thumb: if the answer would only inform one past event, it's an expert signal; if the answer would change how the next 4+ weeks are built, it's an open question.
- For `recommendations[]` horizons:
  - `today` = the REMAINDER of today. If the athlete already logged a session today, acknowledge that session is done and frame this as "next steps after today's workout" (recovery, fuelling, sleep, mobility). Do NOT prescribe a workout for today if one is already in `raw.json` for today's date.
  - `tomorrow` = the next calendar day. If current time is late evening, this is essentially "within the next 12 hours."
  - `this_week` = the 7 days starting from tomorrow (or from now if tomorrow is implied).
  - `next_4_weeks` = block-level guardrails for the next 28 days.
- Follow the **Signals → Evidence → Implications → Uncertainty** framing inherited from upstream. Do not fabricate numbers; cite the expert output as the source for any KPI value.

If an expert's `for_synthesis` text is longer than ~5 sentences, summarize it down for the KPIs and recommendations — don't embed giant paragraphs verbatim. The expert's full text stays inside `experts.<name>.for_synthesis` where the renderer displays it collapsed.

## Phase 4: Render HTML

Invoke the bundled renderer via Bash. Again, inline the literal `RUN_DIR` value you captured in Phase 0:

```
"${CLAUDE_PLUGIN_ROOT}/bin/render-analysis" \
  "<RUN_DIR_VALUE>/analysis.json" \
  "<RUN_DIR_VALUE>/analysis.html"
```

The renderer auto-discovers extras next to `analysis.json` — `raw.json` and `training-metrics.json` for the charts, and (when present) `data/lab-zones.json` + `raw-90d.json` to append the **HR/pace-vs-lab-zones** section before `</body>`. That section is regenerated on every render via the bundled `bin/hrpace-lab-section` (a uv/scipy script; this renderer stays stdlib-only), so any later re-render of the same HTML reproduces it automatically — callers don't need to re-inject it. No `data/lab-zones.json` → the section is silently omitted.

Then update the `latest` symlink — the next analyze run reads `runs/latest` for its incremental fetch and since-last-run delta:

```
ln -sfn <TS_VALUE> runs/latest
```

## Phase 5: Report to the user

Print:

1. The absolute path to `analysis.html` (the user will open it in a browser).
2. The executive summary (3-5 sentences).
3. **Any activity already logged today** — name, type, duration, training effect. If present, the "today" recommendation below must frame itself as post-session guidance, not as a workout prescription. If absent, the "today" recommendation is a normal prescription.
4. The "today" and "tomorrow" recommendations from the `recommendations` array. These are the most time-sensitive.
5. Brief note on `this_week` and `next_4_weeks` framing.
6. Any `open_questions` — these become candidates to investigate in the next analyze run.

Keep the final message short. The HTML is the long-form artifact.

## Error handling

- Missing profile → stop, tell user to run `/peak-lab:setup`.
- `bin/fetch-garmin` exits non-zero with an auth-related error → stop, tell user to re-run `bin/garmin-auth` (the peak-lab login script).
- Subagent returns malformed JSON → stop, print the raw output, do not auto-retry.
- Render script non-zero exit → stop, print stderr, leave the JSON artifact in place so the user can inspect it.
