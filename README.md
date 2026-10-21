# Aethelgard

RPG de ação isométrico 2D, open source, feito em Python. **Não há classes**: o personagem
evolui nas proficiências que usa (Blades, Shield, Armor…). Mapas gerados por seed, monstros
em grupos normais, champions e únicos com minions, loot com afixos, guarda ativa com escudo e
tempos de ação (recarga de habilidades, recuperação de golpe e de bloqueio).

Status: **protótipo jogável (marcos M0–M2)**, com sprites animados de protótipo (arte do [Flare](https://github.com/flareteam/flare-game), CC-BY-SA 3.0) e formas simples onde ainda falta arte.

![Captura do protótipo](docs/captura_prototipo.png)

- Design completo: [`docs/GDD_Aethelgard_v0.4.docx`](docs/GDD_Aethelgard_v0.4.docx)
- Balanceamento: [`docs/Balanceamento_Aethelgard_v0.3.xlsx`](docs/Balanceamento_Aethelgard_v0.3.xlsx)
- Plano de trabalho: [`docs/BACKLOG.md`](docs/BACKLOG.md)
- Arquitetura: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md)

## Como jogar

Requisito: **Python 3.11 ou mais novo** (https://www.python.org/downloads/).

```bash
git clone https://github.com/SEU_USUARIO/aethelgard.git
cd aethelgard
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
aethelgard            # ou: python -m aethelgard
```

Opções: `--seed 42` (mundo fixo), `--area plains` (começar numa área), `--new` (personagem novo, ignora o save),
`--save-dir pasta` (onde guardar o save), `--offline` (não baixa a arte), `--art-dir pasta` (onde fica a arte).

**Arte:** os sprites não estão no repositório. Toda vez que o jogo abre, ele confere se todas as folhas de sprite
estão na pasta de arte; se falta alguma, mostra uma tela de carregamento e baixa só o que falta (na primeira vez,
uns 65 MB: personagens e mortos-vivos do repositório do Flare, lobos do OpenGameArt). Esc começa a jogar na hora e
o download continua por trás. Quando a conversão muda numa versão nova do jogo, a arte é baixada de novo na abertura. Se uma fonte falhar, a tela mostra o motivo (R tenta de novo, Enter joga assim).
O que não tem sprite aparece como um retângulo xadrez magenta com "?". O terminal também mostra a conferência
(`[art] … 38/38 sheets ready`). A arte fica na pasta do usuário (`%APPDATA%\Aethelgard\art`,
`~/Library/Application Support/Aethelgard/art` ou `~/.local/share/aethelgard/art`). Para baixar à mão:

```bash
python -m aethelgard.tools.fetch_art            # ou: aethelgard-art
python -m aethelgard.tools.fetch_art --all      # refaz tudo
python -m aethelgard.tools.fetch_art --flare caminho/do/flare-game  # arquivos do Flare de um clone local
```

Opcional: `pip install -e ".[art]"` instala o Pillow e gera PNGs 5× menores.

O jogo é todo em inglês. Ao reabrir, o personagem volta **na vila** com os mesmos níveis, pontos de
árvore, equipamento, mochila, ouro e poções; o mundo é gerado de novo (seed nova a cada jogo).

### Controles

| Ação | Tecla |
|---|---|
| Andar / atacar | Clique esquerdo no chão / no monstro |
| Guarda com escudo | Segurar **Espaço** (o escudo aponta para o mouse) |
| Habilidades | **1–6** (barra de atalhos; clique num atalho para escolher a habilidade) |
| Árvores de habilidade | **T**: clique num nó para gastar ponto; ativos custam 1 ponto e sobem pelo uso; mouse em cima + **1–6** põe na barra |
| Mapa grande | **Tab** (o mapa pequeno fica no canto) |
| Salvar | **F5** ou `save` no console (o jogo também salva ao sair) |
| Console de depuração | **F1** (digite `help`; `bag` lista mochila e equipamento, `load` recarrega o save) |
| Sair | Esc |

A névoa de guerra esconde o que você ainda não viu, e monstros fora do campo de visão não
aparecem. Saídas em amarelo, waypoint em azul. Champions são azuis, únicos dourados, minions marrons e
o chefe roxo. A barra de habilidades mostra a recarga de cada uma; o centro da tela mostra
recuperação de golpe, de bloqueio e atordoamento com o tempo restante.

## Save

Um arquivo JSON versionado por personagem, gravado com segurança (arquivo temporário + troca) e com
backup automático (`character.bak.json`), usado se o principal estiver corrompido:

| Sistema | Pasta |
|---|---|
| Windows | `%APPDATA%\Aethelgard\saves` |
| macOS | `~/Library/Application Support/Aethelgard/saves` |
| Linux | `~/.local/share/aethelgard/saves` |

A variável de ambiente `AETHELGARD_SAVE_DIR` ou a opção `--save-dir` trocam a pasta. Rodadas com
`--frames` (testes/CI) nunca gravam.

## Bibliotecas

| Biblioteca | Para quê | Licença |
|---|---|---|
| [pygame-ce](https://pyga.me) | janela, desenho, entrada, som | LGPL |
| [pygame_gui](https://github.com/MyreMylar/pygame_gui) | janelas e widgets (árvore de habilidades, console) | MIT |
| [numpy](https://numpy.org) | geração de mapas vetorizada | BSD |
| [python-tcod](https://github.com/libtcod/python-tcod) | A*, distâncias e campo de visão em C | BSD |
| [pytmx](https://github.com/bitcraft/pytmx) | mapas feitos à mão no editor [Tiled](https://www.mapeditor.org) | LGPL |
| pytest, ruff, PyInstaller | testes, padrão de código, executável | MIT/BSD/GPL |

## Editar mapas no Tiled

A vila (`src/aethelgard/data/maps/village.tmx`) é feita à mão no editor gratuito **Tiled**. O mapa
é lógico e ortogonal: cada tile é uma célula do jogo, e o jogo desenha em isométrico.
- Tiles do conjunto `logic.tsx`: chão e estrada (andáveis), parede e água (bloqueiam). A propriedade `walkable` decide.
- Camada de objetos `objects`: `entry` (chegada), `exit` (propriedade `target` = id da área),
  `waypoint` e `npc` (propriedade `role`: `vendor`, `stash`…).
- Para usar um mapa feito à mão em outra área, ponha `"map": "maps/arquivo.tmx"` em `areas.json`.

## Para desenvolver

```bash
pytest                                  # testes rápidos
pytest -m slow                          # 100 seeds por área (≈30 s)
ruff check src tests                    # padrão de código
python -m aethelgard.tools.simulate     # progressão do Ato 1 com os dados atuais
python -m aethelgard.tools.curves       # tabelas geradas pelas curvas
```

Regra de ouro: **regra de jogo vai em `aethelgard/core`, com teste, sem importar pygame.**
O cliente (`aethelgard/client`) só lê entrada, envia comandos ao mundo e desenha.
Números ficam em `src/aethelgard/data/*.json`. Veja [`CONTRIBUTING.md`](CONTRIBUTING.md).

```
src/aethelgard/
  core/     regras: dados, RNG, XP por uso, combate, guarda, tempos, monstros, loot,
            geração de mapas, A*, mundo em tempo real, sessão, simulação
  client/   pygame-ce: câmera isométrica, mapa, atores, HUD, console
  data/     todos os números do jogo (JSON)
  tools/    simulação e tabelas pela linha de comando
tests/      pytest
docs/       GDD, planilha, backlog, arquitetura
```

## Licença

- **Código:** [GPL-3.0-or-later](LICENSE). Você pode usar, estudar, modificar e redistribuir;
  versões modificadas precisam continuar abertas sob a mesma licença.
- **Arte de terceiros (CC-BY-SA 3.0):** personagens e mortos-vivos do jogo livre
  [Flare](https://github.com/flareteam/flare-game) (Clint Bellanger, Justin Jacobs e colaboradores); lobos de
  [Wolf monsters animated](https://opengameart.org/content/wolf-monsters-animated) (Danimal; modelo de Micket, CC0;
  texturas de p0ss); javali de [Boar enemies](https://opengameart.org/content/boar-enemies) (Danimal; modelo de
  Myname; **CC-BY-SA 4.0**). Não fazem parte deste repositório: são baixados quando o jogo abre (fontes em
  `data/ui/sprites.json`), reduzidos e reempacotados, com `ATTRIBUTION.md` e a licença em cada pasta da arte. Cada arquivo está em
  [`docs/licencas_assets.csv`](docs/licencas_assets.csv).
- **Arte e som próprios:** ainda não há. Quando houver, terão licença própria (veja o backlog M8-07).
- O balanceamento usa o ritmo de ARPGs clássicos como referência, com curvas próprias por
  fórmula. Nenhuma tabela, nome, arte ou som dos jogos de referência (Diablo, Dungeon Siege) faz parte
  deste repositório; arte de terceiros só entra com licença livre que permita redistribuição.
