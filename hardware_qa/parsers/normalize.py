"""Parsers that normalize raw Linux command output into structured JSON.

Each parser tolerates distro differences and missing fields; anything it
cannot extract is simply omitted so the answer layer can say "unknown"
instead of hallucinating.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_GB = 1024.0**3


def parse_tool_output(tool_name: str, stdout: str, source: str = "") -> dict[str, Any]:
    """Dispatch raw stdout to the right normalizer."""
    parsers = {
        "get_cpu_info": _parse_lscpu,
        "get_memory_summary": _parse_free,
        "get_memory_topology": _parse_dmidecode_memory,
        "get_block_devices": _parse_lsblk,
        "get_gpu_info": _parse_lspci_gpus,
        "get_dmi_system": lambda t: _parse_dmi_system(t, source),
        "get_pci_devices": _parse_lspci,
    }
    parser = parsers.get(tool_name)
    if parser is None:
        return {"raw_output": stdout}
    try:
        return parser(stdout)
    except Exception as exc:  # never crash the assistant on odd output
        return {"error": f"Failed to parse '{tool_name}' output: {exc}", "raw_output": stdout[:2000]}


# ---------------------------------------------------------------- lscpu

def _parse_lscpu(text: str) -> dict[str, Any]:
    data: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            data[key.strip()] = val.strip()

    def _int(key: str) -> int | None:
        raw = data.get(key, "")
        m = re.search(r"\d+", raw)
        return int(m.group()) if m else None

    return {
        "model_name": data.get("Model name"),
        "architecture": data.get("Architecture"),
        "cores_per_socket": _int("Core(s) per socket"),
        "sockets": _int("Socket(s)"),
        "cpus": _int("CPU(s)"),
        "mhz": data.get("CPU max MHz") or data.get("CPU MHz"),
        "raw": data,
    }


# ---------------------------------------------------------------- free

def _parse_free(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            # Mem: total used free shared buff/cache available
            if len(parts) >= 7:
                total, used, _free, _shared, _cache, available = parts[1:7]
                return {
                    "total_gb": round(int(total) / _GB, 1),
                    "used_gb": round(int(used) / _GB, 1),
                    "available_gb": round(int(available) / _GB, 1),
                    "source": "free -b",
                }
    return {"error": "Could not parse 'free' output."}


# ------------------------------------------------------- dmidecode memory

def _parse_dmidecode_memory(text: str) -> dict[str, Any]:
    max_cap = re.search(r"Maximum Capacity:\s*(.+)", text)
    modules: list[dict[str, Any]] = []

    # Split into per-device blocks
    blocks = re.split(r"Memory Device\s*\n\s*Handle:", text)
    for block in blocks[1:]:
        size_m = re.search(r"Size:\s*(.+)", block)
        speed_m = re.search(r"Speed:\s*(.+)", block)
        mfr_m = re.search(r"Manufacturer:\s*(.+)", block)
        pn_m = re.search(r"Part Number:\s*(.+)", block)
        size = size_m.group(1).strip() if size_m else "Unknown"
        installed = "No Module Installed" not in size
        modules.append(
            {
                "installed": installed,
                "size": size,
                "speed": speed_m.group(1).strip() if speed_m else None,
                "manufacturer": mfr_m.group(1).strip() if mfr_m else None,
                "part_number": pn_m.group(1).strip() if pn_m else None,
            }
        )

    # Fallback: count bare "Size:" lines if block split failed
    if not modules:
        sizes = re.findall(r"Size:\s*(.+)", text)
        modules = [
            {"installed": "No Module Installed" not in s, "size": s.strip()}
            for s in sizes
        ]

    used = sum(1 for m in modules if m.get("installed"))
    return {
        "maximum_capacity": max_cap.group(1).strip() if max_cap else None,
        "total_slots": len(modules),
        "used_slots": used,
        "free_slots": len(modules) - used,
        "module_details": modules,
    }


# ---------------------------------------------------------------- lsblk

def _parse_lsblk(text: str) -> dict[str, Any]:
    raw = json.loads(text)
    devices: list[dict[str, Any]] = []

    def _walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            name = node.get("name")
            tran = (node.get("tran") or "").lower()
            rota = node.get("rota")
            is_nvme = bool(name and name.startswith("nvme")) or tran == "nvme"
            is_ssd = is_nvme or (rota == 0 and node.get("type") == "disk")
            devices.append(
                {
                    "name": name,
                    "size": node.get("size"),
                    "type": node.get("type"),
                    "mountpoint": node.get("mountpoint"),
                    "model": node.get("model"),
                    "rota": rota,
                    "tran": node.get("tran"),
                    "is_nvme": is_nvme,
                    "is_ssd": is_ssd,
                }
            )
            children = node.get("children")
            if children:
                _walk(children)

    _walk(raw.get("blockdevices", []))
    return {
        "devices": devices,
        "has_nvme": any(d["is_nvme"] for d in devices),
    }


# ---------------------------------------------------------------- lspci

_GPU_RE = re.compile(r"(vga|3d controller|display)", re.I)


def _parse_lspci_gpus(text: str) -> dict[str, Any]:
    gpus = [ln.strip() for ln in text.splitlines() if _GPU_RE.search(ln)]
    return {"gpus": gpus}


def _parse_lspci(text: str) -> dict[str, Any]:
    devices = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {"devices": devices, "count": len(devices)}


# ------------------------------------------------------- dmidecode / sysfs

def _parse_dmi_system(text: str, source: str = "") -> dict[str, Any]:
    # sysfs fallback: /sys/class/dmi/id files are key=value lines
    if source.startswith("/") or "sys/class/dmi" in source:
        kv: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                kv[k.strip()] = v.strip()
        return {
            "manufacturer": kv.get("sys_vendor"),
            "product_name": kv.get("product_name"),
            "product_version": kv.get("product_version"),
            "board_name": kv.get("board_name"),
            "board_vendor": kv.get("board_vendor"),
            "source": "sysfs",
        }

    def _field(name: str) -> str | None:
        m = re.search(rf"{re.escape(name)}:\s*(.+)", text)
        return m.group(1).strip() if m else None

    return {
        "manufacturer": _field("Manufacturer"),
        "product_name": _field("Product Name"),
        "product_version": _field("Version"),
        "board_name": None,
        "board_vendor": None,
        "source": "dmidecode",
    }


def read_sysfs_dmi() -> str:
    """Helper used by fallbacks: concatenate /sys/class/dmi/id key files."""
    base = Path("/sys/class/dmi/id")
    parts: list[str] = []
    for name in (
        "sys_vendor",
        "product_name",
        "product_version",
        "product_serial",
        "board_vendor",
        "board_name",
    ):
        p = base / name
        if p.exists():
            try:
                parts.append(f"{name}={p.read_text(encoding='utf-8', errors='replace').strip()}")
            except OSError:
                continue
    return "\n".join(parts)
