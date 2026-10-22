"""Gamepad play (M0-07, M9-02) with the layout of ``data/ui/gamepad.json`` (GDD 12.3).

Uses SDL's game-controller API (pygame._sdl2.controller), so any pad SDL knows (Xbox, PlayStation, Steam Deck)
has the same buttons. Like the keyboard, it only turns input into the same commands the mouse sends: walking is
a ``MoveTo`` a step ahead of the hero, skills go through :meth:`Game.use_skill` aimed at the nearest monster or at
the right stick. Menus (title, options, the game menu) move with the d-pad and press with A.
"""

from __future__ import annotations

import json
import math
from importlib import resources
from typing import TYPE_CHECKING

import pygame

from ..core.world import AttackInPlace, AttackTarget, MoveTo, PickUp, SetGuard

if TYPE_CHECKING:
    from .app import Game

AXES = ("leftx", "lefty", "rightx", "righty", "triggerleft", "triggerright")
AXIS_MAX = 32767


def load_config() -> dict:
    return json.loads((resources.files("aethelgard") / "data" / "ui" / "gamepad.json").read_text(encoding="utf-8"))


def button_code(name: str) -> int:
    return getattr(pygame, f"CONTROLLER_BUTTON_{name.upper()}")


class Gamepad:
    def __init__(self, game: Game, cfg: dict | None = None):
        self.g = game
        self.cfg = cfg or load_config()
        self.pads: dict[int, object] = {}  # instance id → controller
        self.axes = dict.fromkeys(AXES, 0.0)  # -1..1 (triggers 0..1); tests set these directly
        self.held: set[int] = set()
        self.walking = False
        self.guarding = False
        c = self.cfg
        self._slot_of = {button_code(n): i for i, n in enumerate(c["skillBar"])}
        self._shifted = {button_code(n): len(c["skillBar"]) + i for i, n in enumerate(c["shiftedBar"])}
        self._actions = {button_code(c[a]): a for a in ("attack", "lifePotion", "energyPotion", "townPortal", "standStill", "menu", "map",
                                                        "inventory", "skillTrees")}
        self.shift = button_code(c["shift"])
        try:
            from pygame._sdl2 import controller

            controller.init()
            self._controller = controller
            for i in range(controller.get_count()):
                self._open(i)
        except (ImportError, pygame.error):
            self._controller = None

    @property
    def active(self) -> bool:
        return bool(self.pads) or self.walking or self.guarding

    def _open(self, index: int) -> None:
        try:
            pad = self._controller.Controller(index)
            self.pads[pad.as_joystick().get_instance_id()] = pad
        except (pygame.error, AttributeError):
            pass

    # ---- events

    def handle(self, ev: pygame.event.Event) -> bool:
        """True when the event was the pad's."""
        if ev.type == pygame.CONTROLLERDEVICEADDED and self._controller is not None:
            self._open(ev.device_index)
            self.g.hud.add(self.g.tr("gamepad.connected"), (200, 200, 160))
            return True
        if ev.type == pygame.CONTROLLERDEVICEREMOVED:
            self.pads.pop(ev.instance_id, None)
            return True
        if ev.type == pygame.CONTROLLERAXISMOTION:
            name = AXES[ev.axis] if 0 <= ev.axis < len(AXES) else None
            if name:
                self.axes[name] = ev.value / AXIS_MAX
            return True
        if ev.type == pygame.CONTROLLERBUTTONUP:
            self.held.discard(ev.button)
            self._release(ev.button)
            return True
        if ev.type != pygame.CONTROLLERBUTTONDOWN:
            return False
        self.held.add(ev.button)
        self.press(ev.button)
        return True

    def _menu(self):
        g = self.g
        for panel in (g.options, g.credits, g.select, g.title, g.menu):
            if panel.open:
                return panel
        return None

    def press(self, button: int) -> None:
        g = self.g
        panel = self._menu()
        if panel is not None:  # menus: d-pad moves, A presses, B (or Start) goes back
            if button in (pygame.CONTROLLER_BUTTON_DPAD_UP, pygame.CONTROLLER_BUTTON_DPAD_DOWN):
                panel.move_focus(-1 if button == pygame.CONTROLLER_BUTTON_DPAD_UP else 1)
            elif button == pygame.CONTROLLER_BUTTON_A:
                panel.press_focus()
            elif button in (pygame.CONTROLLER_BUTTON_B, pygame.CONTROLLER_BUTTON_START):
                if panel is g.menu:
                    g.menu.open = False
                else:
                    panel.back()
            return
        if g.dead:
            if button in (pygame.CONTROLLER_BUTTON_A, pygame.CONTROLLER_BUTTON_START):
                g.respawn()
            return
        shifted = self.shift in self.held
        if shifted and button in self._shifted:
            g.use_slot(self._shifted[button], aim=self.aim_point())
            return
        if button in self._slot_of:
            g.use_slot(self._slot_of[button], aim=self.aim_point())
            return
        action = self._actions.get(button)
        if action == "attack":
            self.attack()
        elif action == "standStill":  # strike toward the aim without moving
            ax, ay = self.aim_point()
            g.world.apply(AttackInPlace(g.session.player_id, ax, ay))
        elif action is not None:
            g.do_action("cancel" if action == "menu" else action)

    def _release(self, button: int) -> None:
        slot = self._shifted.get(button) if self.shift in self.held else self._slot_of.get(button)
        if slot is not None:
            self.g._release(self.g.session.skill_bar.skill_at(slot))

    # ---- what A does

    def attack(self) -> None:
        """The nearest monster in reach; else whatever is next to you (an item, the merchant, the stash)."""
        g, p, c = self.g, self.g.player, self.cfg
        pid = g.session.player_id
        target = g.nearest_monster(c["attackReachTiles"])
        if target is not None and not p.guarding:
            g.world.apply(AttackTarget(pid, target.id))
            return
        near = [(math.hypot(d.x - p.x, d.y - p.y), i) for i, d in g.world.ground.items()]
        near = [t for t in near if t[0] <= c["pickupReachTiles"]]
        if near:
            g.world.apply(PickUp(pid, min(near)[1]))
            return
        for role in ("vendor", "stash"):
            if g.world.npc_near(p, role):
                g._open_service(role)
                return
        if g.world.at_waypoint(p):
            g.open_waypoints()

    # ---- every frame

    def stick(self, x: str, y: str) -> tuple[float, float] | None:
        sx, sy = self.axes[x], self.axes[y]
        n = math.hypot(sx, sy)
        if n < self.cfg["deadZone"]:
            return None
        return sx / n, sy / n

    def to_grid_dir(self, sx: float, sy: float) -> tuple[float, float]:
        """A screen direction (the stick) as a grid direction: up on the stick is up on the screen."""
        g = self.g
        cx, cy = g.cam.to_screen(g.player.x, g.player.y)
        gx, gy = g.cam.to_grid(cx + sx * 100, cy + sy * 100)
        dx, dy = gx - g.player.x, gy - g.player.y
        n = math.hypot(dx, dy) or 1.0
        return dx / n, dy / n

    def aim_point(self) -> tuple[float, float]:
        """Right stick if pushed, else the left one, else straight ahead."""
        p = self.g.player
        st = self.stick("rightx", "righty") or self.stick("leftx", "lefty")
        if st is None:
            dx, dy = p.face_x, p.face_y
        else:
            dx, dy = self.to_grid_dir(*st)
        return p.x + dx * self.cfg["aimTiles"], p.y + dy * self.cfg["aimTiles"]

    def update(self) -> None:
        """Left stick walks; the left trigger holds the guard toward the right stick (or ahead)."""
        g, p = self.g, self.g.player
        pid = g.session.player_id
        guard = self.axes["triggerleft"] >= self.cfg["triggerPress"]
        if guard or self.guarding:
            ax, ay = self.aim_point()
            g.world.apply(SetGuard(pid, guard, ax - p.x, ay - p.y))
            self.guarding = guard
        st = self.stick("leftx", "lefty")
        if st is None:
            if self.walking:  # let go: stop where you are
                g.world.apply(MoveTo(pid, p.x, p.y))
                self.walking = False
            return
        dx, dy = self.to_grid_dir(*st)
        step = self.cfg["stepTiles"]
        g.world.apply(MoveTo(pid, p.x + dx * step, p.y + dy * step))
        self.walking = True
