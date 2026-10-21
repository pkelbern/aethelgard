# Backlog de desenvolvimento — Aethelgard

Gerado a partir do roadmap do GDD (seção 17). Cada marco só fecha quando o critério de pronto for atingido.
Estimativas em dias de trabalho de uma pessoa, só para ordem de grandeza.

Status: [x] feito e testado · [ ] com a marca **escrito, validar** = código pronto que ainda não foi validado · [ ] a fazer.

| Marco | Feito | Validar | Total | Dias restantes |
|---|---|---|---|---|
| M0 – Base | 4 | 0 | 8 | 2.5 |
| M1 – Combate | 6 | 9 | 17 | 4.7 |
| M2 – Proficiências | 4 | 0 | 6 | 1.5 |
| M3 – Árvores | 2 | 5 | 13 | 18 |
| M4 – Monstros | 2 | 2 | 8 | 9.5 |
| M5 – Mundo | 4 | 2 | 8 | 6 |
| M6 – Loot | 2 | 6 | 10 | 2.5 |
| M7 – Vertical slice | 0 | 4 | 10 | 24.5 |
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
  Contador de FPS e cena de estresse. Se faltar desempenho: só atualizar monstros perto do jogador e agrupar o desenho. Medido sem tela (out/2026): 60 monstros amontoados num quadrado 8×8 na planície ~51 fps, cripta ~70 fps; o custo maior é colisão entre corpos (`blocked_by_body` é O(n²)): grade espacial resolve.
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
  Q/W (GDD 12.3): Vida 40% do máximo em 2 s, Energia 50% em 1,5 s, recarga de 0,5 s por tipo de poção, várias se somam (items.json consumables). Contador e recarga (com os segundos restantes, como nas habilidades) no HUD ao lado da barra. Falta: cinto.
- [ ] **M1-07** Projéteis de monstros à distância _(código, ~1.5 d)_ — **escrito, validar**  
  Arqueiro (flecha física), Xamã e Necromante (rajada mágica) atiram projéteis visíveis em linha reta para onde o jogador estava, sem desviar; paredes param; só atiram com linha de visão. Guarda bloqueia físicos; mágicos só com Bulwark (decide a questão 3 do GDD 19.2). Quem atira recua de quem chega a menos de 3 tiles (balance.json combat.rangedKiteTiles) antes de atirar de novo; encurralado, atira de onde está. core/projectiles.py.
- [x] **M1-08** Guarda ativa com escudo (segurar Espaço) _(código, ~1.5 d)_  
  Arco frontal, custo de Energia por bloqueio, janela perfeita com contra-ataque (Aparar), guarda quebrada atordoa. Testado no mundo em tempo real.
- [x] **M1-09** Recarga curta entre guardas _(balanceamento, ~0.5 d)_  
  0,3 s para erguer a guarda de novo (evita abuso do aparo perfeito).
- [ ] **M1-14** Sem esquiva com a guarda erguida _(regra, ~0.2 d)_ — **escrito, validar**  
  Golpe dentro do arco da guarda erguida sempre chega à guarda (vira bloqueio); pelas costas a esquiva por Defesa continua valendo.
- [ ] **M1-15** Shift: atacar sem se mexer _(UI, ~0.3 d)_ — **escrito, validar**  
  Shift+clique (e segurar) golpeia na direção do mouse sem andar e acerta quem estiver ao alcance na frente (arco de 90°); com arco, atira nessa direção. Comando AttackInPlace.
- [ ] **M1-16** Vantagem do corpo a corpo contra quem luta à distância _(balanceamento, ~0.3 d)_ — **escrito, validar**  
  Golpe corpo a corpo em quem usa arco ou ataca à distância (arqueiros, xamãs, necromantes, o jogador com arco): +25% de dano e +25% de precisão (balance.json combat.meleeVsRanged*).
