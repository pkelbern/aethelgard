"""Dragging skills to the bar, the right-click skill (M3-03) and the gamepad (M0-07, M9-02)."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from aethelgard.core.progression import Bind  # noqa: E402
from aethelgard.core.session import GameSession  # noqa: E402
from test_ranged_and_leaders import add_monster  # noqa: E402

# ---------------------------------------------------------------- core


def test_the_right_click_skill_is_saved_and_dropped_with_the_skill(data):
    s = GameSession(data, 1)
    assert s.skill_bar.set_right("fire_bolt") is Bind.NOT_LEARNED
    s.grant_skill("fire_bolt")
    assert s.skill_bar.set_right("fire_bolt") is Bind.OK
    back = GameSession.load(data, s.save())
    assert back.skill_bar.right == "fire_bolt"
    back.skills.set_level("fire_bolt", 0)
    back.skill_bar.drop_unusable()
    assert back.skill_bar.right is None


def test_swapping_bar_slots(data):
    s = GameSession(data, 1)
    s.grant_skill("fire_bolt")
    first = s.skill_bar.slot_of("fire_bolt")
    s.skill_bar.swap(first, 5)
    assert s.skill_bar.skill_at(5) == "fire_bolt" and s.skill_bar.skill_at(first) != "fire_bolt"


# ---------------------------------------------------------------- client


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELGARD_SAVE_DIR", str(tmp_path))
    from aethelgard.client.app import Game

    g = Game(seed=3, new_game=True, download_art=False)
    g.load_area("plains")
    yield g
    pygame.quit()


def drag(g, a, b):
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=a))
    pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=b, rel=(b[0] - a[0], b[1] - a[1]), buttons=(1, 0, 0)))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=b))
    g.tick()


def bar_rects(g):
    n = len(g.session.skill_bar)
    return g.hud.slot_rects(n, g.screen.get_size()), g.hud.right_rect(n, g.screen.get_size())


def test_dragging_from_the_trees_to_the_bar_and_to_the_right_click(game):
    g = game
    g.session.grant_skill("fire_bolt")
    g.session.skill_bar.clear(g.session.skill_bar.slot_of("fire_bolt"))
    g.open_only(g.tree_view)
    g.tree_view.tab = "pyromancy"
    g.tick()
    bolt = next(box for box, node_id in g.tree_view._nodes if node_id == "fire_bolt")
    slots, right = bar_rects(g)
    drag(g, bolt.center, slots[4].center)
    assert g.session.skill_bar.skill_at(4) == "fire_bolt"
    drag(g, bolt.center, right.center)
    assert g.session.skill_bar.right == "fire_bolt"
    drag(g, slots[4].center, slots[2].center)
    assert g.session.skill_bar.skill_at(2) == "fire_bolt" and g.session.skill_bar.skill_at(4) is None
    drag(g, slots[2].center, (g.screen.get_width() // 2, 100))  # off the bar
    assert g.session.skill_bar.slot_of("fire_bolt") is None


def test_a_drag_spends_no_point_but_a_click_does(game):
    g = game
    g.session.grant_skill("fire_bolt")
    g.open_only(g.tree_view)
    g.tree_view.tab = "pyromancy"
    g.tick()
    boxes = dict((n, b) for b, n in g.tree_view._nodes)
    passive = next(n for n in boxes if g.data.node(n).kind == "passive" and not g.data.node(n).requires)
    g.session.skills.set_level(passive, 0)
    drag(g, boxes[passive].center, (boxes[passive].centerx + 40, boxes[passive].centery))
    assert g.session.skills.points_in(passive) == 0, "a press that moved is not a click"


def test_right_click_in_the_trees_then_in_the_world_casts(game):
    g = game
    g.session.grant_skill("fire_bolt")
    g.player.energy = g.player.max_energy
    g.open_only(g.tree_view)
    g.tree_view.tab = "pyromancy"
    g.tick()
    bolt = next(box for box, node_id in g.tree_view._nodes if node_id == "fire_bolt")
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=bolt.center))
    g.tick()
    assert g.session.skill_bar.right == "fire_bolt"
    g.close_windows()
    pygame.mouse.set_pos((g.screen.get_width() // 2 + 200, g.screen.get_height() // 2))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=pygame.mouse.get_pos()))
    for _ in range(40):
        g.tick()
    assert g.session.skills.xp.get("fire_bolt", 0) > 0 or g.world.projectiles or g.player.energy < g.player.max_energy


# ---------------------------------------------------------------- gamepad


def pad(g, button, up=True):
    pygame.event.post(pygame.event.Event(pygame.CONTROLLERBUTTONDOWN, button=button, instance_id=0))
    if up:
        pygame.event.post(pygame.event.Event(pygame.CONTROLLERBUTTONUP, button=button, instance_id=0))
    g.tick()


def test_the_left_stick_walks_and_letting_go_stops(game):
    g = game
    x0, y0 = g.player.x, g.player.y
    g.pad.axes["leftx"] = 1.0  # right on the screen = +x and -y on the grid
    for _ in range(20):
        g.tick()
    assert g.player.x > x0 and g.player.y < y0
    g.pad.axes["leftx"] = 0.0
    g.tick()
    x1 = g.player.x
    for _ in range(10):
        g.tick()
    assert g.player.x == pytest.approx(x1, abs=0.05)


def test_the_trigger_guards_toward_the_right_stick(game):
    g = game
    g.pad.axes["triggerleft"] = 1.0
    g.pad.axes["rightx"] = -1.0
    g.tick()
    assert g.player.guarding
    g.pad.axes["triggerleft"] = 0.0
    g.tick()
    assert not g.player.guarding


def test_a_attacks_the_nearest_monster_and_x_uses_the_first_slot(game):
    g = game
    g.world.monsters.clear()
    m = add_monster(g.world, g.data, "bandit", g.player.x + 2.0, g.player.y, 99)
    pad(g, pygame.CONTROLLER_BUTTON_A)
    assert g.player.target_id == m.id
    g.session.grant_skill("fire_bolt")
    g.session.skill_bar.swap(g.session.skill_bar.slot_of("fire_bolt"), 0)
    g.player.energy = g.player.max_energy
    used = []
    g.use_skill = lambda skill, empty, aim=None: used.append((skill, aim))
    pad(g, pygame.CONTROLLER_BUTTON_X)
    assert used and used[0][0] == "fire_bolt" and used[0][1] is not None


def test_the_dpad_drinks_and_start_opens_the_menu_which_the_dpad_walks(game):
    g = game
    g.player.life = g.player.max_life / 2
    g.session.consumables["life_potion"] = n = 2
    pad(g, pygame.CONTROLLER_BUTTON_DPAD_UP)
    assert g.session.consumables["life_potion"] == n - 1
    pad(g, pygame.CONTROLLER_BUTTON_START)
    assert g.menu.open
    pad(g, pygame.CONTROLLER_BUTTON_DPAD_DOWN)
    assert g.menu.focus == "resume"
    pad(g, pygame.CONTROLLER_BUTTON_DPAD_DOWN)
    pad(g, pygame.CONTROLLER_BUTTON_A)
    assert g.store.exists(), "Save game"
    pad(g, pygame.CONTROLLER_BUTTON_B)
    assert not g.menu.open


def test_the_keyboard_arrows_walk_the_title_and_an_empty_name_is_hero(game):
    g = game
    g.title.open = True
    g.title.press("new")
    g.select.focus = "create"
    g.select.press_focus()
    assert g.session.name == "Hero" and not g.title.open and not g.select.open
    g.title.open = True
    g.title.press("new")
    g.select.press("create")
    assert g.store.slot == "hero_2", "never over another save"


# ---------------------------------------------------------------- icons (M7-02)


def test_every_active_skill_and_item_base_has_an_icon(data):
    from aethelgard.tools.fetch_sounds import load_icon_config

    cfg = load_icon_config()
    actives = {n.id for n in data.skills.nodes if n.kind == "active"}
    assert set(cfg["skills"]) == actives
    assert set(cfg["items"]) == {b.id for b in data.items.bases}
    assert set(cfg["consumables"]) == set(data.items.consumables)
    assert all(0 <= i < 256 for part in ("skills", "items", "consumables") for i in cfg[part].values())


def test_icons_are_cut_from_the_sheet_or_missing(tmp_path):
    from aethelgard.client.icons import Icons
    from aethelgard.tools.fetch_sounds import file_path, load_icon_config

    pygame.init()
    cfg = load_icon_config()
    icons = Icons(tmp_path, cfg)
    assert icons.skill("fire_bolt", 32) is None, "no sheet yet: names are drawn"
    path = file_path(cfg, tmp_path, "icons")
    path.parent.mkdir(parents=True)
    sheet = pygame.Surface((cfg["size"] * cfg["columns"], cfg["size"] * 32))
    sheet.fill((10, 200, 10), (cfg["size"] * 5, cfg["size"] * 4, cfg["size"], cfg["size"]))  # icon 37 = fire bolt
    pygame.image.save(sheet, str(path))
    icons.reload()
    img = icons.skill("fire_bolt", 32)
    assert img.get_size() == (32, 32) and img.get_at((16, 16))[:3] == (10, 200, 10)
    assert icons.skill("no_such_skill", 32) is None
