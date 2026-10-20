# Arquitetura

## Camadas

```
client/ (pygame-ce)     entrada → comandos; desenho do estado; HUD; console
   │  World.apply(comando) / World.step(dt) / eventos
   ▼
core/                   regras, sem pygame
   data.py              modelos dos JSON, curvas por fórmula, validate()
   rng.py               xoshiro256** determinístico, Rng.for_label(seed, "rótulo")
   events.py            EventBus e eventos (todos com player_id)
   progression.py       XpCurve, LevelFactor, XpCalculator, ProficiencySet, CharacterSheet, SkillTrees, SkillBar (atalhos)
   describe.py          texto dos tooltips (habilidades agora, itens no M6-04) a partir de skills.json e stats.json
   combat.py            CombatMath (acerto, armadura, guarda ativa), números do jogador e do monstro
   timing.py            Cooldowns, recuperação de golpe e de bloqueio, recarga de habilidade
   monsters.py          MonsterFactory (nível × rank), PackSpawner, nomes de únicos
   items.py             LootGenerator, Item, Loadout (equipamento), Inventory (mochila), SharedStash (baú);
                        ItemContainer e item_to_dict/item_from_dict são o formato único de itens em todo lugar
   world_gen.py         AreaGenerator (áreas abertas, masmorras e mapas do Tiled) + AreaLayout.validate()
   pathfinding.py       A* do tcod
   world.py             mundo em tempo real dirigido por comandos
   session.py           GameSession: monta tudo, entra em áreas, coleta loot, morte, save()/load()
   saves.py             SaveStore: arquivo do personagem em disco, gravação atômica e backup
   simulation.py        simulação de progressão (mesma conta da planilha)
```

**Regra de ouro:** `core` não depende de `pygame` para funcionar. Tudo que dá para testar sem tela fica nele.

## Bibliotecas