- [ ] **M1-11** Bloqueio parcial: o golpe bloqueado ainda fere _(balanceamento, ~0.5 d)_ — **escrito, validar**  
  O bloqueio para 70% do golpe (balance.guard.blockReduction) + base do escudo (0/5/10/15%) + Postura Defensiva (+2,5%/ponto), no máximo 95% (blockReductionCap); a armadura age sobre o que passa. Aparo perfeito para 95%. Validar no jogo se 70% de base está bom.
- [ ] **M1-12** Colisão entre corpos _(código, ~1 d)_ — **escrito, validar**  
  Jogador e monstros não se atravessam; contorno pela borda do corpo; grupos nascem sem sobreposição. Raio por tipo (monsters.json) × sizeMult do rank; monstro sem corpo exige collides=false explícito e o quadro do alvo avisa ("Ethereal"). Corpos do tamanho dos sprites (out/2026): humanoides 0,42, Zumbi 0,46, Lobo 0,6, Lobo Alfa e Javali 0,66; o alcance corpo a corpo conta até a borda do corpo. Paredes param a borda do corpo (caixa de ±raio, até `collision.wallMarginMax` 0,45, então corredor de 1 célula ainda passa). O alcance de um monstro também conta a própria largura (alcance + raio − 0,42): o chefe (2,2× o tamanho) alcança quem encosta nele.
- [ ] **M1-13** Segurar o botão do mouse repete o clique _(UI, ~0.5 d)_ — **escrito, validar**  
  Segurando no chão continua andando para o cursor; segurando num monstro continua atacando-o até ele morrer.
- [ ] **M1-17** Sem combate na cidade _(regra, ~0.2 d)_ — **escrito, validar**  
  Na Vila o World recusa atacar e usar habilidades ("You cannot fight in town"), como no D2.
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
- [x] **M3-02** Tela de árvores _(UI, ~3 d)_  
  Feito: janela desenhada (T, client/skill_tree_view.py) com uma aba por proficiência, ativos à esquerda e passivos à direita por camada, linhas de pré-requisito, barra de XP de uso, tooltip desenhado pelo jogo (core/describe.py) com descrição, nível atual e próximo, custo, sinergias, portões e requisitos. Falta: arte dos ícones.
- [ ] **M3-13** Árvores com pré-requisitos e ativos que sobem pelo uso _(código, ~2 d)_ — **escrito, validar**  
  Ativo custa 1 ponto para desbloquear e depois só sobe usando (XP próprio = 0,75 × XP de proficiência dos golpes dele, mesma curva, sem teto). Fireball pede Fire Bolt, Multishot pede Power Shot etc.; passivos em cadeia (Kindling → Ignite → Quick Casting → Searing Heat). Portões: nível 10 do ativo pede 5 pontos num passivo da árvore, nível 20 pede 10. Bulwark e War Cry ganham XP por uso perto de monstros. Magias não se aprendem mais pela varinha; a varinha só soma nível e deixa lançar antes de desbloquear. Save v6 (migra v5). Validar no jogo: ritmo de subida do Fire Bolt e do Precise Strike no Ato 1 e se 0,75 (skills.json skillXpRate) está bom.
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

- [ ] **M3-08** Duas armas e árvore Dual Wield _(código, ~3 d)_ — **escrito, validar**  
  4ª proficiência (XP de todos os golpes com duas armas e das habilidades da árvore — Lâminas é só para uma lâmina sem par; perfil 1 FOR / 2 DES). Mão secundária com 70% do dano (balance.dualWield). Árvore: Double Strike (golpe com as duas armas), Off-Hand Training, Frenzy (acúmulos de velocidade), Ambidexterity, Whirling Blades (360° com as duas), Blade Guard (desvio de golpes corpo a corpo), Blade Dance (falta implementar), Bloodlust (roubo de vida). Títulos Twinblade, Blademaster, Skirmisher, Marauder.

