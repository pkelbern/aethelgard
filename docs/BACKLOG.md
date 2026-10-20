# Backlog de desenvolvimento — Aethelgard

Gerado a partir do roadmap do GDD (seção 17). Cada marco só fecha quando o critério de pronto for atingido.
Estimativas em dias de trabalho de uma pessoa, só para ordem de grandeza.

Status: [x] feito e testado · [ ] com a marca **escrito, validar** = código pronto que ainda não foi validado · [ ] a fazer.

| Marco | Feito | Validar | Total | Dias restantes |
|---|---|---|---|---|
| M0 – Base | 4 | 0 | 8 | 2.5 |
| M1 – Combate | 6 | 6 | 14 | 4.5 |
| M2 – Proficiências | 4 | 0 | 6 | 1.5 |
| M3 – Árvores | 2 | 1 | 7 | 8 |
| M4 – Monstros | 2 | 2 | 8 | 9.5 |
| M5 – Mundo | 4 | 1 | 8 | 7.5 |
| M6 – Loot | 2 | 3 | 8 | 5.5 |
| M7 – Vertical slice | 0 | 2 | 9 | 28.5 |
| M8 – Vitrine | 0 | 0 | 7 | 11 |
| M9 – Lançamento | 1 | 0 | 7 | 22 |
| M10 – Multiplayer | 2 | 0 | 6 | 22 |

## M0 – Base

**Pronto quando:** Andar por clique sem travar em paredes, 60 fps

- [ ] **M0-01** Rodar o protótipo no seu PC _(código, ~0.5 d)_  
  pip install -e .[dev], depois aethelgard (ou python -m aethelgard). Critério: anda, ataca, ergue a guarda e troca de área sem erro.
- [x] **M0-02** Migração para Python: pacote, núcleo, cliente e testes _(código, ~3 d)_  
  src/aethelgard (core sem pygame + client pygame-ce), 52 testes pytest, ruff.
- [ ] **M0-03** Publicar no GitHub e ativar o CI _(infra, ~0.5 d)_  
  Repositório público, .github/workflows/ci.yml roda ruff e pytest em Linux, Windows e macOS.
- [x] **M0-04** Câmera isométrica e mapa placeholder _(código, ~1 d)_  
  Projeção 2:1, mapa em blocos pré-renderizados de 16×16 tiles, câmera suave.
- [x] **M0-05** Movimento por clique com A* _(código, ~1 d)_  
  A* com 8 direções sem cortar quinas; deslizar em paredes.
- [ ] **M0-06** Medir FPS com 60 monstros _(código, ~0.5 d)_  
  Contador de FPS e cena de estresse. Se faltar desempenho: só atualizar monstros perto do jogador e agrupar o desenho.
- [ ] **M0-07** Gamepad: andar com analógico _(código, ~1 d)_  
  pygame.joystick: analógico esquerdo anda, A ataca o mais próximo, LT ergue a guarda.
- [x] **M0-08** Adotar bibliotecas de jogo _(código, ~1.5 d)_  
  numpy (mapas), tcod (A*, distâncias, campo de visão), pytmx + Tiled (mapas à mão), pygame_gui (janelas). Teste de 100 seeds caiu de 27 s para 13 s.

## M1 – Combate

**Pronto quando:** Matar e morrer funcionam; números conferem com as fórmulas

- [x] **M1-01** Fórmulas de acerto, bloqueio e armadura no núcleo _(código, ~1 d)_  
  CombatMath com testes (5–95%, tetos de 75%).
- [x] **M1-02** Ataque básico do jogador e dano no monstro _(código, ~1 d)_  
  Clique no monstro: aproxima, ataca no intervalo; dano efetivo sem overkill.
- [x] **M1-03** Monstros atacam o jogador _(código, ~1 d)_  
  Alerta por raio e por grupo; persegue e ataca no alcance.
- [ ] **M1-04** Morte e retorno à cidade _(código, ~1 d)_  
  Feito: o personagem cai, a tela fica cinza com o que foi perdido, e um clique (ou Enter) leva à Vila (comando Respawn no World); perde 10% do ouro; perda de XP dentro do nível existe por parâmetro e está desligada. Pendente: corpo com equipamento para recuperar.
