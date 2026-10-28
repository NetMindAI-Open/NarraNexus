---
name: home-assistant-setup
description: Set up a local Home Assistant and connect the user's smart-home devices (Xiaomi/Mi Home, Philips Hue, Zigbee, etc.), then hand back the URL + token so it can be bound into NarraNexus. Use when the user asks to connect their home / smart devices / Xiaomi / Home Assistant and does not already have an HA URL + token.
---

# home-assistant-setup

Get a user's smart home controllable by this agent. You drive everything via the
bundled `ha_setup.py` CLI — the user only acts at the two points that genuinely
need them (a one-time `sudo` for `/etc/hosts`, and signing into their device
account). This skill is **local/desktop only**: if the user runs NarraNexus in
the cloud, they instead expose their own HA and paste its URL + token into the
Smart Home config panel — no install needed.

## Availability

`ha_setup.py` is bundled with this skill — it is already on disk at
`skills/home-assistant-setup/ha_setup.py`. Run it, do **not** download or
reinstall it. It needs Python 3 with `aiohttp` (present in this platform's
Python). Requires Docker to be installed on the user's machine.

Every command prints human progress to stderr and one machine line to stdout:
- `RESULT {…json…}` — the outcome; parse it.
- `ACTION_REQUIRED: <instruction>` (exit code 10) — a step only the user can do.
  Relay the instruction to the user (for a login URL, give it as a **clickable
  link in chat**), wait for them, then continue.

## Steps

Run each as `python3 skills/home-assistant-setup/ha_setup.py <command>`.

1. **`doctor`** — read-only. See what already exists (Docker present? HA running?
   onboarded? Xiaomi installed? hosts ok? token?). Skip steps already done.

2. **`deploy`** — deploy Home Assistant in Docker and wait until ready.
   Idempotent (skips if the container is already up).

3. **`init --gen`** — create the admin account and mint a Long-Lived Access
   Token. `RESULT` carries `username`, `password` (if generated), and `token`.
   Show the username/password to the user so they can log into HA later, and
   keep the `token` for step 8. If HA is already onboarded, pass
   `--username <u> --password <p>` instead so it logs in and mints a token.

4. **Ask which ecosystem** the user's devices belong to (Xiaomi/Mi Home, a brand
   cloud like Hue/Nest/Tuya/SmartThings, or local Zigbee/Z-Wave/Matter). For
   **Xiaomi** continue with steps 5–7. For other ecosystems, add the matching HA
   integration via the WebSocket `config_entries/flow` API instead (same
   pattern; only the handler and the human login step differ).

5. **`install-xiaomi`** — install the official `XiaoMi/ha_xiaomi_home`
   integration and restart HA. Idempotent. (Downloads inside the container, so a
   blocked host network doesn't matter.)

6. **`xiaomi-login`** — prints `ACTION_REQUIRED` with the Xiaomi authorization
   URL. Give it to the user as a **clickable chat link**; they sign in and
   approve. The message also tells the user what to do next, which depends on
   whether `homeassistant.local` resolves:

   - **Default (no sudo) — Approach A**: after authorizing, the browser fails to
     open a `homeassistant.local` page (expected). Ask the user for the `code=`
     value in that failed page's address bar (it looks like `C3_...` and ends
     right before the `&`), then run `xiaomi-callback --code <that value>`.
     **Do NOT paste the whole URL** — its `&` breaks the shell unless quoted, and
     that's the #1 way this step fails. Pass ONLY the code via `--code`; the CLI
     rebuilds the rest from saved state. HA ignores the Host header, so this
     delivers the code and completes the flow — no `/etc/hosts` edit needed.
   - **If the user prefers / already ran `hosts`**: the browser lands back on HA
     directly; just run `xiaomi-finish`.

   Both `xiaomi-callback` and `xiaomi-finish` poll to completion and report how
   many device entities were imported.

   (Optional: `hosts` makes `homeassistant.local` resolve locally for the
   `xiaomi-finish` path; Approach A avoids it. `hosts` run **by the user in a
   terminal** prompts for their sudo password and does it in one step; run **by
   you (agent, no terminal)** it can't prompt, so it prints the `sudo` command as
   `ACTION_REQUIRED` for the user to run. The password is the user's
   authorization — never try to bypass it.)

7. **Bind into NarraNexus** — either run
   `bind --agent-id <this agent's id> [--backend-url <url>] [--user-id <id>]`, or
   call `PUT /api/home-assistant/binding` yourself with the `base_url` + `token`
   from step 3, **for the agent the user is talking to**. Then the user can ask
   you to list/control their devices.

## Notes

- `all` runs deploy→init→install-xiaomi→xiaomi-login and stops at the one
  `ACTION_REQUIRED` (Xiaomi login). Use it for a hands-off run; then finish with
  `xiaomi-callback` (or `xiaomi-finish`). Use the individual commands when you
  need finer control or are resuming.
- If a command fails, run `doctor` and check the HA logs
  (`docker logs homeassistant`). Never fake a step the user must do.
- Scenario rules ("turn on the living-room light at 8pm") are **not** set up
  here — those live in the agent's Awareness. This skill only makes the devices
  reachable.