- [ ] **M3-09** Arcos e árvore Archery _(código, ~3 d)_ — **escrito, validar**  
  5ª proficiência (todo tiro de arco; 3 DES por nível). Arco é de duas mãos (tira escudo e segunda arma; mochila cheia recusa), atira flecha reta de longe com linha de visão. Árvore: Power Shot, Steady Aim, Multishot (leque que abre perto e fecha longe: cobre spreadTiles de largura na distância do alvo, entre 8° e 70°), Eagle Eye (alcance), Piercing Arrow (atravessa), Quick Draw, Rain of Arrows (falta implementar), Hunter's Mark. 4 bases de arco. Personagem novo leva um arco e uma segunda espada na mochila. Falta: arte do arco equipado (sprites.json gear).

- [ ] **M3-10** Magia: base e escola Pyromancy (fogo, dano em área) _(código, ~4 d)_ — **escrito, validar**  
  6ª proficiência (perfil 3 INT; XP do dano das magias). Magias se desbloqueiam na árvore como qualquer ativo (M3-13); a varinha (slot 'wand') soma o nível dela à magia e deixa lançá-la enquanto equipada, mesmo antes de desbloquear. Magia sempre acerta, ignora armadura e sofre a resistência do alvo (zumbi −25%, esqueletos −10%, xamã e necromante +20%, Fire Enchanted +50%); +1% de dano mágico por INT (balance.json attributes). Árvore: Fire Bolt, Kindling, Fireball (explode em área), Ignite (queima), Fire Wall, Quick Casting, Meteor, Searing Heat. Varinha vai em qualquer mão (com escudo, ou ao lado de uma arma); na mão principal o golpe é de mão vazia. Varinhas: Ember (Fire Bolt, nível 1, todo personagem novo leva uma), Cinder (Fireball, iLvl 5), Blaze (Fire Wall, iLvl 10), Star (Meteor, iLvl 16). Títulos Pyromancer, Arcane Blade, Battlemage, Hellknight, Flamedancer, Flame Archer. core/spells.py; cliente em client/spell_view.py (fogo no chão, explosões, meteoro caindo, monstros queimando). Fire Wall e Meteor só onde há linha de visão (não atrás de paredes); fogo no chão e queimadura nunca causam recuo (hit recovery). Validar no jogo: custo de Energia e dano do Fire Bolt nos níveis 1–6.
- [ ] **M3-11** Escola de suporte (cura e bênçãos) _(código, ~3 d)_  
  Mesmo modelo da Pyromancy (ativos desbloqueados na árvore e evoluídos pelo uso; varinha soma nível). A definir: nome, nós, como ganha XP (vida curada? tempo de bênção em combate?).
- [ ] **M3-12** Escola de defesa (barreiras e proteções) _(código, ~3 d)_  
  Mesmo modelo; XP pelo dano que as barreiras absorvem (como a Armadura).

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
  Chegada ao lado da saída de origem, sem voltar sem querer. Saída é uma zona (raio `exits.touchRadiusTiles` 1,4) que dispara ao entrar nela; chegar dentro dela não leva de volta. No mapa: área dourada pulsando do tamanho da zona, placa/escada/boca de caverna maiores, nome do destino numa plaqueta por cima de tudo, sem árvores num raio de 3 células.
- [ ] **M5-03** Prefabs feitos à mão nas áreas abertas _(código, ~3 d)_  
  Acampamentos, ruínas, santuários e baús desenhados no Tiled (.tmx) e carimbados na geração pela seed.
- [ ] **M5-04** Salas pré-montadas nas masmorras _(código, ~3 d)_  
  Trocar retângulos por salas desenhadas no Tiled, com conectores marcados como objetos.
- [ ] **M5-09** Continuidade entre áreas _(código, ~0.5 d)_ — **escrito, validar**  
  areas.json mapPos coloca as áreas no mapa do ato: a saída para um vizinho fica na borda voltada para ele, e a volta na borda oposta, no mesmo ponto ao longo da borda (a vila, feita à mão, fixa o ponto). validate() exige vizinhos lado a lado.
