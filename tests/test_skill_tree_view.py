"""Skill trees window: layout with no overlaps, prerequisites above, clicks spend points, keys bind."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from aethelgard.client.skill_tree_view import NODE_H, tree_layout  # noqa: E402


@pytest.mark.parametrize("tree", ["blades", "shield", "armor", "dual_wield", "bows", "pyromancy"])
def test_layout_has_no_overlaps_and_requirements_sit_above(data, tree):
    nodes = [n for n in data.skills.nodes if n.tree == tree]
    boxes, rows = tree_layout(nodes, 900)
    assert set(boxes) == {n.id for n in nodes}
    rects = [pygame.Rect(b.x, b.y, b.w, b.h) for b in boxes.values()]
    assert not any(a.colliderect(b) for i, a in enumerate(rects) for b in rects[i + 1:])
    assert all(r.right <= 900 for r in rects)
    half = (900 - 64) // 2 + 64
    for n in nodes:
        assert (boxes[n.id].x < half) == (n.kind == "active"), "actives left, passives right"
        for req in n.requires:
            assert boxes[req].y + NODE_H < boxes[n.id].y


@pytest.fixture
def game(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHELGARD_SAVE_DIR", str(tmp_path))
    from aethelgard.client.app import Game

    g = Game(seed=3, new_game=True, download_art=False)
    g.load_area("plains")
    yield g
    pygame.quit()


def click(g, pos):
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos))
    g.tick()


def test_clicking_a_node_unlocks_it_and_the_tab_switches_trees(game):
    g = game
    g.open_only(g.tree_view)
    v = g.tree_view
    assert v.open
    g.tick()  # the first frame lays the window out
    pyro = next(box for box, tree in v._tabs if tree == "pyromancy")
    click(g, pyro.center)
    assert v.tab == "pyromancy"
    bolt = next(box for box, node_id in v._nodes if node_id == "fire_bolt")
    click(g, bolt.center)
    assert "fire_bolt" in g.session.skills.unlocked and g.session.skill_bar.slot_of("fire_bolt") is not None
    pygame.mouse.set_pos(bolt.center)
    assert v.node_at(bolt.center) == "fire_bolt"
    g.open_only(g.tree_view)
    assert not v.open, "its key closes it again"
