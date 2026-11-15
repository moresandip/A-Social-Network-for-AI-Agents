"""Startup capability discovery.

Probes which inspection binaries exist on this machine so the agent only
registers tools that can actually run. Missing tools are reported honestly
instead of being attempted and failing.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field

from hardware_qa.tools.registry import ALLOW_LISTED_TOOLS


@dataclass
class CapabilityReport:
    available_tools: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    demo_mode: bool = False
    platform: str = field(default_factory=platform.platform)

    def has(self, tool_name: str) -> bool:
        return tool_name in self.available_tools


def _binary_available(name_or_path: str) -> bool:
    if not name_or_path:
        return False
    if os.path.isabs(name_or_path):
        return os.path.exists(name_or_path) and os.access(name_or_path, os.X_OK)
    return shutil.which(name_or_path) is not None


def _tool_available(tool_name: str) -> bool:
    spec = ALLOW_LISTED_TOOLS[tool_name]
    if _binary_available(spec.argv[0]):
        return True
    return any(_binary_available(fb) for fb in spec.fallbacks)


def discover_capabilities(*, demo_mode: bool = False) -> CapabilityReport:
    """Scan the system once at startup and return a CapabilityReport."""
    report = CapabilityReport(demo_mode=demo_mode)
    if demo_mode:
        report.available_tools = list(ALLOW_LISTED_TOOLS.keys())
        report.missing_tools = []
        return report

    for name in ALLOW_LISTED_TOOLS:
        if _tool_available(name):
            report.available_tools.append(name)
        else:
            report.missing_tools.append(name)
    return report