- [ ] **M1-05** Animação de ataque e impacto (placeholder) _(game feel, ~1.5 d)_  
  Flash no alvo, recuo curto e som de impacto. Critério: o golpe 'tem peso'. Com os sprites (M7-09): o alvo acende enquanto está em recuperação de golpe ou atordoado (`hitFlash` em data/ui/sprites.json).
- [ ] **M1-06** Poções de vida e energia com recarga _(código, ~1 d)_ — **escrito, validar**  
  Q/W (GDD 12.3): Vida 40% do máximo em 2 s, Energia 50% em 1,5 s, recarga de 0,5 s, várias se somam (items.json consumables). Contador e recarga no HUD ao lado da barra. Falta: cinto.
- [ ] **M1-07** Projéteis de monstros à distância _(código, ~1.5 d)_ — **escrito, validar**  
  Arqueiro (flecha física), Xamã e Necromante (rajada mágica) atiram projéteis visíveis em linha reta para onde o jogador estava, sem desviar; paredes param; só atiram com linha de visão. Guarda bloqueia físicos; mágicos só com Bulwark (decide a questão 3 do GDD 19.2). core/projectiles.py.
- [x] **M1-08** Guarda ativa com escudo (segurar Espaço) _(código, ~1.5 d)_  
  Arco frontal, custo de Energia por bloqueio, janela perfeita com contra-ataque (Aparar), guarda quebrada atordoa. Testado no mundo em tempo real.
- [x] **M1-09** Recarga curta entre guardas _(balanceamento, ~0.5 d)_  
  0,3 s para erguer a guarda de novo (evita abuso do aparo perfeito).
- [ ] **M1-14** Sem esquiva com a guarda erguida _(regra, ~0.2 d)_ — **escrito, validar**  
  Golpe dentro do arco da guarda erguida sempre chega à guarda (vira bloqueio); pelas costas a esquiva por Defesa continua valendo.
- [ ] **M1-11** Bloqueio parcial: o golpe bloqueado ainda fere _(balanceamento, ~0.5 d)_ — **escrito, validar**  
  O bloqueio para 70% do golpe (balance.guard.blockReduction) + base do escudo (0/5/10/15%) + Postura Defensiva (+2,5%/ponto), no máximo 95% (blockReductionCap); a armadura age sobre o que passa. Aparo perfeito para 95%. Validar no jogo se 70% de base está bom.
- [ ] **M1-12** Colisão entre corpos _(código, ~1 d)_ — **escrito, validar**  
  Jogador e monstros não se atravessam; contorno pela borda do corpo; grupos nascem sem sobreposição. Raio por tipo (monsters.json) × sizeMult do rank; monstro sem corpo exige collides=false explícito e o quadro do alvo avisa ("Ethereal").
- [ ] **M1-13** Segurar o botão do mouse repete o clique _(UI, ~0.5 d)_ — **escrito, validar**  
  Segurando no chão continua andando para o cursor; segurando num monstro continua atacando-o até ele morrer.
- [x] **M1-10** Tempos de ação: recargas e recuperações _(código, ~1.5 d)_  
  Recarga por habilidade e global, recuperação de golpe (≥ 1/12 da vida), recuperação de bloqueio, recuperação de monstros. Testes em test_world_timing.py.

## M2 – Proficiências

**Pronto quando:** XP por uso nas 3 proficiências com testes passando e log de XP

- [x] **M2-01** XP por uso: ofensivo, bloqueio, armadura _(código, ~1 d)_  
  XpCalculator + ProficiencySet via EventBus, teto defensivo por monstro.
- [x] **M2-02** Curva e fator de nível calibrados pelo D2 _(balanceamento, ~1 d)_  
  Dados em xp_curve.json e balance.json; simulação do Ato 1 igual à planilha.
- [x] **M2-03** Atributos, nível do personagem e título _(código, ~0.5 d)_  
  CharacterSheet com testes.
- [x] **M2-04** HUD de proficiências e números flutuantes de XP _(código, ~0.5 d)_  
  Barras por proficiência e '+XP' sobre o jogador.
