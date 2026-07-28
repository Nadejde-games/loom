"""The modifier / effect stack (Phase 9, Tier 1). See docs/spikes/rpg-systems.md Tier 1
"Gear & the modifier/effect stack".

One shared computation underlies gear bonuses, ability buffs/debuffs (Tier 2), and hunger
penalties (Tier 3): the **effective sheet** = base stats → apply all active modifiers → recompute
derivations → clamp pools. A :class:`Modifier` is one ``{target, op, value}`` apply-pair
(CircleMUD's ``struct affected_type`` apply-location + modifier); an :class:`Effect` is a
*timed* bundle of them (a buff/debuff with a duration/source) — the seam abilities and hunger
reuse. The recompute itself lives on the :class:`~loom.rpg.sheet.Sheet` (it owns the values);
this module ships the data types.

Signed off: **additive-first** stacking — same-target ``add`` modifiers sum. A ``type`` field is
**reserved** so typed/capped stacking (D&D bonus types, ARPG "increased/more") drops in later
with no change to this mechanism or its callers. ``op`` currently supports only ``"add"``; other
ops are reserved likewise. Pure data — no engine, no world; the engine translates equipped gear
into modifiers tagged by ``source`` so the sheet never holds an item reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Modifier:
    """One apply-pair. ``target`` names what it adjusts — a base attribute (``"str"``), a derived
    value (``"prof"``), or a pool bound (``"max_hp"`` / ``"regen_mana"``). ``value`` is the amount;
    ``op`` is ``"add"`` (the only op today). ``type`` is reserved for typed/capped stacking.
    ``source`` records what granted it (an item id, an effect name) so it can be removed as a
    unit on unequip or expiry."""
    target: str
    value: int
    op: str = "add"
    type: str = ""
    source: str = ""

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "Modifier":
        """Parse an authored apply-pair (item ``modifiers`` in world.json). ``source`` is not
        authored — the engine tags it when the item is equipped."""
        return cls(target=str(cfg["target"]), value=int(cfg.get("value", 0)),
                   op=str(cfg.get("op", "add")), type=str(cfg.get("type", "")))

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Modifier":
        """Rebuild a modifier from its persisted form (includes ``source``)."""
        return cls(target=str(d.get("target", "")), value=int(d.get("value", 0)),
                   op=str(d.get("op", "add")), type=str(d.get("type", "")),
                   source=str(d.get("source", "")))

    def to_dict(self) -> dict:
        return {"target": self.target, "value": self.value, "op": self.op,
                "type": self.type, "source": self.source}


@dataclass
class Effect:
    """A timed bundle of modifiers — a buff/debuff (Tier 2) or a hunger penalty (Tier 3). Applied
    and removed as a unit; ``expires_pulse`` (when set) is the loop pulse it lifts on. Tier 0/1
    define the shape; the expiry loop system arrives with the systems that grant durations."""
    name: str
    modifiers: list = field(default_factory=list)       # list[Modifier]
    expires_pulse: Any = None                           # int | None (None ⇒ permanent)
    source: str = ""
