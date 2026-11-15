"""Allow-listed system inspection tools.

The LLM never generates command strings. It selects a tool NAME; the
executor maps that name to a fixed argv defined here. Every command is
read-only. Tools that need root declare requires_root=True and are invoked
via non-interactive `sudo -n` only when the process is not already root.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    argv: list[str]
    requires_root: bool = False
    fallbacks: tuple[str, ...] = field(default_factory=tuple)


ALLOW_LISTED_TOOLS: dict[str, ToolSpec] = {
    "get_cpu_info": ToolSpec(
        argv=["lscpu"],
        fallbacks=("lscpu",),
    ),
    "get_memory_summary": ToolSpec(
        argv=["free", "-b"],
        fallbacks=("free",),
    ),
    "get_memory_topology": ToolSpec(
        argv=["/usr/sbin/dmidecode", "-t", "memory"],
        requires_root=True,
        fallbacks=("/usr/sbin/dmidecode", "dmidecode"),
    ),
    "get_block_devices": ToolSpec(
        argv=["lsblk", "-J", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,MODEL,ROTA,TRAN"],
        fallbacks=("lsblk",),
    ),
    "get_gpu_info": ToolSpec(
        argv=["lspci"],
        fallbacks=("lspci",),
    ),
    "get_dmi_system": ToolSpec(
        argv=["/usr/sbin/dmidecode", "-t", "system"],
        requires_root=True,
        fallbacks=("/usr/sbin/dmidecode", "dmidecode", "/sys/class/dmi/id"),
    ),
    "get_pci_devices": ToolSpec(
        argv=["lspci"],
        fallbacks=("lspci",),
    ),
}
