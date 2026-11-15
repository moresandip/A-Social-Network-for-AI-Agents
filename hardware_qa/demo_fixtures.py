"""Fixture payloads used by --demo mode.

Lets the assistant run on machines without Linux inspection tools (or on
non-Linux dev boxes) by returning realistic, schema-correct data instead of
live command output. Clearly labeled as demo data in the CLI output.
"""

from __future__ import annotations

import copy
from typing import Any

_FIXTURES: dict[str, dict[str, Any]] = {
    "get_cpu_info": {
        "model_name": "Intel(R) Core(TM) i7-1165G7 @ 2.80GHz",
        "cores_per_socket": 4,
        "sockets": 1,
        "cpus": 8,
        "mhz": "2800.0000",
        "architecture": "x86_64",
        "raw": {"Model name": "Intel(R) Core(TM) i7-1165G7 @ 2.80GHz"},
    },
    "get_memory_summary": {
        "total_gb": 15.6,
        "used_gb": 4.2,
        "free_gb": 2.1,
        "available_gb": 11.0,
        "source": "free -b",
    },
    "get_memory_topology": {
        "total_slots": 2,
        "used_slots": 1,
        "free_slots": 1,
        "maximum_capacity": "64 GB",
        "module_details": [
            {
                "installed": True,
                "size": "16 GB",
                "speed": "3200 MT/s",
                "manufacturer": "Samsung",
                "part_number": "M471A5244CB0-CWE",
            },
            {"installed": False, "size": "No Module Installed"},
        ],
        "note": "Demo fixture data.",
    },
    "get_block_devices": {
        "devices": [
            {
                "name": "nvme0n1",
                "size": "476.9G",
                "type": "disk",
                "mountpoint": "/",
                "model": "Samsung SSD 980 500GB",
                "rota": 0,
                "tran": "nvme",
                "is_nvme": True,
                "is_ssd": True,
            },
            {
                "name": "nvme0n1p1",
                "size": "512M",
                "type": "part",
                "mountpoint": "/boot/efi",
                "model": None,
                "rota": 0,
                "tran": "nvme",
                "is_nvme": True,
                "is_ssd": True,
            },
        ],
        "has_nvme": True,
    },
    "get_gpu_info": {
        "gpus": [
            "00:02.0 VGA compatible controller: Intel Corporation Iris Xe Graphics (rev 01)",
        ]
    },
    "get_dmi_system": {
        "manufacturer": "Dell Inc.",
        "product_name": "XPS 13 9310",
        "product_version": "Not Specified",
        "board_name": "0K3WRG",
        "board_vendor": "Dell Inc.",
        "source": "demo fixture",
    },
    "get_pci_devices": {
        "devices": [
            "00:00.0 Host bridge: Intel Corporation Device 9a08",
            "00:02.0 VGA compatible controller: Intel Corporation Iris Xe Graphics (rev 01)",
            "02:00.0 Non-Volatile memory controller: Samsung Electronics Co Ltd NVMe SSD Controller S4LV008[Pascal]",
        ],
        "count": 3,
    },
}


def get_demo_payload(tool_name: str) -> dict[str, Any]:
    """Return a deep copy of the fixture for a tool, or an error dict."""
    fixture = _FIXTURES.get(tool_name)
    if fixture is None:
        return {"error": f"No demo fixture available for tool '{tool_name}'."}
    return copy.deepcopy(fixture)
