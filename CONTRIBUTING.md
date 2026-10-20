# Como contribuir

Obrigado pelo interesse no Aethelgard! O projeto ainda é um protótipo, então contribuições
pequenas e bem testadas ajudam mais do que grandes mudanças.

## Antes de começar

1. Veja o [`docs/BACKLOG.md`](docs/BACKLOG.md) e abra uma issue dizendo o que pretende fazer.
2. Para mudanças de design (regras, números, novas proficiências), leia o GDD em `docs/`.

## Ambiente

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest && ruff check src tests
```

## Regras do código

- **Regra de jogo vai em `src/aethelgard/core`** e não importa `pygame`. Toda regra nova vem com teste.
- **O cliente não decide nada:** `src/aethelgard/client` lê entrada, envia comandos ao `World`
  (`MoveTo`, `AttackTarget`, `UseSkill`, `SetGuard`) e desenha o estado.
- **Números em JSON** (`src/aethelgard/data`), nunca soltos no código. `validate()` acusa referências quebradas.
- **Prefira bibliotecas a reinventar:** desenho e entrada com pygame-ce, janelas com pygame_gui,
  busca de caminho e visão com tcod, arrays com numpy, mapas com Tiled/pytmx. Biblioteca nova
  precisa de licença compatível com GPL-3.0 e entra em `pyproject.toml` com um comentário do porquê.
- **Aleatoriedade só com `aethelgard.core.rng.Rng`**, nunca o módulo `random`: o jogo precisa
  ser reproduzível pela seed (e, no futuro, sincronizado no multiplayer).
- **Eventos de jogador carregam `player_id`.** Nada de estado global fora de `GameSession`/`World`.
- Código, comentários e textos do jogo em inglês. Nomes exibidos ficam nos JSON (`data/`), não no código.

## Mapas

Cidades e salas especiais são feitas no editor [Tiled](https://www.mapeditor.org) e lidas com
pytmx (veja o README). Áreas sem `map` em `areas.json` são geradas pela seed. Todo mapa passa
por `AreaLayout.validate()`: entrada, saídas e waypoint precisam estar alcançáveis.

## Balanceamento

Mudou curva, multiplicador ou área? Rode `python -m aethelgard.tools.simulate`. O teste
`test_simulacao_do_ato_1_na_meta` falha se a proficiência principal sair da meta de 12–14 no
chefe do Ato 1. Se a mudança for intencional, atualize o teste e o GDD no mesmo pull request.

## Arte e som

Só entram no repositório assets com licença que permita redistribuição (CC0, CC-BY, CC-BY-SA)
ou criados por você e cedidos ao projeto. Registre autor, fonte e licença em `assets/CREDITS.md`.

## Pull requests

- Um assunto por pull request, com descrição do que muda e como testar.
- `pytest` e `ruff check src tests` passando (o CI roda em Linux, Windows e macOS).
- Ao contribuir, você concorda em licenciar sua contribuição sob a GPL-3.0-or-later.
