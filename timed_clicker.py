from __future__ import annotations

import asyncio
import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import nodriver as uc


LOGGER = logging.getLogger("timed_clicker")


@dataclass(slots=True)
class ButtonStep:
    name: str
    selector: str
    click_method: str = "click"
    post_click_delay_ms: int = 0


@dataclass(slots=True)
class ClickPlan:
    run_at: datetime
    url: str
    profile_dir: Path
    warmup_seconds: int = 120
    selector_timeout_seconds: float = 10.0
    settle_delay_ms: int = 250
    pre_fire_spin_ms: int = 250
    page_ready_selector: str | None = None
    headless: bool = False
    sandbox: bool = True
    browser_executable_path: str | None = None
    browser_args: list[str] | None = None
    buttons: list[ButtonStep] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a page ahead of time and click configured buttons at a target timestamp."
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to a JSON config file. See timed_clicker.example.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load the page and validate selectors, but do not wait for the fire time or click.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args()


def load_config(path: Path) -> ClickPlan:
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        buttons = [parse_button_step(item) for item in raw["buttons"]]
    except KeyError as exc:
        raise ValueError("Config must include a 'buttons' array.") from exc

    run_at_raw = raw.get("run_at")
    if not run_at_raw:
        raise ValueError("Config must include 'run_at' in local ISO format.")

    run_at = datetime.fromisoformat(run_at_raw)
    if run_at.tzinfo is not None:
        run_at = run_at.astimezone().replace(tzinfo=None)

    profile_dir = Path(raw.get("profile_dir", "./chrome-profile")).expanduser().resolve()

    return ClickPlan(
        run_at=run_at,
        url=raw["url"],
        profile_dir=profile_dir,
        warmup_seconds=int(raw.get("warmup_seconds", 120)),
        selector_timeout_seconds=float(raw.get("selector_timeout_seconds", 10.0)),
        settle_delay_ms=int(raw.get("settle_delay_ms", 250)),
        pre_fire_spin_ms=int(raw.get("pre_fire_spin_ms", 250)),
        page_ready_selector=raw.get("page_ready_selector"),
        headless=bool(raw.get("headless", False)),
        sandbox=bool(raw.get("sandbox", True)),
        browser_executable_path=raw.get("browser_executable_path"),
        browser_args=list(raw.get("browser_args", [])),
        buttons=buttons,
    )


def parse_button_step(raw: dict[str, Any]) -> ButtonStep:
    selector = raw.get("selector")
    if not selector:
        raise ValueError("Each button step must include a selector.")

    click_method = raw.get("click_method", "click")
    if click_method not in {"click", "mouse_click"}:
        raise ValueError(
            f"Unsupported click_method '{click_method}'. Use 'click' or 'mouse_click'."
        )

    return ButtonStep(
        name=raw.get("name") or selector,
        selector=selector,
        click_method=click_method,
        post_click_delay_ms=int(raw.get("post_click_delay_ms", 0)),
    )


def validate_plan(plan: ClickPlan, *, dry_run: bool) -> None:
    if not plan.url:
        raise ValueError("Config must include 'url'.")
    if not plan.buttons:
        raise ValueError("Config must include at least one button step.")
    if plan.warmup_seconds < 0:
        raise ValueError("'warmup_seconds' must be zero or greater.")
    if plan.pre_fire_spin_ms < 0:
        raise ValueError("'pre_fire_spin_ms' must be zero or greater.")
    if plan.browser_args is None:
        raise ValueError("'browser_args' must be an array when provided.")

    now = datetime.now()
    if not dry_run and plan.run_at <= now:
        raise ValueError(
            f"'run_at' must be in the future. Now={now.isoformat(timespec='seconds')}"
        )


async def wait_until(target: datetime, label: str) -> None:
    while True:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            LOGGER.info("%s reached at %s", label, timestamp_now())
            return

        if remaining > 60:
            sleep_for = min(remaining - 30, 300)
        elif remaining > 5:
            sleep_for = min(remaining - 2, 10)
        elif remaining > 1:
            sleep_for = 0.25
        else:
            sleep_for = 0.01

        await asyncio.sleep(sleep_for)