- [ ] **M5-10** Começo mais leve _(balanceamento, ~0.3 d)_ — **escrito, validar**  
  Grupos menores no início (areas.json packSize: Planície 2–3, Caverna 2–4, Bosque 3–4; padrão 3–5) e Planície menor (96×96). Simulação: Lâminas 4 ao sair da Planície (antes 5), fim do Ato 1 igual (13 / 9 / 7).
- [ ] **M5-05** Waypoints com menu de viagem _(código, ~1.5 d)_ — **escrito, validar**  
  Pisar ativa (salvo no personagem, save v4) e abre o menu com todos os waypoints do ato; os não encontrados ficam cinza. Chegar por waypoint não reabre o menu até sair dele. Comando TravelWaypoint. Nunca nasce a menos de 4 tiles de uma saída (escada, entrada, borda).
- [ ] **M5-06** Portal de cidade _(código, ~1 d)_ — **escrito, validar**  
  Sempre disponível, sem pergaminho (recarga de 5 s em balance.json townPortal). E invoca o portal: 1,5 s parado (townPortal.castSeconds) com uma animação, e ele abre um passo à sua frente; andar, atacar ou levar um golpe forte interrompe (sem gastar a recarga). O portal aparece no mapa (Tab e o pequeno). Entrar leva à vila, onde o portal aparece ao lado do waypoint; entrar de novo volta ao mesmo ponto e o portal se fecha. As áreas agora guardam o estado na partida (monstros mortos continuam mortos, itens no chão continuam lá).
- [x] **M5-07** Névoa de guerra e mapa automático (Tab) _(UI, ~2 d)_  
  Feito: campo de visão do tcod revela o mapa, monstros fora da visão somem, mapa pequeno no canto e grande com Tab. Saídas, waypoint, NPCs e portal só aparecem depois de explorados.
- [x] **M5-08** Cidade feita à mão no Tiled _(conteúdo, ~2 d)_  
  Feito: data/maps/village.tmx com entrada, saída, waypoint, vendedora e baú (marcadores); clicar no NPC anda até ele e abre a janela.

## M6 – Loot

**Pronto quando:** Drops seguem as tabelas; inventário, baú e vendedor funcionam

- [x] **M6-01** Gerador de loot no núcleo _(código, ~1 d)_  
  Quantidade, raridade, base, afixos por iLvl, nomes; testes.
- [ ] **M6-02** Itens no chão e coleta _(código, ~2 d)_ — **escrito, validar**  
  Drops caem em volta do corpo com cor de raridade; clique anda até o item e coleta (comando PickUp); ouro coletado ao passar por cima (balance.loot.autoPickupGold); Alt mostra todos os nomes sem sobreposição; mochila cheia deixa o item no chão. Some ao sair da área.
- [ ] **M6-03** Inventário em grade 10×6 e equipamento _(UI, ~4 d)_ — parte **escrito, validar**  
  Feito: tela no I (client/inventory_view.py) com slots de equipamento e grade 10×6; equipar arrastando para o slot, duplo clique ou Ctrl+clique; o mesmo tira; requisitos (borda vermelha, aviso); troca no lugar da mochila; comandos EquipItem/UnequipItem no World; tooltip do item (describe_item). Slots de equipamento em items.json (equipment): dois anéis; a mão secundária aceita escudo ou arma de uma mão (duas armas alternam os golpes, sem guarda nem habilidades ativas de Escudo). Falta: itens ocupando várias células (size) e os afixos valerem no combate (hoje só a base conta: dano, armadura, escudo); a soma já existe em `Loadout.totals()` (afixos, únicos, peças e bônus de conjunto), falta ler dela em combat.py, CharacterSheet.item_bonus e velocidade de movimento.
- [ ] **M6-04** Tooltip com comparação ao equipado _(UI, ~1.5 d)_ — **escrito, validar**  
  Shift sobre um item da mochila: diferenças em verde/vermelho por atributo (describe.compare_items) e o item vestido ao lado.
