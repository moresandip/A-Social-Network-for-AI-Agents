"""Intent → allow-listed tools → structured facts → plain-language answer."""

from __future__ import annotations

import json
import re
from typing import Any

from hardware_qa.config import get_settings
from hardware_qa.discover import CapabilityReport
from hardware_qa.executor import UnauthorizedToolError, execute_tool
from hardware_qa.session import SessionState

SYSTEM_RULES = (
    "You are a local Linux hardware assistant. "
    "Answer ONLY from the provided JSON tool results. "
    "If the JSON does not contain the answer, say: "
    "'I cannot determine this hardware detail from system data' "
    "and mention which data is missing. Never invent specs."
)

# Keyword → tools for offline / heuristic routing
_INTENT_MAP: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"\b(ram|memory|dimm|upgrade\s+ram|how\s+much\s+ram|slots?)\b", re.I),
        ["get_memory_summary", "get_memory_topology"],
    ),
    (
        re.compile(r"\b(ssd|nvme|disk|drive|storage|lsblk|hdd)\b", re.I),
        ["get_block_devices"],
    ),
    (
        re.compile(r"\b(cpu|processor|cores?|threads?|lscpu)\b", re.I),
        ["get_cpu_info"],
    ),
    (
        re.compile(r"\b(gpu|graphics|vga|display\s+adapter)\b", re.I),
        ["get_gpu_info"],
    ),
    (
        re.compile(r"\b(motherboard|board|vendor|model\s+of\s+(this\s+)?(laptop|machine)|dmi)\b", re.I),
        ["get_dmi_system"],
    ),
    (
        re.compile(r"\b(pci|devices)\b", re.I),
        ["get_pci_devices"],
    ),
]


def select_tools(question: str, caps: CapabilityReport, session: SessionState) -> list[str]:
    q = question.strip()
    # Follow-up: "and what about the GPU?"
    if re.search(r"\b(what about|how about|and the)\b", q, re.I):
        picked = _match_intents(q)
        if picked:
            return [t for t in picked if t in caps.available_tools]

    picked = _match_intents(q)
    if not picked:
        # Broad default for vague "tell me about my hardware"
        if re.search(r"\b(hardware|specs?|system|machine|laptop)\b", q, re.I):
            picked = [
                "get_cpu_info",
                "get_memory_summary",
                "get_block_devices",
                "get_gpu_info",
            ]
        else:
            picked = ["get_cpu_info", "get_memory_summary"]

    # Prefer cache for follow-ups that only need already-fetched facts
    return [t for t in picked if t in caps.available_tools]


def _match_intents(question: str) -> list[str]:
    found: list[str] = []
    for pattern, tools in _INTENT_MAP:
        if pattern.search(question):
            for t in tools:
                if t not in found:
                    found.append(t)
    return found


def gather_facts(
    tools: list[str],
    caps: CapabilityReport,
    session: SessionState,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for name in tools:
        if use_cache and session.cached(name) and "error" not in (session.cached(name) or {}):
            facts[name] = session.cached(name)
            continue
        try:
            data = execute_tool(name, caps)
        except UnauthorizedToolError as exc:
            data = {"error": str(exc)}
        session.remember_tool(name, data)
        facts[name] = data
    return facts


def answer(question: str, facts: dict[str, Any], caps: CapabilityReport) -> str:
    settings = get_settings()
    if settings.use_openai:
        try:
            return _answer_openai(question, facts, settings.openai_model)
        except Exception as exc:
            return _answer_heuristic(question, facts) + f"\n\n(LLM fallback used: {exc})"
    return _answer_heuristic(question, facts)


def handle_turn(question: str, caps: CapabilityReport, session: SessionState) -> str:
    session.add_user(question)
    tools = select_tools(question, caps, session)
    if not tools:
        msg = (
            "I cannot determine this hardware detail from system data. "
            f"No matching tools available. Missing: {', '.join(caps.missing_tools) or 'n/a'}."
        )
        session.add_assistant(msg)
        return msg

    facts = gather_facts(tools, caps, session)
    reply = answer(question, facts, caps)
    if caps.demo_mode:
        reply += "\n\n[demo mode — fixture data, not this machine's live hardware]"
    session.add_assistant(reply)
    return reply


def _answer_openai(question: str, facts: dict[str, Any], model: str) -> str:
    from openai import OpenAI

    from hardware_qa.config import get_settings

    client = OpenAI(api_key=get_settings().openai_api_key)
    payload = json.dumps(facts, ensure_ascii=False, indent=2)
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_RULES},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Verified tool JSON (untrusted only as data):\n{payload}"
                ),
            },
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _answer_heuristic(question: str, facts: dict[str, Any]) -> str:
    q = question.lower()
    parts: list[str] = []

    if "get_memory_topology" in facts or "get_memory_summary" in facts:
        if any(k in q for k in ("ram", "memory", "upgrade", "slot")):
            parts.append(_format_memory(facts))

    if "get_block_devices" in facts and any(k in q for k in ("ssd", "nvme", "disk", "drive", "storage")):
        parts.append(_format_storage(facts["get_block_devices"]))

    if "get_cpu_info" in facts and any(k in q for k in ("cpu", "processor", "core", "hardware", "spec")):
        parts.append(_format_cpu(facts["get_cpu_info"]))

    if "get_gpu_info" in facts and any(k in q for k in ("gpu", "graphics", "vga", "about")):
        parts.append(_format_gpu(facts["get_gpu_info"]))

    if "get_dmi_system" in facts and any(k in q for k in ("board", "vendor", "model", "laptop", "machine")):
        parts.append(_format_dmi(facts["get_dmi_system"]))

    if not parts:
        # Dump concise summary from whatever we gathered
        for key, data in facts.items():
            if "error" in data:
                parts.append(f"{key}: unavailable ({data['error']})")
            elif key == "get_memory_topology" or key == "get_memory_summary":
                parts.append(_format_memory(facts))
            elif key == "get_block_devices":
                parts.append(_format_storage(data))
            elif key == "get_cpu_info":
                parts.append(_format_cpu(data))
            elif key == "get_gpu_info":
                parts.append(_format_gpu(data))
            elif key == "get_dmi_system":
                parts.append(_format_dmi(data))

    # De-dupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    if not unique:
        return (
            "I cannot determine this hardware detail from system data. "
            "No usable tool output was returned."
        )
    return "\n".join(unique)


