# peak-lab

A Claude Code plugin that turns your Garmin Connect data into a multi-agent
training analysis. It pulls the past couple of months of activities,
wellness, and training-status data, dispatches three specialist subagents in
parallel (metrics, activity, physiology), synthesizes their outputs, and
renders a self-contained HTML dashboard.

peak-lab is a **research preview** and a Claude-Code-native port of
[leonzzz435/garmin-ai-coach](https://github.com/leonzzz435/garmin-ai-coach)
(MIT) — the same three-expert architecture, re-expressed with Claude Code
skills, subagents, and two small CLIs (`bin/fetch-garmin`, `bin/garmin-call`)
as the data layer.

> **Not Garmin Coach.** Garmin Coach is Garmin's own adaptive training
> feature. peak-lab is an unaffiliated third-party tool that reads data from
> your Garmin Connect account.

## Quickstart

You need **Claude Code**, a **Garmin Connect account**, and **`uv`** (the
Python runner peak-lab's CLIs use).

1. **Install `uv`**:
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Clone the plugin**:
   ```
   git clone https://github.com/shlomihod/peak-lab.git
   ```
3. **Make a training-project folder** and launch Claude Code from it:
   ```
   mkdir ~/my-training-project && cd ~/my-training-project
   claude --plugin-dir /path/to/peak-lab
   ```
4. **Bootstrap**: run `/peak-lab:setup`. It prints the path to the bundled
   `bin/garmin-auth` script — run that once in a terminal. It prompts for
   your Garmin email, password, and an MFA code (if enabled), then saves
   OAuth tokens to `~/.garminconnect/garmin_tokens.json`. Your password is
   never stored. (Re-run `garmin-auth --verify` to check tokens, `--force`
   to log in again.)
5. **Analyze**: run `/peak-lab:analyze`. The first run takes a couple of
   minutes while `uv` resolves dependencies; later runs are ~15–30 seconds.

`--plugin-dir` loads peak-lab for one session — the intended path while it's
unpublished. Once it's in a plugin marketplace, installation becomes
`/plugin marketplace add <marketplace>` then `/plugin install peak-lab@<marketplace>`.

## The commands

- **`/peak-lab:setup`** — one-time bootstrap. Checks prerequisites, walks you
  through Garmin authentication, and captures a short athlete profile (goals,
  constraints, races) in `profile.md`. Re-run any time to update the profile.
- **`/peak-lab:analyze`** — pulls ~60 days of Garmin data (activities, sleep,
  HRV, training status/readiness, body battery, plans, PRs, race predictions)
  into `raw.json`, dispatches the `metrics`, `activity`, and `physiology`
  expert subagents in parallel, synthesizes their findings, and writes
  `analysis.html` + `analysis.json`. Open the HTML in a browser.

## Where your data lives

peak-lab writes **every file into the current working directory** — the
folder you launched Claude Code from. That folder is your training project:
profile, raw data, analyses, and plans all live there. No env vars, no
fallback paths.

**Keep your training project separate from the plugin source** — don't run
peak-lab from inside the cloned `peak-lab/` folder. A typical project after an
analyze run:

```
my-training-project/
├── profile.md               your athlete profile (goals, constraints, races)
├── garmin-call.log          audit log of every Garmin API call
└── runs/
    ├── 20260413T142123Z/    one analyze run
    │   ├── raw.json         full Garmin pull (~20 MB)
    │   ├── analysis.json
    │   └── analysis.html    open this in a browser
    └── latest → …           symlink to the most recent run
```

If you git-track the project, ignore the bulky raw data in `.gitignore`:

```
runs/*/raw.json
garmin-call.log
```

Garmin OAuth tokens live separately at `~/.garminconnect/garmin_tokens.json`
— global, written only by `garmin-auth`, reused across all your projects.

## Configuration

peak-lab uses Claude Code's `userConfig` mechanism — you're prompted when the
plugin is enabled:

| Key                    | Default | Meaning                                 |
| ---------------------- | ------- | --------------------------------------- |
| `analysis_window_days` | `60`    | How many days of data `/analyze` pulls. |

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│                    /peak-lab:analyze (skill)                    │
│                                                                 │
│  1. Read athlete profile                                        │
│  2. bin/fetch-garmin → raw.json (bulk pull, orchestrator)       │
│  3. Task(metrics-expert)  ┐                                     │
│  3. Task(activity-expert) ├─ parallel subagent dispatch         │
│  3. Task(physiology-expert)                                     │
│       └── subagents: Read, Grep, Bash → bin/garmin-call         │
│           (whitelist is the capability boundary)                │
│  4. Synthesize expert outputs → analysis.json                   │
│  5. bin/render-analysis → analysis.html                         │
└─────────────────────────────────────────────────────────────────┘
         │                                   │
         ▼ (bulk)                            ▼ (ad-hoc)
┌───────────────────┐              ┌───────────────────┐
│  bin/fetch-garmin │              │  bin/garmin-call  │
│  (CLI, batch)     │              │  whitelist + audit│
└────────┬──────────┘              └────────┬──────────┘
         └────────────┬─────────────────────┘
                      ▼
         ┌──────────────────────────────┐
         │     Garmin Connect API       │
         │  (python-garminconnect 0.3+) │
         └──────────────────────────────┘
```

## Security model

peak-lab is a **research preview** — don't point it at a Garmin account you
can't afford to expose. Garmin's OAuth tokens grant full account access
(there is no read-only scope), so every safeguard here is client-side.

The one real boundary is `bin/garmin-call`. Every Garmin request — from the
orchestrator and from subagents alike — goes through it, and it dispatches
only a hardcoded set of ~69 read-only methods. Anything else, including every
`delete_*` / `add_*` / `set_*` / `upload_*` / `create_*` method, is rejected
before it reaches Garmin.

Two caveats worth knowing:

- **Garmin free-text is untrusted.** Activity names, notes, and workout
  descriptions are user-editable and could carry prompt-injection text.
  Expert prompts treat them as untrusted input, but this relies on Claude's
  general robustness, not a scrubbing layer.
- **Subagents can run `Bash`.** The `garmin-call` whitelist protects your
  Garmin account, not your local machine. A fully adversarial model with
  shell access could bypass these client-side controls — a threat this
  preview does not attempt to mitigate.

Every `garmin-call` invocation is logged to `garmin-call.log` (method,
status, timing — no response bodies) for a basic audit trail.

## Limitations

- **Not a replacement for Garmin Coach** — a second-opinion analysis
  alongside whatever plan you already follow.
- **Read-only** — peak-lab never uploads anything to Garmin; all writes are
  local.
- **Claude Code only** — unlike the original `garmin-ai-coach`, there's no
  multi-provider LLM abstraction; peak-lab runs entirely on Claude Code.
- `/analyze` fetches incrementally by default, reusing closed historical days
  from the previous `raw.json`; pass `--no-incremental` to force a clean pull.

## Attribution

The three-expert architecture, expert prompts, and metric framing are derived
from [leonzzz435/garmin-ai-coach](https://github.com/leonzzz435/garmin-ai-coach)
(MIT). Garmin data access uses
[cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
0.3.2+. Training-pace zones use Jack Daniels' VDOT model — see `bin/vdot` for
the citation trail. Full attribution is in [`NOTICE`](./NOTICE).

## License

MIT — see [`LICENSE`](./LICENSE).