- [ ] **M2-05** Log de XP por fonte para playtest _(ferramenta, ~1 d)_  
  Arquivo CSV por sessão: tempo, proficiência, fonte, monstro, XP. Critério: dá para achar farm na planilha.
- [ ] **M2-06** Playtest de 30 min focado no ritmo de XP _(balanceamento, ~0.5 d)_  
  Comparar tempo por nível com a simulação; ajustar escala da curva se preciso.

## M3 – Árvores

**Pronto quando:** Builds Guardião e Duelista jogáveis e diferentes

- [x] **M3-01** Árvores no núcleo: camadas, pontos, sinergias _(código, ~1 d)_  
  SkillTrees com testes (bloqueio de camada, retorno decrescente, respec).
- [x] **M3-02** Tela de árvores (pygame_gui) _(UI, ~3 d)_  
  Feito: janela com as 3 árvores (T), botão + por nó, tooltip desenhado pelo jogo (core/describe.py) com descrição, valores do nível atual e do próximo, custo, sinergias e requisito. Falta: arte dos ícones e linhas entre camadas.
- [ ] **M3-03** Barra de habilidades com contagem regressiva _(UI, ~1 d)_ — **escrito, validar**  
  Feito: barra de 6 atalhos (GDD 6.1) nas teclas 1–6 (data/ui/keys.json), setor de recarga e segundos. O jogador escolhe a habilidade de cada tecla: passar o mouse na árvore e apertar a tecla, ou clicar no atalho (seletor); clique direito limpa; atribuir numa tecla ocupada tira a habilidade antiga da barra (não troca). Habilidade nova vai para o primeiro atalho livre. Salva com o personagem (save v3). Tooltip ao passar o mouse no atalho. Falta: arrastar e soltar; clique direito para usar habilidade (GDD 12.3).
- [ ] **M3-04** Implementar as ativas que faltam _(código, ~3 d)_  
  Feitas: Precise Strike, Shield Bash (atordoa e empurra 1,5 tile), Wide Slash (arco), Bulwark (guarda 360°), Shield Throw (projétil reto que ricocheteia em linha reta para o inimigo mais próximo) — as duas últimas mudanças **escrito, validar**. Faltam: Charge, Whirlwind, War Cry.
- [ ] **M3-05** Implementar os passivos do MVP _(código, ~3 d)_  
  Ligar efeitos de skills.json a PlayerCombat e às regras (sangramento, ritmo, segundo fôlego…).
- [ ] **M3-06** Pergaminho do Esquecimento (respec) _(código, ~0.5 d)_  
  Item raro que zera uma árvore.
- [ ] **M3-07** Bônus de recuperação e recarga em itens e passivos _(código, ~1 d)_  
  Afixos da Firmeza (recuperação de golpe), do Reflexo (recuperação de bloqueio), Expedito (recarga); ligar os afixos ao personagem quando o inventário existir.

## M4 – Monstros

**Pronto quando:** Spawns seguem a distribuição; modificadores e IA por arquétipo

- [x] **M4-01** Fábrica de monstros com nível × rank _(código, ~1 d)_  
  Multiplicadores do D2 em Normal; testes.
- [x] **M4-02** Pacotes: normal, champion (com berserker), único + minions _(código, ~1 d)_  
  PackSpawner com testes de distribuição (~80% únicos).
- [ ] **M4-03** Efeitos dos modificadores _(código, ~3 d)_  
  Ígneo explode, Gélido desacelera, Elétrico descarga, Vampírico cura, Teleportador, Aura de Fúria.
- [ ] **M4-04** IA por arquétipo _(código, ~3 d)_  
  flanker, charger, ranged, caster_revive, support_heal (monsters.json → ai).
- [ ] **M4-05** Chefe do Ato 1 com padrão de ataque próprio _(código, ~3 d)_  
  2–3 ataques telegrafados e uma fase abaixo de 50% de vida.
- [ ] **M4-06** Desativar monstros longe do jogador _(desempenho, ~0.5 d)_  
  Pausar _Process fora de um raio. Critério: custo de CPU estável em áreas cheias.