- [ ] **M6-05** Tela do baú compartilhado e vendedor _(UI, ~2 d)_ — **escrito, validar**  
  client/item_windows.py: uma janela-base para inventário, baú e loja (grades, arrastar, Shift/Ctrl/duplo clique, tooltip). Baú: itens e ouro, arquivo próprio shared_stash.json com backup. Loja: estoque normal/mágico renovado a cada volta à vila, preços em items.json trade, venda a 20%, o que você vende pode ser recomprado, poções e pergaminho sempre à venda. Comandos Buy/Sell/BuyConsumable/StashPut/StashTake/StashGold exigem estar perto do NPC.
- [ ] **M6-06** 5 itens únicos do MVP _(conteúdo, ~1 d)_ — **escrito, validar**  
  items.json `uniques`: Thornbite (Short Sword), Wanderer's Stride (Boots), Emberline (Hunter's Bow), Oathkeeper's Ward (Round Shield), Cinderheart (Amulet). Base, nome e linhas fixos; só as faixas min–max rolam. Podem fugir das regras de afixo (ex.: bota com velocidade de ataque). Sem único permitido no iLvl, vira raro.
- [ ] **M6-09** Regras de afixo por classe, joias nunca normais e conjuntos _(código + conteúdo, ~1.5 d)_ — **escrito, validar**  
  items.json `itemClasses` (sword, bow, wand, shield, body, helm, gloves, boots, belt, ring, amulet; grupos weapon/armor/jewelry); cada afixo diz em `on` onde pode rolar (arco nunca dá habilidade de Lâminas nem bloqueio; bota dá velocidade de movimento e nunca de ataque; luva nunca dá movimento). Anel e amuleto caem no mínimo mágicos (`minRarity`), inclusive na loja. Raridade Conjunto (verde) entre raro e único: Sentinel's Vigil (elmo, cota, escudo) e Grey Fletcher (luvas, botas, arco), bônus parciais com 2 e 3 peças (`Loadout.totals`). Tooltip mostra o conjunto e acende os bônus ativos. Falta: os bônus valerem no combate (vem com o M6-03).
- [ ] **M6-07** Save completo _(código, ~2 d)_  
  Feito: save v2 do personagem (níveis, pontos, equipamento, mochila, ouro, consumíveis) em arquivo versionado com backup (core/saves.py), F5/`save`, salva ao sair e reabre na vila. Waypoints (v4) e baú compartilhado em disco também.
- [x] **M6-08** Baú compartilhado no núcleo _(código, ~0.5 d)_  
  SharedStash: arquivo próprio, versionado, lido por todos os personagens; testes de ida e volta.

## M7 – Vertical slice

**Pronto quando:** 5 pessoas de fora terminam o Ato 1; feedback coletado

- [ ] **M7-10** Ficha do personagem (C) _(UI, ~1 d)_ — **escrito, validar**  
  core/sheet.py monta as linhas com as mesmas fórmulas do combate (nível, título, ouro, proficiências com XP, atributos, vida/energia e regeneração, dano, DPS médio (ataques/s × dano médio com críticos × chance de acerto), precisão com chance de acerto contra monstro do mesmo nível, crítico, alcance, defesa com chance de errar, armadura com redução, bloqueio). Passar o mouse numa linha explica de onde vem o número. Regeneração de Energia (2%/s, 0,5%/s com guarda) foi para balance.json. O que cada ponto de atributo faz (precisão, crítico e defesa por DES, dano por FOR, dano mágico por INT) está em balance.json attributes.
- [ ] **M7-01** Direção de arte e pipeline 3D → sprite _(arte, ~5 d)_  
  Definir paleta e escala; protótipo de personagem em 8 direções renderizado no Blender.
