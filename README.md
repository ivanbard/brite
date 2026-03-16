# Timed `nodriver` Clicker

This repo contains a one-shot Python runner that opens a page ahead of time, validates the target selectors, and clicks the configured buttons in order at a specific local timestamp.

## Install

```bash
pip install nodriver
```

## Configure

Copy [`timed_clicker.example.json`](./timed_clicker.example.json) and update:

- `run_at`: local ISO datetime, for example `2026-03-16T18:00:00`
- `url`: page to preload
- `profile_dir`: persistent Chrome profile directory to preserve login/session state
- `page_ready_selector`: selector that proves the page is loaded
- `resolve_poll_ms`: how often to poll for the next button after a click
- `prevalidate_steps`: number of leading button steps expected to exist before the fire time
- `keep_open_after_run`: keep the browser open after a successful live run
- `sandbox`: whether to let Chrome use its sandboxed startup path
- `browser_executable_path`: optional explicit Chrome/Chromium executable path
- `browser_args`: optional extra Chrome flags
- `buttons`: ordered click sequence

Each button entry supports:

- `name`: log label
- `selector`: CSS selector
- `timeout_seconds`: optional per-step wait budget for that button to appear
- `poll_ms`: optional per-step DOM poll interval while waiting for that button
- `click_retries`: optional number of times to retry the click if the first attempt fails
- `retry_delay_ms`: delay between click retries
- `require_visible`: wait until the matched element is visibly rendered
- `require_enabled`: wait until the matched element is not disabled
- `click_method`: `click` or `mouse_click`
- `post_click_delay_ms`: optional pause after the click

## Run

Dry-run selector rehearsal:

```bash
python timed_clicker.py timed_clicker.example.json --dry-run
```

Live run:

```bash
python timed_clicker.py timed_clicker.example.json
```

## Behavior

- The script waits until `run_at - warmup_seconds`, then launches the browser.
- It opens the target page and validates only the first `prevalidate_steps` selectors before the fire time.
- It re-queries each button immediately before clicking to reduce stale-element failures.
- It polls for later buttons at `resolve_poll_ms` intervals after each click.
- It uses a coarse sleep followed by a tight spin window before the target second.
- If any selector cannot be resolved, the run fails before clicking.
- By default it closes the browser when the run ends; set `keep_open_after_run` to keep it open.

## Notes

- Use a real Chrome/Chromium profile that is already logged in to the target site.
- Start with `--dry-run` and a near-future timestamp to verify selectors and timing.
- For multi-step flows where later buttons only appear after earlier clicks, leave `prevalidate_steps` at `1`.
- For high-traffic pages, give later steps larger `timeout_seconds` values instead of adding fixed sleeps.
- If the page shows modals or overlays, add logic for those conditions before using the script in production.

## Windows startup troubleshooting

If `nodriver` prints `Failed to connect to browser` right after launching Chrome:

- Use an absolute `profile_dir`. The script now resolves this automatically.
- Set `browser_executable_path` explicitly if Chrome is not on the expected path.
- Try `"sandbox": false` in the config. The upstream error text refers to `no_sandbox=True`; in the current API that maps to `sandbox=False`.
- Close any stuck Chrome instances that were launched against the same `profile_dir`, then retry.
- If the profile directory was created by a failed run, delete that directory and retry with a fresh one.