- [ ] **M4-08** Líderes: Xamã e Necromante _(código, ~1 d)_ — **escrito, validar**  
  Nunca aparecem sozinhos nem em grupos champion/único: lideram um grupo normal (mesmo tamanho, então o XP do Ato 1 não muda), até 3 por área/andar. Aura enquanto vivos: Xamã +15% dano e +10% velocidade de ataque; Necromante −10% dano físico recebido, num raio de 6 tiles. O quadro do alvo explica (Leader / Empowered).
- [ ] **M4-07** Nome do monstro ao passar o mouse (GDD 12.1) _(UI, ~0.5 d)_ — **escrito, validar**  
  Nome na cor do rank (branco, azul champion, dourado único, lilás chefe), contorno nos pés e quadro no topo com vida, nível, rank e modificadores. Vale para todos os monstros, não só únicos e champions.

## M5 – Mundo

**Pronto quando:** Ato 1 navegável com escadas, waypoints e 100 seeds sem área quebrada

- [x] **M5-01** Gerador de áreas abertas e masmorras por seed _(código, ~2 d)_  
  AreaGenerator com validação e 100 seeds por área nos testes.
- [x] **M5-02** Transições: bordas, entradas, escadas _(código, ~0.5 d)_  
  Chegada ao lado da saída de origem, sem voltar sem querer.
- [ ] **M5-03** Prefabs feitos à mão nas áreas abertas _(código, ~3 d)_  
  Acampamentos, ruínas, santuários e baús desenhados no Tiled (.tmx) e carimbados na geração pela seed.
- [ ] **M5-04** Salas pré-montadas nas masmorras _(código, ~3 d)_  
  Trocar retângulos por salas desenhadas no Tiled, com conectores marcados como objetos.
- [ ] **M5-05** Waypoints com menu de viagem _(código, ~1.5 d)_  
  Ativar ao tocar; menu lista os ativados. Salvar no personagem.
- [ ] **M5-06** Portal de cidade (pergaminho) _(código, ~1 d)_ — **escrito, validar**  
  E abre o portal onde você está; entrar leva à vila, onde o portal aparece ao lado do waypoint; entrar de novo volta ao mesmo ponto. O portal fica até o próximo pergaminho. As áreas agora guardam o estado na partida (monstros mortos continuam mortos, itens no chão continuam lá).
- [x] **M5-07** Névoa de guerra e mapa automático (Tab) _(UI, ~2 d)_  
  Feito: campo de visão do tcod revela o mapa, monstros fora da visão somem, mapa pequeno no canto e grande com Tab. Saídas, waypoint, NPCs e portal só aparecem depois de explorados.
- [x] **M5-08** Cidade feita à mão no Tiled _(conteúdo, ~2 d)_  
  Feito: data/maps/village.tmx com entrada, saída, waypoint, vendedora e baú (marcadores). Falta: lógica do vendedor e do baú.

## M6 – Loot

**Pronto quando:** Drops seguem as tabelas; inventário, baú e vendedor funcionam

- [x] **M6-01** Gerador de loot no núcleo _(código, ~1 d)_  
  Quantidade, raridade, base, afixos por iLvl, nomes; testes.
- [ ] **M6-02** Itens no chão e coleta _(código, ~2 d)_ — **escrito, validar**  
  Drops caem em volta do corpo com cor de raridade; clique anda até o item e coleta (comando PickUp); ouro coletado ao passar por cima (balance.loot.autoPickupGold); Alt mostra todos os nomes sem sobreposição; mochila cheia deixa o item no chão. Some ao sair da área.
- [ ] **M6-03** Inventário em grade 10×6 e equipamento _(UI, ~4 d)_ — parte **escrito, validar**  
  Feito: tela no I (client/inventory_view.py) com slots de equipamento e grade 10×6; equipar arrastando para o slot, duplo clique ou Ctrl+clique; o mesmo tira; requisitos (borda vermelha, aviso); troca no lugar da mochila; comandos EquipItem/UnequipItem no World; tooltip do item (describe_item). Slots de equipamento em items.json (equipment): dois anéis; a mão secundária aceita escudo ou arma de uma mão (duas armas alternam os golpes, sem guarda nem habilidades ativas de Escudo). Falta: itens ocupando várias células (size) e os afixos valerem no combate (hoje só a base conta: dano, armadura, escudo).
