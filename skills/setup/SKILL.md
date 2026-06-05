---
name: setup
description: First-run bootstrap for peak-lab. Verifies prerequisites, walks the user through Garmin Connect authentication, and captures an athlete profile. Run this once before /peak-lab:analyze.
---

# peak-lab setup

You are bootstrapping the peak-lab plugin for a new user. Run through the following checklist, pausing for user input where needed. Be concise — each step is one or two sentences of output unless something is wrong.

## 1. Verify `uv` is installed

Run `command -v uv` via Bash. If missing, tell the user to install it (`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS/Linux) and stop here. Do not attempt to auto-install.

## 2. Verify Garmin Connect tokens exist

Run `"${CLAUDE_PLUGIN_ROOT}/bin/garmin-auth" --verify` via Bash. Exit code 0 means tokens are valid — skip to step 4. Non-zero means we need to log in (step 3).

## 3. Walk the user through one-time Garmin Connect authentication

Tell the user to run this in their terminal and wait for confirmation:

```
"${CLAUDE_PLUGIN_ROOT}/bin/garmin-auth"
```

(They can copy the absolute path from the message you printed.) The script prompts for email, password, and MFA code if needed, then saves DI OAuth tokens to `~/.garminconnect/garmin_tokens.json`. peak-lab never sees the password — only the saved tokens.

If they hit a 429, tell them to wait 15-60 minutes and try again — Garmin throttles by IP. Do NOT retry repeatedly; that extends the cooldown.

After the user confirms the script printed `✓ Login successful`, re-run `bin/garmin-auth --verify` to confirm. If verify still fails, surface the error and stop.

## 4. Capture the athlete profile

**peak-lab works in the current directory.** Whatever directory the user ran Claude Code from IS their training project — `profile.md`, `runs/`, and `garmin-call.log` all live right there. No env vars, no overrides, no git-repo-root guessing. If the user wants a different project, they `cd` and relaunch.

Sanity-check the cwd in one Bash call:

```
pwd
test ! -d .claude-plugin || { echo "ERROR: $(pwd) is the plugin source directory — cd into a separate training project folder"; exit 1; }
test -f profile.md && echo "PROFILE_EXISTS" || echo "NO_PROFILE"
```

If the output includes `.claude-plugin/` at the current directory, stop and tell the user to `cd` into a separate training project folder and relaunch.

If `PROFILE_EXISTS`, read `profile.md` and print a short summary, then ask the user whether to keep or update it.

If it doesn't exist, keep the ask short. Sports, training hours, and recent race history can be **inferred later** from the Garmin data pulled in `/peak-lab:analyze` — do NOT ask for them up front. Only ask for what's genuinely self-reported and unknowable from the data:

1. **Current training goals** (required, free-form) — e.g. "build base then race a sub-4 marathon in October", "first 70.3 in June".
2. **Known injuries or constraints** (required, can be "none") — historical issues count too, with notes on whether they're active or latent.
3. **Target race time for the A race** (optional, but recommended if a race is set) — e.g. "marathon target 4:00:00". This drives VDOT-derived training pace zones when there's no recent real race to anchor on.
4. **Upcoming races** beyond the A race (optional) — peak-lab also auto-discovers races from Garmin Connect's calendar via `get_scheduled_workouts` in the analyze flow, so only ask if the user wants to add something that isn't already in Garmin.

Do **not** ask about sports, typical training hours, or recent race results — those come from the Garmin pull. Explain this briefly so the user understands why the profile is so short.

Write the collected answers to `profile.md` (in the current directory) using the Write tool. Use this structure:

```markdown
# Athlete profile

_Last updated: <YYYY-MM-DD>_

## Goals
...

## Constraints / injuries
...

## A race — target time
<e.g. "City Marathon YYYY-MM-DD — target H:MM:SS"; omit the target section if none set>

## Additional races (beyond what Garmin's calendar contains)
| Name | Date | Priority |
| ---- | ---- | -------- |
| ... | ... | ... |

## Auto-derived at analyze time
- Sports: inferred from `activities[]` in `raw.json`
- Training volume: computed from 4-week and 8-week rolling windows over `activities[]`
- Recent race fitness: derived from `personal_records` or race-flagged activities
```

## 5. Confirm and hand off

Print: `✓ peak-lab is ready. Run /peak-lab:analyze to generate your first training analysis.`

## Notes for you, the orchestrator

- The training project IS the current working directory. Don't invent env vars, don't consult git, don't look anywhere else.
- When you use the Write tool to create `profile.md`, pass an absolute path (e.g. `/path/to/my-project/profile.md`) — the Write tool expects absolute paths. Use `$(pwd)/profile.md` resolved to its literal value.
- Never print the user's Garmin password or token contents.
- If any step fails, surface the exact error and do not proceed.
- Data lives in the user's cwd. This is intentional: peak-lab treats the project folder as the single source of truth for the athlete's training knowledge.
