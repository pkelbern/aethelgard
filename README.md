# Aethelgard

RPG de ação isométrico 2D, open source, feito em Python. **Não há classes**: o personagem
evolui nas proficiências que usa (Lâminas, Escudo, Couraça…). Mapas gerados por seed, monstros
em grupos normais, champions e únicos com minions, loot com afixos, guarda ativa com escudo e
tempos de ação (recarga de habilidades, recuperação de golpe e de bloqueio).

Status: **protótipo jogável (marcos M0–M2)**, com formas simples no lugar da arte.

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

Opções: `--seed 42` (mundo fixo), `--area planicie` (começar numa área).

### Controles

| Ação | Tecla |
|---|---|
| Andar / atacar | Clique esquerdo no chão / no monstro |
| Guarda com escudo | Segurar **Espaço** (o escudo aponta para o mouse) |
| Habilidades | **Q** Golpe Preciso · **W** Golpe de Escudo · **E** Corte Amplo · **R** Muralha |
| Gastar ponto de árvore | **1** Lâmina Afiada · **2** Golpe Preciso · **3** Postura Defensiva · **4** Golpe de Escudo · **5** Pele de Ferro · **6** Recuperação · **7** Aparar |
| Árvores de habilidade (janela) | **T** |
| Mapa grande | **Tab** (o mapa pequeno fica no canto) |
| Console de depuração | **F1** (digite `ajuda`) |
| Sair | Esc |

A névoa de guerra esconde o que você ainda não viu, e monstros fora do campo de visão não
aparecem. Saídas em amarelo, waypoint em azul. Champions são azuis, únicos dourados, minions marrons e
o chefe roxo. A barra de habilidades mostra a recarga de cada uma; o centro da tela mostra
recuperação de golpe, de bloqueio e atordoamento com o tempo restante.

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

A Vila (`src/aethelgard/data/maps/vila.tmx`) é feita à mão no editor gratuito **Tiled**. O mapa
é lógico e ortogonal: cada tile é uma célula do jogo, e o jogo desenha em isométrico.
- Tiles do conjunto `logica.tsx`: chão e estrada (andáveis), parede e água (bloqueiam). A propriedade `walkable` decide.
- Camada de objetos `objetos`: `entry` (chegada), `exit` (propriedade `target` = id da área),
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
- **Arte e som:** ainda não há assets próprios. Quando houver, terão licença própria, descrita
  em `assets/LICENSE` (veja o backlog M8-07).
- O balanceamento usa o ritmo de ARPGs clássicos como referência, com curvas próprias por
  fórmula. Nenhuma tabela, nome, arte ou som de outros jogos faz parte deste repositório.