- [ ] **M6-04** Tooltip com comparação ao equipado _(UI, ~1.5 d)_ — **escrito, validar**  
  Shift sobre um item da mochila: diferenças em verde/vermelho por atributo (describe.compare_items) e o item vestido ao lado.
- [ ] **M6-05** Tela do baú compartilhado e vendedor _(UI, ~2 d)_  
  Tela do baú (itens e ouro, 100 espaços) usando o SharedStash do núcleo; vendedor com estoque renovado.
- [ ] **M6-06** 5 itens únicos do MVP _(conteúdo, ~1 d)_  
  Tabela de únicos com propriedades fixas e faixas; tirar o 'único vira raro'.
- [ ] **M6-07** Save completo _(código, ~2 d)_  
  Feito: save v2 do personagem (níveis, pontos, equipamento, mochila, ouro, consumíveis) em arquivo versionado com backup (core/saves.py), F5/`save`, salva ao sair e reabre na vila. Falta: waypoints ativados e gravar o baú compartilhado em disco.
- [x] **M6-08** Baú compartilhado no núcleo _(código, ~0.5 d)_  
  SharedStash: arquivo próprio, versionado, lido por todos os personagens; testes de ida e volta.

## M7 – Vertical slice

**Pronto quando:** 5 pessoas de fora terminam o Ato 1; feedback coletado

- [ ] **M7-01** Direção de arte e pipeline 3D → sprite _(arte, ~5 d)_  
  Definir paleta e escala; protótipo de personagem em 8 direções renderizado no Blender.
- [ ] **M7-02** Tileset dos 3 biomas do Ato 1 _(arte, ~5 d)_  
  Planície, caverna, cripta (packs licenciados ou próprios).
- [ ] **M7-03** Sprites do guerreiro e das 3 famílias _(arte, ~10 d)_  
  Parado, andar, atacar ×2, bloquear, ser atingido, morrer. Protótipo com a arte do Flare (M7-09) cobre o guerreiro, Mortos-vivos e Saqueadores. Falta: Feras (lobo, lobo alfa, javali), 2ª variação de ataque e a arte própria (M7-01).
- [ ] **M7-04** Áudio: impactos, bloqueio, drops, level up, música _(áudio, ~4 d)_  
  Prioridade nos sons de feedback de combate.
- [ ] **M7-05** Menus: título, opções, novo jogo/continuar _(UI, ~2 d)_  
  Volume, resolução, idioma, teclas.
- [ ] **M7-06** Localização _(código, ~2 d)_  
  Jogo todo em inglês desde out/2026 (nomes nos JSON, mensagens no código). Falta: mover as mensagens do cliente para um arquivo de strings e decidir se PT-BR entra no lançamento.
- [ ] **M7-07** Playtest com 5 pessoas de fora _(produção, ~3 d)_  
  Roteiro, gravação, questionário; consolidar feedback.
- [ ] **M7-08** Planilha de licenças de assets _(jurídico, ~0.5 d)_ — **escrito, validar**  
  Arquivo, autor, fonte, licença, exige crédito: `docs/licencas_assets.csv` (36 folhas do Flare). Falta: conferir o autor de cada folha no histórico do flare-game e pôr os créditos numa tela do jogo.
- [ ] **M7-09** Sprites animados de protótipo (arte do Flare, CC-BY-SA 3.0) _(arte/código, ~1.5 d)_ — **escrito, validar**  
  `tools/import_flare.py` converte as folhas do Flare (escala 1/3 para tiles 64×32, atlas por folha, paleta de 256 cores) para o formato próprio; `client/sprites.py` desenha em 8 direções com pose pelo estado do núcleo (parado, andar, golpe sincronizado com o tempo do ataque, bloquear, ser atingido com brilho, morrer + cadáver). Jogador em camadas com equipamento visível; Esqueleto, Arqueiro, Necromante, Zumbi, Bandido, Lanceiro e Xamã. Validar no jogo: tamanho na tela, leitura do golpe, desempenho com 60 monstros (M0-06).

## M8 – Vitrine

**Pronto quando:** Página da Steam publicada; trailer e demo estáveis

- [ ] **M8-01** Conta Steamworks, taxa e dados fiscais _(negócio, ~1 d)_  
  Steam Direct, W-8BEN, banco. Avaliar abrir empresa com contador.