- [ ] **M7-02** Tileset dos 3 biomas do Ato 1 _(arte, ~5 d)_ — **escrito, validar**  
  Tilesets do Flare (CC-BY-SA 3.0), baixados quando o jogo abre: grama com árvores e pedras nas bordas (vila, planície, bosque, colinas), caverna (terra e rocha) e cripta (pedra, paredes com estantes, tochas, estandartes). Paredes com a face virada para a sala, ordenadas em profundidade com os personagens e translúcidas quando o herói está atrás. Objetos: waypoint (apagado/aceso), escadas, entrada da masmorra, placas nas saídas, baú, mercadora com sprite; itens no chão e flechas/rajadas com arte. Falta: bosque e colinas com cara própria (hoje iguais à planície), casas na vila, ícones de itens e habilidades (icons.png do Flare).
- [ ] **M7-03** Sprites do guerreiro e das 3 famílias _(arte, ~10 d)_  
  Parado, andar, atacar ×2, bloquear, ser atingido, morrer. Protótipo com a arte do Flare (M7-09) cobre o guerreiro, Mortos-vivos e Saqueadores. Lobo e Lobo Alfa: pacote 'Wolf monsters animated' (Danimal, CC-BY-SA, OpenGameArt), baixado quando o jogo abre. Javali: 'Boar enemies' (Danimal, CC-BY-SA 4.0). Tamanhos igualados pela altura em pé, na proporção do Diablo 2 (herói ≈ 94 px). Falta: 2ª variação de ataque e a arte própria (M7-01). Validar no jogo os nomes dos arquivos dentro do pacote dos lobos (escolhidos por padrão de nome).
- [ ] **M7-04** Áudio: impactos, bloqueio, drops, level up, música _(áudio, ~4 d)_  
  Prioridade nos sons de feedback de combate.
- [ ] **M7-05** Menus: título, opções, novo jogo/continuar _(UI, ~2 d)_  
  Volume, resolução, idioma, teclas. Feito: Esc sem nada aberto abre o menu do jogo (Continuar, Salvar, Salvar e sair) e pausa o jogo; falta a tela de título e as opções.
- [ ] **M7-06** Localização _(código, ~2 d)_  
  Jogo todo em inglês desde out/2026 (nomes nos JSON, mensagens no código). Falta: mover as mensagens do cliente para um arquivo de strings e decidir se PT-BR entra no lançamento.
- [ ] **M7-07** Playtest com 5 pessoas de fora _(produção, ~3 d)_  
  Roteiro, gravação, questionário; consolidar feedback.
- [ ] **M7-08** Planilha de licenças de assets _(jurídico, ~0.5 d)_ — **escrito, validar**  
  Arquivo, autor, fonte, licença, exige crédito: `docs/licencas_assets.csv` (36 folhas do Flare). Falta: conferir o autor de cada folha no histórico do flare-game e pôr os créditos numa tela do jogo.
- [ ] **M7-09** Sprites animados de protótipo (arte do Flare, CC-BY-SA 3.0) _(arte/código, ~1.5 d)_ — **escrito, validar**  
  A arte não fica no GitHub: toda vez que o jogo abre ele confere as folhas e, se falta alguma, mostra uma tela de carregamento e baixa só o que falta para a pasta do usuário (Flare num commit fixo; pacotes .7z do OpenGameArt); sem sprite, aparece um xadrez magenta. `tools/fetch_art.py` (também `aethelgard-art`) faz o mesmo à mão e converte as folhas do Flare (escala 1/3 para tiles 64×32, atlas por folha, paleta de 256 cores) para o formato próprio; `client/sprites.py` desenha em 8 direções com pose pelo estado do núcleo (parado, andar, golpe sincronizado com o tempo do ataque, bloquear, ser atingido com brilho, morrer + cadáver). Jogador em camadas com equipamento visível (duas armas: a da mão secundária é a arte espelhada e os golpes alternam de braço); Esqueleto, Arqueiro, Necromante, Zumbi, Bandido, Lanceiro e Xamã. Validar no jogo: tamanho na tela, leitura do golpe, desempenho com 60 monstros (M0-06).

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
