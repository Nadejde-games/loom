"""The character sheet — the single new primitive the whole RPG arc hangs off (Phase 9,
Tier 0). See docs/spikes/rpg-systems.md §D–§G.

A :class:`Sheet` is an **optional** component on a ``Character``: base stats, resource pools,
level/xp, build points, and use-based skills. Hard mechanics read and write it; the soft world
perceives it only *qualitatively* (§E — an onlooker or NPC mind sees "wounded", never
``HP 12/50``; exact numbers appear solely on the owner's own ``score``). Odd, Wren, and every
purely-narrative NPC keep no sheet and behave exactly as before — the field defaults to ``None``
and nothing in the engine touches it.

A :class:`Ruleset` is the game's declared RPG vocabulary, built once from the ``"stats"`` block
of ``world.json`` (the same enum-free, rules-as-data split the clock and loot forge use): the
:class:`~loom.rpg.stats.StatSet`, the :class:`~loom.rpg.pool.PoolSpec` list, and the default
hit-die. It mints sheets (``make_sheet``) with pools initialised from their derivations. A sheet
holds a reference to its ruleset, so it derives, recomputes, perceives, and renders on its own —
the engine only calls ``render`` / ``health_descriptor`` / ``regen_tick`` by duck type and never
imports this package.

Pure and deterministic: no engine, no I/O. Live state (pool ``current``, level, xp, points,
skills, any trained stats) round-trips through the save overlay via ``live_state`` /
``load_live_state`` — tolerant, so an older save loads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .effects import Modifier
from .expr import ExprError
from .pool import Pool, PoolSpec
from .progression import Progression
from .skills import SkillSet
from .stats import StatSet
from .template import Template


@dataclass
class Sheet:
    """A character's live mechanical state. Constructed via :meth:`Ruleset.make_sheet`; carries
    a reference to its :class:`Ruleset` so every derivation and cue resolves without the
    engine."""
    ruleset: "Ruleset"
    stats: dict = field(default_factory=dict)       # base attribute values (mutable: Tier 1 train)
    level: int = 1
    xp: int = 0
    hit_die: int = 8
    points: int = 0                                 # unspent build points (Tier 1)
    skills: dict = field(default_factory=dict)      # name -> {"xp": int, "level": int}
    pools: dict = field(default_factory=dict)       # key -> Pool, in declared order
    equipment: dict = field(default_factory=dict)   # slot -> item_id worn (Tier 1)
    modifiers: list = field(default_factory=list)   # active Modifier stack (gear + effects)

    # --- the modifier stack: base → +modifiers → effective (Tier 1) ---------

    def effective_stats(self) -> dict:
        """Base attributes with all active **stat** modifiers folded in (additive) — the values
        derivations are computed from, so gear's ``+2 STR`` flows through to ``str_mod`` and on to
        every pool and formula."""
        eff = dict(self.stats)
        for m in self.modifiers:
            if m.op == "add" and m.target in eff:
                eff[m.target] = eff[m.target] + m.value
        return eff

    def _modifier_sum(self, target: str) -> int:
        return sum(m.value for m in self.modifiers if m.op == "add" and m.target == target)

    # --- derivation namespace ----------------------------------------------

    def namespace(self) -> dict:
        """The map every pool / XP / combat formula reads from: the **effective** attribute values,
        the declared derived values (``con_mod``, ``prof``), and the sheet's own scalars —
        ``level``, ``hit_die``, and each skill's level (so ``bow to-hit = dex_mod + archery``
        resolves). Modifiers that target a *derived* name fold in after derivation; modifiers on a
        pool bound (``max_hp``) are applied in :meth:`recompute`."""
        skill_levels = {name: int(sk.get("level", 0)) for name, sk in self.skills.items()}
        extra = {"level": self.level, "hit_die": self.hit_die, **skill_levels}
        ns = self.ruleset.stats.derive(self.effective_stats(), extra=extra)
        for m in self.modifiers:
            if m.op == "add" and m.target in ns and m.target not in self.stats:
                ns[m.target] = ns[m.target] + m.value
        return ns

    def recompute(self) -> None:
        """Re-derive every pool's ``max``/``regen`` (after a level-up, a trained stat, an
        equip/unequip, or a load), apply any ``max_<pool>`` / ``regen_<pool>`` gear modifiers, and
        re-clamp each ``current`` into its new bounds."""
        ns = self.namespace()
        for key, pool in self.pools.items():
            pool.recompute(ns)
            bonus_max = self._modifier_sum(f"max_{key}")
            if bonus_max:
                pool.max = max(pool.spec.floor, pool.max + bonus_max)
            bonus_regen = self._modifier_sum(f"regen_{key}")
            if bonus_regen:
                pool.regen += bonus_regen
            pool.set(pool.current)                  # re-clamp to the modified max

    # --- gear: equip / unequip (translate item modifiers onto the stack) ----

    def equip(self, slot: str, item_id: str, modifiers) -> dict:
        """Wear ``item_id`` in ``slot``: remove whatever occupied the slot, fold the item's
        ``modifiers`` (dicts or :class:`Modifier`) onto the stack tagged by ``source=item_id``,
        and recompute. Returns ``{ok, replaced}`` — ``replaced`` is the item id displaced, if any."""
        if self.equipment.get(slot) == item_id:
            return {"ok": True, "replaced": None, "already": True}
        replaced = self.equipment.get(slot)
        if replaced:
            self.remove_source(replaced)
        self.equipment[slot] = item_id
        for m in modifiers or []:
            mod = m if isinstance(m, Modifier) else Modifier.from_config(m)
            self.modifiers.append(Modifier(target=mod.target, value=mod.value, op=mod.op,
                                           type=mod.type, source=item_id))
        self.recompute()
        return {"ok": True, "replaced": replaced, "already": False}

    def unequip_item(self, item_id: str) -> dict:
        """Take off ``item_id``: clear its slot, strip its modifiers, and recompute. Returns
        ``{ok, slot}`` — ``ok=False`` if it was not worn."""
        slot = next((s for s, i in self.equipment.items() if i == item_id), None)
        if slot is None:
            return {"ok": False, "slot": ""}
        del self.equipment[slot]
        self.remove_source(item_id)
        self.recompute()
        return {"ok": True, "slot": slot}

    def remove_source(self, source: str) -> None:
        """Drop every modifier granted by ``source`` (an item id or effect name)."""
        self.modifiers = [m for m in self.modifiers if m.source != source]

    def is_equipped(self, item_id: str) -> bool:
        return item_id in self.equipment.values()

    def regen_tick(self) -> None:
        """One regen step across all pools — called by the :class:`~loom.rpg.pool.RegenSystem`."""
        for pool in self.pools.values():
            pool.apply_regen()

    # --- growth: XP → levels → build points → trained stats (Tier 1) --------

    def grant_xp(self, amount: int) -> dict:
        """Award XP and level up while the running total crosses the next threshold — each level
        crossed grants ``points_per_level`` build points and recomputes pools (so HP/mana grow).
        A single grant may cross several levels (catch-up). Returns ``{gained, level, xp, points}``.
        A no-op returning ``gained=0`` when the world declares no progression rule."""
        prog = getattr(self.ruleset, "progression", None)
        if prog is None:
            return {"gained": 0, "level": self.level, "xp": self.xp, "points": self.points}
        self.xp += max(0, int(amount))
        gained = 0
        cap = prog.cap()
        while self.level < cap:
            need = prog.xp_to_level(self.level + 1)
            if need <= prog.xp_to_level(self.level) or self.xp < need:
                break                                   # non-increasing curve, or not there yet
            self.level += 1
            self.points += prog.points_per_level
            gained += 1
        if gained:
            self.recompute()
        return {"gained": gained, "level": self.level, "xp": self.xp, "points": self.points}

    def train(self, stat_name: str, *, by: int = 1) -> dict:
        """Raise a base attribute by ``by``, paying from build points via the point-buy cost table
        (§H, Tier 1 — the escalating cost, one table for creation and growth). Recomputes derived
        values and pool maxima on success. Returns ``{ok, ...}``: ``ok=False`` with a ``reason``
        when the attribute is unknown, at its trainable ceiling, or unaffordable."""
        stats = self.ruleset.stats
        stat = stats.get(stat_name)
        if stat is None:
            return {"ok": False, "reason": f"there is no attribute called '{stat_name}'"}
        point_buy = stats.point_buy
        if point_buy is None:
            return {"ok": False, "reason": "this world has no training economy"}
        current = int(self.stats.get(stat_name, stat.default))
        target = current + int(by)
        ceiling = min(stat.max, point_buy.ceil)
        if target > ceiling:
            return {"ok": False, "reason": f"{stat_name} is already at its trainable maximum ({ceiling})"}
        try:
            cost = point_buy.cost_of_score(target) - point_buy.cost_of_score(current)
        except ExprError:
            return {"ok": False, "reason": f"{stat_name} is outside the trainable range"}
        if cost > self.points:
            return {"ok": False, "reason": f"training {stat_name} costs {cost} points; you have {self.points}"}
        self.points -= cost
        self.stats[stat_name] = target
        self.recompute()
        return {"ok": True, "stat": stat_name, "value": target, "spent": cost, "points": self.points}

    # --- qualitative perception (the golden rule, applied to minds, §E) -----

    def health_descriptor(self) -> str:
        """The single health cue an onlooker reads — the ``vital`` pool's condition (HP's
        ``bloodied``/``near death``), or empty when it is at full. Never a number."""
        vital = self._vital_pool()
        if vital is not None and vital.is_notable():
            return vital.condition()
        return ""

    def notable_cues(self) -> list:
        """The self-perception lines an NPC mind reasons over — each pool's cue that is worth
        surfacing (hurt, hungry), the healthy/full bands suppressed. Drives a wounded NPC to
        flee and a starving one to seek food, all in prose over cues, never numbers."""
        return [p.condition() for p in self.pools.values() if p.is_notable()]

    def _vital_pool(self) -> Pool | None:
        """The pool an onlooker's health descriptor reads: the one tagged ``policy == "vital"``,
        else the first declared pool (HP by convention)."""
        for pool in self.pools.values():
            if pool.spec.policy == "vital":
                return pool
        return next(iter(self.pools.values()), None)

    # --- the owner's exact surface (§F) ------------------------------------

    def render(self, name: str = "") -> str:
        """The ``score`` sheet — the owner's own exact numbers (the one place they appear).
        Attributes with their derived ``*_mod``, each pool ``current/max``, level/xp/points,
        and any skills."""
        ns = self.namespace()
        eff = self.effective_stats()
        lines = [f"== {name} ==" if name else "== Character sheet =="]
        head = f"Level {self.level}   XP {self.xp}"
        if self.points:
            head += f"   Unspent points {self.points}"
        lines.append(head)
        attrs = []
        for s in self.ruleset.stats.attributes:
            v = eff.get(s.name, s.default)          # effective (base + gear)
            mod = ns.get(f"{s.name}_mod")
            if isinstance(mod, (int, float)):
                attrs.append(f"{s.name.upper()} {v} ({'+' if mod >= 0 else ''}{int(mod)})")
            else:
                attrs.append(f"{s.name.upper()} {v}")
        if attrs:
            lines.append("  ".join(attrs))
        for key, pool in self.pools.items():
            lines.append(f"{key.upper():<6} {pool.current}/{pool.max}")
        if self.skills:
            lines.append("Skills: " + ", ".join(
                f"{n} L{d.get('level', 0)}" for n, d in self.skills.items()))
        if self.equipment:
            lines.append("Worn: " + ", ".join(
                f"{slot.replace('_', ' ')}" for slot in self.equipment))
        return "\n".join(lines)

    # --- persistence (live delta only; authored base reloads) ---------------

    def live_state(self) -> dict:
        """The mutable delta the save overlay carries — pool ``current`` values, level/xp/
        points, skills, and any trained base stats. The authored definition (vocabulary,
        formulas, pool specs) reloads from ``world.json``, so only what play changed persists."""
        return {
            "stats": dict(self.stats),
            "level": self.level,
            "xp": self.xp,
            "points": self.points,
            "skills": {n: dict(d) for n, d in self.skills.items()},
            "pools": {k: p.current for k, p in self.pools.items()},
            "equipment": dict(self.equipment),
            "modifiers": [m.to_dict() for m in self.modifiers],
        }

    def load_live_state(self, data: Mapping[str, Any]) -> None:
        """Compose a saved live state back onto a freshly-built sheet. Tolerant (every read a
        ``dict.get``); recomputes maxima from the restored stats/level *before* re-seating pool
        ``current`` values, so a level gained offline lands the larger pool correctly."""
        if not isinstance(data, dict):
            return
        if isinstance(data.get("stats"), dict):
            self.stats.update(self.ruleset.stats.coerce(data["stats"]))
        self.level = int(data.get("level", self.level))
        self.xp = int(data.get("xp", self.xp))
        self.points = int(data.get("points", self.points))
        if isinstance(data.get("skills"), dict):
            self.skills = {str(n): {"xp": int(d.get("xp", 0)), "level": int(d.get("level", 0))}
                           for n, d in data["skills"].items() if isinstance(d, dict)}
        if isinstance(data.get("equipment"), dict):
            self.equipment = {str(k): str(v) for k, v in data["equipment"].items()}
        if isinstance(data.get("modifiers"), list):
            self.modifiers = [Modifier.from_dict(m) for m in data["modifiers"]
                              if isinstance(m, dict)]
        self.recompute()                            # after gear loads, so maxima include it
        for key, current in (data.get("pools") or {}).items():
            if key in self.pools:
                self.pools[key].set(int(current))


@dataclass
class Ruleset:
    """The game's declared RPG vocabulary — the :class:`StatSet`, the :class:`PoolSpec` list,
    the :class:`~loom.rpg.skills.SkillSet`, the character :class:`~loom.rpg.template.Template`
    catalogue, and the default hit-die — built once from the ``"stats"`` block of
    ``world.json``. The factory for sheets, and the shared reference each sheet derives
    against."""
    stats: StatSet
    pools: list = field(default_factory=list)       # list[PoolSpec], declared order
    default_hit_die: int = 8
    skills: Any = None                              # SkillSet | None
    progression: Any = None                         # Progression | None
    templates: list = field(default_factory=list)   # list[Template]
    default_templates: list = field(default_factory=list)   # names composed for a new player

    def is_empty(self) -> bool:
        return self.stats.is_empty() and not self.pools

    def template(self, name: str) -> Template | None:
        return next((t for t in self.templates if t.name == name), None)

    def default_sheet(self, *, full: bool = True) -> "Sheet":
        """The single assigned starting sheet for a connecting player — composes the declared
        ``default`` template stack (§H)."""
        from .template import default_sheet
        return default_sheet(self, full=full)

    def make_sheet(self, *, stats: Mapping[str, int] | None = None, level: int = 1,
                   hit_die: int | None = None, skills: Mapping[str, Any] | None = None,
                   points: int = 0, full: bool = True) -> Sheet:
        """Mint a sheet: coerce ``stats`` against the vocabulary (fill defaults, clamp), build a
        live :class:`Pool` per spec, derive their maxima, and (``full``) start every pool at
        max. The one construction path — character creation and every NPC/mob sheet use it."""
        sheet = Sheet(
            ruleset=self,
            stats=self.stats.coerce(stats or {}),
            level=int(level),
            hit_die=int(hit_die if hit_die is not None else self.default_hit_die),
            points=int(points),
            skills={str(n): {"xp": int(d.get("xp", 0)), "level": int(d.get("level", 0))}
                    for n, d in (skills or {}).items() if isinstance(d, dict)},
            pools={ps.key: Pool(spec=ps) for ps in self.pools},
        )
        sheet.recompute()
        if full:
            for pool in sheet.pools.values():
                pool.current = pool.max
        return sheet

    @classmethod
    def from_meta(cls, cfg: Mapping[str, Any] | None) -> "Ruleset":
        """Build the ruleset from a world's ``"stats"`` meta block: ``attributes`` / ``derived``
        / ``point_buy`` (the :class:`StatSet`), a ``pools`` map (``key -> {max, regen, floor,
        policy, cues}``), and an optional default ``hit_die``."""
        cfg = cfg or {}
        pools = [PoolSpec.from_config(key, pc) for key, pc in (cfg.get("pools") or {}).items()]
        templates = [Template.from_config(t) for t in cfg.get("templates", []) if t.get("name")]
        return cls(stats=StatSet.from_config(cfg), pools=pools,
                   default_hit_die=int(cfg.get("hit_die", 8)),
                   skills=SkillSet.from_config(cfg.get("skills")),
                   progression=Progression.from_config(cfg.get("progression")),
                   templates=templates,
                   default_templates=[str(n) for n in cfg.get("default", [])])