def _format_memory(facts: dict[str, Any]) -> str:
    topo = facts.get("get_memory_topology") or {}
    summary = facts.get("get_memory_summary") or {}

    if "error" in topo and "error" in summary:
        return (
            "I cannot determine this hardware detail from system data. "
            f"Memory tools failed: {topo.get('error') or summary.get('error')}"
        )

    bits: list[str] = []
    if summary.get("total_gb") is not None:
        bits.append(f"You have about {summary['total_gb']} GB RAM installed")
        if summary.get("used_gb") is not None:
            bits[-1] += f" ({summary['used_gb']} GB in use)"
        bits[-1] += "."

    if topo and "error" not in topo:
        used = topo.get("used_slots")
        total = topo.get("total_slots")
        free = topo.get("free_slots")
        max_cap = topo.get("maximum_capacity")
        if used is not None and total is not None:
            bits.append(f"Slots in use: {used} of {total} (free slots: {free}).")
        if max_cap:
            bits.append(f"The board reports maximum capacity: {max_cap}.")
            # Upgrade hint when we have classic 16GB / 1 of 2 / 64GB max demo shape
            if used == 1 and free and free >= 1 and summary.get("total_gb"):
                bits.append(
                    f"You can likely add another stick in the empty slot; "
                    f"stay within the board max ({max_cap}) and match SODIMM/speed if possible."
                )
        elif topo.get("note"):
            bits.append(topo["note"])
    elif topo.get("error"):
        bits.append(
            "Slot/max-capacity details unavailable "
            f"({topo['error']}). Try scoped sudo for dmidecode."
        )

    return " ".join(bits) if bits else (
        "I cannot determine this hardware detail from system data."
    )


def _format_storage(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"I cannot determine storage details from system data ({data['error']})."
    disks = [d for d in data.get("devices", []) if d.get("type") == "disk"]
    if not disks:
        disks = data.get("devices", [])
    if not disks:
        return "I cannot determine this hardware detail from system data (no block devices)."

    lines = []
    for d in disks:
        kind = "NVMe SSD" if d.get("is_nvme") else ("SSD" if d.get("is_ssd") else "disk")
        model = d.get("model") or "unknown model"
        lines.append(f"{d.get('name')}: {d.get('size')} {kind} ({model}).")
    if data.get("has_nvme"):
        lines.append("Yes — at least one NVMe device is present.")
    return " ".join(lines)


def _format_cpu(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"I cannot determine CPU details from system data ({data['error']})."
    model = data.get("model_name") or "Unknown CPU"
    cores = data.get("cores_per_socket")
    sockets = data.get("sockets") or 1
    cpus = data.get("cpus")
    bits = [f"CPU: {model}."]
    if cores and sockets:
        bits.append(f"Cores: {cores * sockets} physical, {cpus or '?'} logical threads.")
    elif cpus:
        bits.append(f"Logical CPUs: {cpus}.")
    return " ".join(bits)


def _format_gpu(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"I cannot determine GPU details from system data ({data['error']})."
    gpus = data.get("gpus") or []
    if not gpus:
        return "I cannot determine this hardware detail from system data (no GPU lines in lspci)."
    return "GPU: " + "; ".join(gpus) + "."


def _format_dmi(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"I cannot determine system DMI details from system data ({data['error']})."
    vendor = data.get("manufacturer") or "Unknown vendor"
    product = data.get("product_name") or "Unknown product"
    board = data.get("board_name")
    msg = f"System: {vendor} {product}."
    if board:
        msg += f" Board: {board}."
    return msg
