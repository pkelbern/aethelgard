"""Prototype colors (placeholders until the M7 art)."""

BG = (12, 13, 16)
TEXT = (226, 222, 214)
MUTED = (150, 148, 140)
GOLD = (222, 184, 90)
LIFE = (176, 38, 38)
ENERGY = (48, 102, 214)
XP = (206, 170, 64)
EXIT = (232, 196, 82)
WAYPOINT = (90, 160, 255)
GUARD = (130, 200, 255)
NPC = {"vendor": (120, 210, 140), "stash": (200, 170, 110)}
PANEL = (20, 21, 26)
PANEL_EDGE = (70, 66, 58)
COOLDOWN = (0, 0, 0, 165)

PLAYER_BODY = (78, 140, 240)
PLAYER_HEAD = (216, 190, 152)
SHIELD = (140, 112, 76)
SWORD = (216, 216, 228)

RANK = {
    "normal": (180, 64, 64),
    "champion": (90, 140, 255),
    "berserker": (90, 140, 255),
    "unique": (240, 190, 50),
    "minion": (190, 114, 90),
    "boss": (220, 60, 220),
}

FLOAT = {
    "damage": (240, 240, 240),
    "crit": (255, 160, 40),
    "miss": (150, 150, 150),
    "block": (130, 200, 255),
    "perfect": (160, 230, 255),
    "hurt": (230, 60, 60),
    "xp": (226, 210, 130),
    "info": (220, 220, 160),
    "levelup": (255, 210, 80),
}

RARITY = {0: (220, 220, 220), 1: (110, 140, 255), 2: (255, 224, 102), 3: (200, 160, 80)}

THEMES = {
    "town": {
        "solid_walls": True,
        "floor_a": (64, 60, 52), "floor_b": (70, 66, 57),
        "wall_top": (110, 100, 86), "wall_left": (60, 55, 48), "wall_right": (48, 44, 38),
    },
    "open": {"floor_a": (56, 62, 48), "floor_b": (62, 68, 52), "wall_top": (52, 58, 50), "wall_left": (34, 36, 32), "wall_right": (27, 29, 26)},
    "dungeon": {"floor_a": (52, 50, 56), "floor_b": (58, 56, 62), "wall_top": (40, 38, 46), "wall_left": (28, 26, 32), "wall_right": (22, 20, 26)},
}