async def wait_for_fire_time(target: datetime, spin_window_ms: int) -> None:
    spin_window_seconds = max(spin_window_ms, 0) / 1000
    coarse_cutoff = target.timestamp() - spin_window_seconds

    while True:
        now_ts = time.time()
        if now_ts >= coarse_cutoff:
            break
        await asyncio.sleep(min(coarse_cutoff - now_ts, 1))

    while True:
        if time.time() >= target.timestamp():
            LOGGER.info("Fire time reached at %s", timestamp_now())
            return
        await asyncio.sleep(0.001)


async def resolve_button(tab: Any, step: ButtonStep, timeout: float) -> Any:
    LOGGER.info("Resolving selector for step '%s': %s", step.name, step.selector)
    button = await tab.select(step.selector, timeout=timeout)
    if not button:
        raise TimeoutError(f"Selector not found for step '{step.name}': {step.selector}")
    return button


async def click_button(tab: Any, step: ButtonStep, timeout: float) -> None:
    button = await resolve_button(tab, step, timeout)
    await tab

    action = getattr(button, step.click_method)
    started_at = timestamp_now()
    LOGGER.info("Clicking step '%s' with %s at %s", step.name, step.click_method, started_at)
    await action()

    if step.post_click_delay_ms > 0:
        delay_seconds = step.post_click_delay_ms / 1000
        LOGGER.debug(
            "Waiting %.3fs after step '%s' for page transition.",
            delay_seconds,
            step.name,
        )
        await asyncio.sleep(delay_seconds)


async def prepare_page(browser: Any, plan: ClickPlan) -> Any:
    LOGGER.info("Opening %s", plan.url)
    tab = await browser.get(plan.url)
    await tab

    if plan.page_ready_selector:
        LOGGER.info("Waiting for page_ready_selector: %s", plan.page_ready_selector)
        await tab.select(plan.page_ready_selector, timeout=plan.selector_timeout_seconds)

    for step in plan.buttons or []:
        await resolve_button(tab, step, plan.selector_timeout_seconds)

    settle_seconds = plan.settle_delay_ms / 1000
    if settle_seconds > 0:
        LOGGER.debug("Settling for %.3fs after selector validation.", settle_seconds)
        await asyncio.sleep(settle_seconds)

    LOGGER.info("Page prepared at %s", timestamp_now())
    return tab


async def run_click_plan(plan: ClickPlan, *, dry_run: bool) -> int:
    warmup_at = plan.run_at
    if not dry_run:
        warmup_at = datetime.fromtimestamp(plan.run_at.timestamp() - plan.warmup_seconds)
        await wait_until(warmup_at, "Warm-up time")

    plan.profile_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Starting browser with profile %s", plan.profile_dir)

    browser = await uc.start(
        user_data_dir=str(plan.profile_dir),
        headless=plan.headless,
        sandbox=plan.sandbox,
        browser_executable_path=plan.browser_executable_path,
        browser_args=plan.browser_args,
    )

    try:
        tab = await prepare_page(browser, plan)

        if dry_run:
            LOGGER.info("Dry run complete. No clicks were executed.")
            return 0

        await wait_for_fire_time(plan.run_at, plan.pre_fire_spin_ms)
        for step in plan.buttons or []:
            await click_button(tab, step, plan.selector_timeout_seconds)

        LOGGER.info("Completed all clicks at %s", timestamp_now())
        return 0
    finally:
        LOGGER.info("Stopping browser")
        browser.stop()


def timestamp_now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        plan = load_config(args.config)
        validate_plan(plan, dry_run=args.dry_run)
    except Exception as exc:
        LOGGER.error("Invalid configuration: %s", exc)
        return 2

    LOGGER.info(
        "Loaded plan for %s with %d steps",
        plan.run_at.isoformat(timespec="seconds"),
        len(plan.buttons or []),
    )

    try:
        return uc.loop().run_until_complete(run_click_plan(plan, dry_run=args.dry_run))
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user")
        return 130
    except Exception as exc:
        LOGGER.exception("Run failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
