"""Safe execution engine: allow-list only, shell=False, no arbitrary argv."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hardware_qa.config import get_settings
from hardware_qa.demo_fixtures import get_demo_payload
from hardware_qa.discover import CapabilityReport
from hardware_qa.parsers.normalize import parse_tool_output
from hardware_qa.tools.registry import ALLOW_LISTED_TOOLS


class UnauthorizedToolError(ValueError):
    pass


def execute_tool(
    tool_name: str,
    caps: CapabilityReport,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an allow-listed tool and return structured JSON."""
    kwargs = kwargs or {}

    if tool_name not in ALLOW_LISTED_TOOLS:
        raise UnauthorizedToolError(f"Unauthorized tool execution attempt: {tool_name}")

    if tool_name not in caps.available_tools:
        return {
            "error": f"Tool '{tool_name}' is not available on this system.",
            "missing": True,
        }

    if caps.demo_mode:
        return get_demo_payload(tool_name)

    spec = ALLOW_LISTED_TOOLS[tool_name]
    settings = get_settings()

    # Resolve binary to absolute path when possible (hardens against PATH tricks)
    binary = spec.argv[0]
    resolved = shutil.which(binary)
    if resolved:
        cmd = [resolved, *spec.argv[1:]]
    elif os.path.isabs(binary) and os.path.exists(binary):
        cmd = list(spec.argv)
    else:
        return _try_fallbacks(tool_name, spec.fallbacks)

    # Optional passwordless sudo only for known root tools; argv stays fixed
    needs_sudo = (
        spec.requires_root
        and hasattr(os, "geteuid")
        and os.geteuid() != 0  # type: ignore[attr-defined]
    )
    if needs_sudo:
        sudo = shutil.which("sudo")
        if sudo:
            cmd = [sudo, "-n", *cmd]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=settings.command_timeout_sec,
            shell=False,  # CRITICAL: no shell injection
        )
    except subprocess.TimeoutExpired:
        return {"error": f"Tool '{tool_name}' timed out after {settings.command_timeout_sec}s."}
    except FileNotFoundError:
        return _try_fallbacks(tool_name, spec.fallbacks)

    if result.returncode != 0 or not result.stdout.strip():
        fallback = _try_fallbacks(tool_name, spec.fallbacks)
        if "error" not in fallback:
            fallback["fallback_reason"] = (
                result.stderr.strip() or f"command exited {result.returncode}"
            )
            return fallback
        return {
            "error": f"Execution failed for '{tool_name}'.",
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
            "fallback": fallback,
        }

    parsed = parse_tool_output(tool_name, result.stdout, source=" ".join(spec.argv))
    # kwargs currently unused beyond schema documentation; keep for future filters
    if tool_name == "get_memory_topology" and not kwargs.get("include_empty_slots", True):
        parsed["module_details"] = [
            m for m in parsed.get("module_details", []) if m.get("installed")
        ]
    return parsed


def _try_fallbacks(tool_name: str, fallbacks: list[str]) -> dict[str, Any]:
    for fb in fallbacks:
        if fb.startswith("/") and Path(fb).exists():
            if Path(fb).is_dir():
                # /sys/class/dmi/id — read known files
                parts = []
                for name in (
                    "sys_vendor",
                    "product_name",
                    "product_version",
                    "product_serial",
                    "board_vendor",
                    "board_name",
                ):
                    p = Path(fb) / name
                    if p.exists():
                        try:
                            parts.append(f"{name}={p.read_text(encoding='utf-8', errors='replace').strip()}")
                        except OSError:
                            continue
                text = "\n".join(parts)
            else:
                try:
                    text = Path(fb).read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    return {"error": str(exc)}
            return parse_tool_output(tool_name, text, source=fb)

        which = shutil.which(fb)
        if which:
            try:
                result = subprocess.run(
                    [which],
                    capture_output=True,
                    text=True,
                    timeout=get_settings().command_timeout_sec,
                    shell=False,
                )
                if result.stdout.strip():
                    return parse_tool_output(tool_name, result.stdout, source=fb)
            except (subprocess.TimeoutExpired, OSError):
                continue

    return {"error": f"No working binary or fallback for '{tool_name}'."}