| Onde | Biblioteca | Uso |
|---|---|---|
| core/world_gen.py | numpy | mapa como array `uint8[y, x]`, autômato celular vetorizado |
| core/world_gen.py, core/pathfinding.py | tcod | distâncias (`dijkstra2d`) e A* (`tcod.path.AStar`) em C |
| core/world_gen.py | pytmx | mapas feitos à mão no Tiled (`load_tiled_map`) |
| client/vision.py | tcod | campo de visão (`compute_fov`), névoa de guerra, mapa automático |
| client/ui.py | pygame_gui | janela de árvores (T), console (F1); tema em `data/ui/theme.json` |
| client/* | pygame-ce | janela, desenho isométrico, entrada, HUD |

## Tooltips, atalhos e teclas

- `data/stats.json`: rótulo e formato de cada atributo (pct, seconds, degrees…). Um catálogo para habilidades e afixos; `validate()` acusa atributo sem entrada.
- `skills.json`: `effects` crescem por ponto; `base` é fixo depois de aprender (arco do Wide Slash, atordoamento do Shield Bash, 3 s da Bulwark); `description` tem `{stat}` trocado pelo valor do nível mostrado.
- `SkillTrees.effect_at(nó, stat, pontos)` dá o valor em qualquer nível; o tooltip mostra o atual e o próximo.
- `SkillBar` (núcleo, salva no personagem) guarda qual habilidade está em cada atalho. `data/ui/keys.json` (cliente) diz qual tecla é cada atalho. O cliente traduz tecla → atalho → habilidade e manda `UseSkill(skill_id)`; o mundo nunca vê teclas.
- `client/tooltip.py` desenha qualquer lista de `describe.Line`; a barra e a janela de árvores usam o mesmo texto.

## Bloqueio e colisão

- `CombatMath.resolve_physical`: acerto → guarda (para `stopped` = golpe × parcela, no máximo `blockReductionCap`) → armadura sobre `raw − stopped` → dano final. Bloqueio nunca zera o dano.
- `World._step_towards`: um passo; se bate em parede desliza pelos eixos, se bate em corpo desliza pela borda dele. `blocked_by_body` sempre deixa se afastar de uma sobreposição. Corpo morto não bloqueia.

## Laço do jogo

O cliente roda a 60 quadros por segundo com passo fixo:

1. Lê eventos do pygame e transforma em comandos (`MoveTo`, `AttackTarget`, `UseSkill`, `SetGuard`).
2. `World.step(1/60)` avança cooldowns, estados, IA, ataques e movimento.
3. Desenha o mapa (blocos pré-renderizados), atores ordenados por profundidade, números flutuantes e HUD.

No multiplayer, o host roda o mesmo `World` com os comandos de todos e envia o estado; os
clientes só desenham (host autoritativo).

## Tempos de ação (GDD 7.7)

| Estado do jogador | Entra quando | Sai quando | Pode |
|---|---|---|---|
| `attacking` | começa um ataque ou habilidade de golpe | fim da animação (`attack_seconds`) | nada; o golpe sai em `attack_hit_fraction` |
| `hit_recovery` | golpe recebido ≥ 1/12 da vida | `hit_recovery_seconds / (1 + fhr)` | nada; ataque em andamento é perdido |
| `block_recovery` | bloqueio pago com Energia | `block_recovery_seconds / (1 + fbr)` | só manter a guarda |
| `stunned` | guarda quebrada (sem Energia) | `guard_break_stun_seconds` | nada |

Recargas ficam em `PlayerActor.cooldowns` (uma por habilidade + `global` + `guard_raise` + `attack`).
Monstros também têm `hit_recovery` (≥ 1/8 da vida; único 50%, chefe imune) e `stunned` (Shield Bash).

## Fluxo do XP por uso

```
ataque do jogador ─► CombatMath.resolve_physical ─► MonsterInstance.take_damage (sem overkill)
       └─ DamageDealt(player_id) ─► ProficiencySet do jogador ─► XpCalculator.offense ─► Proficiency.add_xp
golpe do monstro ─► guarda erguida e no arco? ─► HitBlocked ─► Escudo (teto por monstro)
                 └► armadura mitigou?         ─► DamageMitigated ─► Couraça (teto por monstro)
```

Uma proficiência nova (ex.: Arcos) é: entrada em `proficiencies.json`, nós em `skills.json`,
e o ataque de arco publicando `DamageDealt("arcos", …)`. Combate e XP não mudam.

## Aleatoriedade

`GameSession` cria um `Rng` por sistema (`world`, `combat:<player>`, `loot`, `spawn`). Mapas usam
`Rng.for_label(seed, "area:<id>:<andar>")`: a mesma seed gera o mesmo mapa em qualquer ordem
de visita. Nunca use o módulo `random` em regra de jogo.

## Dados

| Arquivo | Conteúdo |
|---|---|
| `curves.json` | Curvas a × n^p × g^n: XP das proficiências; XP, vida, dano e precisão dos monstros |
| `balance.json` | XP defensivo, fator de nível, personagem, combate, spawn, morte, guarda, tempos |
| `monster_ranks.json` | normal, champion, berserker, único, minion, chefe |
| `monster_modifiers.json` | modificadores de únicos e champions |
| `monsters.json` | famílias, tipos e chefes |
| `areas.json` | atos, áreas, ligações, níveis, andares |
| `proficiencies.json` | proficiências, perfil de atributos, títulos |
| `skills.json` | nós das árvores, recargas e sinergias |
| `items.json` | bases, afixos, chances de raridade e de drop |

## Save

`GameSession.save()` gera JSON versionado (`SAVE_VERSION = 2`): nome, ouro, XP das proficiências,
pontos de árvore, equipamento, mochila e consumíveis. Saves v1 continuam carregando (equipamento inicial,
mochila vazia). O mundo não é salvo: `load(..., new_world_seed=…)` cria um mundo novo e o cliente
sempre começa na cidade (`GameData.town_id`). Os pontos de árvore voltam por `SkillTrees.restore()`,
que passa pelas mesmas regras de `allocate()` (save antigo ou editado não fura camada nem limite).

`SaveStore` (em `core/saves.py`) grava em `<pasta>/<slot>.json` via arquivo temporário + `os.replace`
e guarda o anterior em `<slot>.bak.json`; se o principal não abrir, usa o backup. No multiplayer, cada
jogador salva a própria `GameSession` (o save é por personagem, não por mundo).

O loot vai direto para a mochila de quem matou (`GameSession.collect`, evento `LootPicked` com
`player_id`) até existirem itens no chão (M6-02). O baú compartilhado (`SharedStash`) usa o mesmo
formato de item e terá arquivo próprio, lido por todos os personagens.

## Distribuição

Para a Steam: PyInstaller gera um executável por sistema (`pip install -e ".[build]"`).
O código-fonte segue público no GitHub sob GPL-3.0-or-later.