- [ ] **M8-02** Página 'Em breve' na Steam _(marketing, ~2 d)_  
  Capturas, descrição, tags, classificação etária.
- [ ] **M8-03** Trailer de 60–90 s _(marketing, ~3 d)_  
  Combate, loot, mapas gerados; gancho nos primeiros 5 s.
- [ ] **M8-04** Demo para o Steam Next Fest _(produção, ~3 d)_  
  Ato 1 parcial; build estável e telemetria local.
- [ ] **M8-05** Devlogs e comunidade _(marketing, ~1 d)_  
  GIFs de combate semanais; Reddit, X, YouTube, Discord.
- [ ] **M8-06** Busca de marca do nome Aethelgard _(jurídico, ~0.5 d)_  
  INPI (Brasil), USPTO e EUIPO; domínio e nome livre na Steam. Já existe um projeto no itch.io com esse nome: avaliar subtítulo. Antes de anunciar.
- [ ] **M8-07** Definir licença da arte e do som _(jurídico, ~0.5 d)_  
  Código é GPL-3.0; assets precisam de licença própria (ex.: CC-BY-NC-SA ou todos os direitos reservados) antes de publicar arte. Atenção: o que for derivado da arte do Flare (M7-09) tem de seguir CC-BY-SA 3.0 (sem NC); a escolha vale só para a arte própria.

## M9 – Lançamento

**Pronto quando:** Checklist de lançamento da Steam completo

- [ ] **M9-01** Integração Steamworks (conquistas, nuvem) _(código, ~3 d)_  
  Biblioteca Python para a API da Steam (avaliar SteamworksPy ou ctypes) e Steam Cloud para os saves.
- [ ] **M9-02** Gamepad completo e Steam Deck verificado _(código, ~4 d)_  
  Todas as telas navegáveis no controle; texto legível em 800p.
- [ ] **M9-03** Polimento e correção de bugs _(produção, ~10 d)_  
  Lista de bugs do playtest e da demo.
- [ ] **M9-04** Responder às novas questões do GDD (19.2) _(design, ~1 d)_  
  Diferenciação sem teto global, recarga da guarda, guarda contra projéteis, nome. As 9 questões da v0.2 foram decididas na v0.3.
- [x] **M9-05** Curvas próprias no lugar das tabelas do D2 _(balanceamento, ~1 d)_  
  Curvas por fórmula em data/curves.json, mesmo ritmo; planilha v0.3 e simulação iguais.
- [ ] **M9-06** Checklist de lançamento da Steam _(negócio, ~2 d)_  
  Builds por plataforma, depots, preço, desconto de lançamento, revisão da Valve.
- [ ] **M9-07** Build com PyInstaller para Windows, Linux e macOS _(infra, ~2 d)_  
  Um executável por sistema, testado sem Python instalado; conferir falso positivo de antivírus.

## M10 – Multiplayer

**Pronto quando:** Dois jogadores terminam o Ato 1 juntos pela Steam, sem dessincronização

- [x] **M10-01** Eventos com PlayerId e XP por jogador _(código, ~0.5 d)_  
  Todo evento de jogador carrega PlayerId; cada ProficiencySet só ouve o próprio. Testado.
- [x] **M10-02** Entrada como comandos aplicados pelo núcleo _(código, ~3 d)_  
  World recebe MoveTo, AttackTarget, UseSkill, SetGuard; o cliente só desenha.
- [ ] **M10-03** Decidir XP em grupo, escala de monstros e loot _(design, ~1 d)_  
  D2: +35% de XP dividido por nível, vida e XP × (n+1)/2. Loot individual ou compartilhado.
- [ ] **M10-04** Rede com Steam Networking e host autoritativo _(código, ~10 d)_  
  Host simula; clientes enviam comandos e recebem estado.
- [ ] **M10-05** Sincronizar monstros, loot e transições de área _(código, ~8 d)_  
  Mesma seed no host e nos clientes; deltas de estado.
- [ ] **M10-06** Lobby e convite de amigos pela Steam _(código, ~3 d)_  
  Criar jogo, convidar, entrar pelo overlay.
