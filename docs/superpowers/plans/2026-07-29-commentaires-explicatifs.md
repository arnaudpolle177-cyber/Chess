# Commentaires explicatifs — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** faire dire au coach *pourquoi* il propose ce coup-là, en remplissant le
champ `cause` du contrat de fragment avec des faits calculés — jamais un jugement.

**Architecture :** trois composants indépendants qui convergent vers un seul point
de rendu. (A) `prophylaxis.py`, fichier neuf, dit ce que le coup EMPÊCHE — un appel
moteur sur la position avec le trait inversé, puis une vérification python-chess
pure. (B) `move_intent.py` gagne quatre faits géométriques comptés sans moteur. (C)
`narration_v2.render` n'autorise la `cause` que quand le meilleur coup ne se détache
pas nettement du deuxième. Le texte lui-même est fabriqué à UN seul endroit
(`fragment_library.fragments_for_intent`), pas dans les onze fonctions de fragment.

**Tech Stack :** Python 3, python-chess, Stockfish via `chess.engine` (UCI),
`unittest`-free — les tests du dépôt sont des fichiers `test_*.py` avec des
fonctions `assert` et un `__main__` qui les appelle.

## Écart assumé par rapport à la spec

La spec (`docs/superpowers/specs/2026-07-28-commentaires-explicatifs-design.md`)
prévoyait de calculer la prophylaxie **en tâche de fond**, « même schéma que
`_attach_scenario_async` ». Ce plan la calcule **de façon synchrone**, sur le
moteur dédié `scenario_engine`. Deux raisons, toutes deux vérifiées dans le code :

1. `_attach_scenario_async` n'est plus déclenché automatiquement — seulement sur
   clic de carte, via `request_scenario` (`web_bridge.py:1341`). La prophylaxie
   n'apparaîtrait donc qu'après un clic.
2. Le chemin asynchrone repousse l'entrée déjà affichée. Pour le « scénario »
   c'est indolore (un bloc apparaît). Pour la `cause`, cela **réécrirait le
   paragraphe sous les yeux du lecteur**.

Le coût mesuré est de 87 ms à profondeur 10, **une fois par position** (le coup
menaçant ne dépend que de l'échiquier, pas du profil), sur un chemin qui prend
déjà plusieurs secondes et sur un moteur qui n'est pas celui de la flèche
principale. Le calcul est mis en cache par FEN et borné par un timeout.

## Global Constraints

- **AUCUN FAIT INVENTÉ.** Tout ce qui est affirmé doit être calculé sur la
  position ou sur l'intention. Aucun jugement comparatif ("mieux", "meilleur",
  "pire", "la case la plus faible") dans un texte de `cause`.
- Les fragments sont des **clauses** : initiale minuscule, aucune ponctuation
  finale (`narration_weaver._lead_sentence` compose `observation + " -- " + cause`).
- Tout le texte affiché est en **français accentué**. Les fichiers `test_*.py` et
  les commentaires de code du dépôt sont en français, accents autorisés.
- **Best-effort, mais journalisé** : toute défaillance de détection laisse le
  commentaire actuel intact (observation + plan), jamais un crash, jamais un texte
  vide — et l'exception est imprimée (`print(f"⚠ ...")`), jamais avalée.
- Encodage console Windows : lancer les scripts qui impriment de l'accentué avec
  `PYTHONIOENCODING=utf-8`, sinon `UnicodeEncodeError: 'gbk' codec`.
- Les tests se lancent à la main : `python test_xxx.py` depuis la racine du dépôt
  `C:\Users\Triv\Desktop\Vivado\test\Chess-main`. Il n'y a pas de runner.
- Chemin du moteur pour les scripts d'audit :
  `C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe`.

---

## Structure de fichiers

| Fichier | Responsabilité | Task |
|---|---|---|
| `prophylaxis.py` (créer) | menace adverse (1 appel moteur) + preuve qu'elle disparaît (pur) | 1 |
| `test_prophylaxis.py` (créer) | garde échec, disparitions prouvées, non-régression | 1 |
| `move_intent.py` (modifier) | 4 faits géométriques + champ `prophylaxis` sur `MoveIntent` | 2 |
| `test_move_intent.py` (modifier) | un cas par fait comptable | 2 |
| `fragment_library.py` (modifier) | `_explain_cause()` — seul endroit qui écrit un texte de cause | 3 |
| `test_fragment_library.py` (modifier) | textes de cause, garde anti-jugement | 3 |
| `narration_v2.py` (modifier) | `Selection.gap_cp`, seuil, passage de la prophylaxie | 4 |
| `test_narration_v2.py` (modifier) | seuil : écart large / écart faible | 4 |
| `web_bridge.py` (modifier) | appel moteur caché par FEN, câblage | 5 |
| `audit_causes.py` (créer) | vérification de bout en bout sur une vraie partie | 6 |

---

### Task 1 : détecteur de prophylaxie

**Files:**
- Create: `prophylaxis.py`
- Test: `test_prophylaxis.py`

**Interfaces:**
- Consumes: rien (fichier autonome, ne dépend que de `chess`).
- Produces:
  - `opponent_threat(engine, board, depth=THREAT_DEPTH) -> Optional[chess.Move]`
    (`engine` = instance `engine_analysis.ChessCoachEngine`, dont l'objet UCI est
    `engine.engine` — voir `variation_narrator._eval_at`).
  - `prevented_by(board, my_move, threat) -> Optional[dict]` avec la forme
    `{"san": str, "reason": "captured"|"blocked"}`.
  - Constante `THREAT_DEPTH = 10`.

- [ ] **Step 1: écrire le test qui échoue**

Créer `test_prophylaxis.py` :

```python
"""Prophylaxie : ce que le coup EMPECHE.

La moitie testee ici est la moitie PURE (prevented_by) -- aucun moteur. La
moitie moteur (opponent_threat) n'est testee que sur sa garde d'echec, le seul
point ou elle peut produire une position ILLEGALE : python-chess accepte
board.push(chess.Move.null()) en echec sans lever la moindre erreur et fabrique
silencieusement une position invalide (is_valid() -> False).
"""
import chess

import prophylaxis


def test_menace_annulee_par_capture_de_la_piece():
    # Cavalier noir en g4 pret a sauter en f2 ; Fxg4 supprime la piece qui
    # devait jouer la menace.
    board = chess.Board("r1bqkb1r/pppp1ppp/2n5/4p3/2B1P1n1/5N2/PPPP1PPP/RNBQ1RK1 w kq - 0 1")
    threat = board.parse_san("Nxf2")           # coup NOIR, la menace
    my_move = board.parse_san("Bxg4")          # je prends le cavalier
    out = prophylaxis.prevented_by(board, my_move, threat)
    assert out is not None
    assert out["reason"] == "captured"
    assert out["san"] == "Nxf2"


def test_menace_toujours_disponible_ne_reclame_rien():
    board = chess.Board("r1bqkb1r/pppp1ppp/2n5/4p3/2B1P1n1/5N2/PPPP1PPP/RNBQ1RK1 w kq - 0 1")
    threat = board.parse_san("Nxf2")
    my_move = board.parse_san("h3")            # ne touche pas au cavalier de g4
    assert prophylaxis.prevented_by(board, my_move, threat) is None


def test_menace_annulee_parce_que_la_case_est_occupee():
    """Le coup n'enleve pas la piece menacante, mais rend le coup illegal :
    reason doit valoir "blocked", pas "captured"."""
    # A ecrire toi-meme : construis une position ou MON coup vient occuper la
    # case d'arrivee de la menace (ou interpose une piece sur sa ligne) sans
    # capturer la piece menacante. Affiche la position avant d'ecrire la FEN.
    # Assertions attendues :
    #   out = prophylaxis.prevented_by(board, my_move, threat)
    #   assert out is not None and out["reason"] == "blocked"


def test_aucune_menace_aucun_plantage():
    board = chess.Board()
    assert prophylaxis.prevented_by(board, board.parse_san("e4"), None) is None


def test_en_echec_pas_de_coup_nul():
    # Roi blanc en echec : le coup nul est ILLEGAL. La detection doit rendre
    # None sans jamais interroger le moteur (engine=None le prouve : si la
    # garde sautait, on planterait sur None.engine).
    check_board = chess.Board("rnbqkbnr/ppp2ppp/8/3pp3/6P1/5P2/PPPPP2P/RNBQKBNR w KQkq - 0 1")
    check_board.push_san("a3")
    check_board.push_san("Qh4")
    assert check_board.is_check()
    assert prophylaxis.opponent_threat(None, check_board) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
```

- [ ] **Step 2: lancer le test, vérifier qu'il échoue**

```
cd C:\Users\Triv\Desktop\Vivado\test\Chess-main
python test_prophylaxis.py
```
Attendu : `ModuleNotFoundError: No module named 'prophylaxis'`.

- [ ] **Step 3: écrire `prophylaxis.py`**

```python
"""
Prophylaxie : ce que le coup recommandé EMPÊCHE.

C'est le seul « pourquoi » profond d'un coup calme -- un coup tranquille est
rarement bon pour ce qu'il fait, il est bon pour ce qu'il enlève à
l'adversaire.

Deux temps, dont un seul coûte un appel moteur :

1. opponent_threat() -- on analyse la position avec le TRAIT INVERSÉ (coup
   nul) pour savoir ce que l'adversaire jouerait s'il avait la main. Un appel,
   profondeur 10, mesuré à 87 ms sur le moteur de fond (1 thread, hash 128 Mo).

2. prevented_by() -- python-chess pur, aucun appel supplémentaire. On ne
   réclame la prophylaxie que dans le cas INDISCUTABLE : après mon coup, la
   menace n'est plus LÉGALE. « La menace est devenue moins bonne » demanderait
   une seconde analyse et resterait discutable ; « la menace est illégale » est
   un fait binaire, vérifiable, et suffisant.
"""
import chess

THREAT_DEPTH = 10


def opponent_threat(engine, board, depth=THREAT_DEPTH):
    """
    Coup que l'adversaire jouerait s'il avait le trait, ou None.

    GARDE NON NÉGOCIABLE sur board.is_check() : le coup nul est illégal quand
    on est en échec, et python-chess NE LÈVE AUCUNE ERREUR dans ce cas -- il
    fabrique silencieusement une position invalide (is_valid() -> False). Sans
    cette garde on enverrait au moteur une position illégale et on
    exploiterait ce qu'il en retourne. C'est le piège principal de ce module.

    Retourne None (jamais d'exception) si : échec, partie finie, moteur absent
    ou moteur en échec -- « on ne sait pas », jamais une valeur par défaut.
    """
    if engine is None or board.is_check() or board.is_game_over():
        return None
    probe = board.copy()
    probe.push(chess.Move.null())
    if not probe.is_valid():
        return None  # ceinture et bretelles : on ne parle pas à un moteur d'une position illégale
    try:
        info = engine.engine.analyse(probe, chess.engine.Limit(depth=depth))
    except Exception as e:
        print(f"⚠ Menace adverse indisponible : {e}")
        return None
    pv = info.get("pv") or []
    return pv[0] if pv else None


def prevented_by(board, my_move, threat):
    """
    Preuve que `threat` (coup ADVERSE, légal dans `board` trait inversé) n'est
    plus jouable après `my_move`, ou None.

    board  : position AVANT mon coup, à MOI de jouer.
    my_move: mon coup (chess.Move).
    threat : la menace rendue par opponent_threat, ou None.

    Retourne {"san": <SAN de la menace>, "reason": "captured"|"blocked"} :
      - "captured" : la pièce qui devait jouer la menace n'est plus là ;
      - "blocked"  : elle est là, mais le coup n'est plus légal (interposition,
                     clouage, case occupée).
    None dès que la menace reste légale -- aucune prophylaxie réclamée.
    """
    if threat is None or my_move is None:
        return None
    try:
        # SAN calculé sur la position À TRAIT INVERSÉ : c'est la seule où la
        # menace est légale, donc la seule où board.san() est correct.
        probe = board.copy()
        probe.push(chess.Move.null())
        if not probe.is_valid() or threat not in probe.legal_moves:
            return None
        san = probe.san(threat)

        after = board.copy()
        after.push(my_move)
        after.push(chess.Move.null())   # à nouveau à l'adversaire de jouer
        if not after.is_valid():
            return None
        if threat in after.legal_moves:
            return None                 # la menace survit : rien à raconter

        piece_gone = after.piece_at(threat.from_square) is None
        return {"san": san, "reason": "captured" if piece_gone else "blocked"}
    except Exception as e:
        print(f"⚠ Prophylaxie indisponible : {e}")
        return None
```

- [ ] **Step 4: lancer le test, vérifier qu'il passe**

```
python test_prophylaxis.py
```
Attendu : `ok test_...` pour les cinq tests.

Si `test_menace_annulee_par_interposition` ou un autre cas échoue parce que la
FEN écrite ici ne produit pas la situation décrite, **corrige la FEN, pas
l'assertion** — vérifie la position à la main avec un petit script
`python -c "import chess; b=chess.Board('...'); print(b)"`. Trois des six FEN
d'un plan précédent de ce dépôt étaient illégales ; ne fais pas confiance à une
FEN sans l'avoir affichée.

- [ ] **Step 5: commit**

```bash
git add prophylaxis.py test_prophylaxis.py
git commit -m "Prophylaxie : detecte la menace adverse annulee par mon coup"
```

---

### Task 2 : contraste géométrique sur `MoveIntent`

**Files:**
- Modify: `move_intent.py` (dataclass `MoveIntent` ; closure `_mk` dans `detect_move_intent`)
- Modify: `test_move_intent.py`

**Interfaces:**
- Consumes: `prophylaxis.prevented_by` (Task 1) — seulement le TYPE du dict,
  transmis par l'appelant, jamais appelé ici.
- Produces: cinq nouveaux champs sur `MoveIntent`, tous avec défaut :
  `new_squares: int = 0`, `escapes_attack: bool = False`,
  `becomes_defended: bool = False`, `new_attack_square: Optional[int] = None`,
  `prophylaxis: Optional[dict] = None`.
  Et un paramètre supplémentaire :
  `detect_move_intent(board, chosen, why_motif=None, why_detail=None, prophylaxis=None)`.

- [ ] **Step 1: écrire les tests qui échouent**

Ajouter à la fin de `test_move_intent.py`, avant tout bloc `__main__` existant :

```python
# --- Contraste geometrique (faits comptables, sans moteur) -----------------
# Chaque fait est un COMPTAGE, donc indiscutable. La frontiere a ne pas
# franchir : "il vise f7, defendu par le seul roi" est un comptage ; "f7, la
# case la plus faible" est un jugement.

def _intent(fen, san, **kw):
    import chess
    import move_intent as mi
    board = chess.Board(fen)
    move = board.parse_san(san)
    return mi.detect_move_intent(board, {"move_uci": move.uci()}, **kw), board


def test_contraste_cases_nouvellement_controlees():
    # Le fou sort en c4 : il controle bien plus de cases depuis c4 que depuis f1.
    it, _ = _intent("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1", "Bc4")
    assert it.new_squares > 0, it.new_squares


def test_contraste_fuite_devant_une_piece_moins_chere():
    # Fou blanc en b5 attaque par le pion a6 : Ba4 le sort de l'attaque.
    it, _ = _intent("r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1", "Ba4")
    assert it.escapes_attack is True


def test_contraste_pas_de_fuite_quand_rien_nattaque():
    it, _ = _intent("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1", "Bc4")
    assert it.escapes_attack is False


def test_contraste_piece_adverse_nouvellement_attaquee():
    # Fb5 attaque le cavalier c6, qui n'etait attaque par rien avant.
    import chess
    it, _ = _intent("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1", "Bb5")
    assert it.new_attack_square == chess.C6, it.new_attack_square


def test_contraste_valeurs_neutres_par_defaut():
    # Un coup de pion sans effet notable ne doit rien affirmer.
    it, _ = _intent("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "h3")
    assert it.escapes_attack is False
    assert it.becomes_defended is False
    assert it.new_attack_square is None


def test_prophylaxie_transmise_telle_quelle():
    fake = {"san": "Nxf2", "reason": "captured"}
    it, _ = _intent("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1",
                    "Bc4", prophylaxis=fake)
    assert it.prophylaxis == fake
```

- [ ] **Step 2: lancer les tests, vérifier qu'ils échouent**

```
python test_move_intent.py
```
Attendu : `AttributeError: 'MoveIntent' object has no attribute 'new_squares'`
(ou `TypeError` sur le paramètre `prophylaxis`).

- [ ] **Step 3: ajouter les champs à la dataclass**

Dans `move_intent.py`, dans la docstring de `MoveIntent`, avant la ligne
fermante `"""`, ajouter :

```
    new_squares : nombre de cases que la pièce jouée contrôle DEPUIS son
                  arrivée et ne contrôlait pas depuis son départ. Comptage pur
                  (chess.Board.attacks), aucun jugement sur leur valeur.
    escapes_attack : la pièce jouée était attaquée par une pièce MOINS CHÈRE
                  qu'elle avant le coup, et ne l'est plus après. La condition
                  « moins chère » est ce qui rend le fait intéressant : être
                  attaqué par plus cher que soi n'est pas une menace.
    becomes_defended : la pièce jouée n'était défendue par personne sur sa case
                  de départ et l'est sur sa case d'arrivée.
    new_attack_square : case d'une pièce ADVERSE que la pièce jouée attaque
                  depuis son arrivée et n'attaquait pas depuis son départ
                  (None si aucune). La plus chère si plusieurs.
    prophylaxis : dict {"san", "reason"} rendu par prophylaxis.prevented_by,
                  ou None. TRANSMIS tel quel par l'appelant, jamais calculé
                  ici (ce module ne parle pas au moteur).
```

Puis, à la fin de la liste des champs (après `mate_in`) :

```python
    new_squares: int = 0
    escapes_attack: bool = False
    becomes_defended: bool = False
    new_attack_square: Optional[int] = None
    prophylaxis: Optional[dict] = None
```

- [ ] **Step 4: écrire le calcul du contraste**

Ajouter au niveau module de `move_intent.py`, juste avant `def detect_move_intent` :

```python
def _geometric_contrast(board, move):
    """
    Ce que le coup CHANGE, compté sans moteur. Quatre faits, quatre comptages :
    cases nouvellement contrôlées, fuite devant une pièce moins chère, pièce
    qui devient défendue, pièce adverse nouvellement attaquée.

    Retourne un dict prêt à être déversé dans MoveIntent (clés = noms des
    champs). Best-effort : en cas de coup illisible, tout à sa valeur neutre.
    """
    neutral = {"new_squares": 0, "escapes_attack": False,
               "becomes_defended": False, "new_attack_square": None}
    piece = board.piece_at(move.from_square)
    if piece is None:
        return neutral
    me = board.turn

    after = board.copy()
    after.push(move)

    # 1. Cases nouvellement contrôlées par la pièce jouée.
    before_att = board.attacks(move.from_square)
    after_att = after.attacks(move.to_square)
    new_squares = len(after_att & ~before_att)

    # 2. Fuite : attaquée par MOINS CHER qu'elle avant, plus après. Attaquée
    #    par plus cher que soi n'est pas une menace -- d'où le filtre de valeur.
    my_value = PIECE_VALUES.get(piece.piece_type, 0)
    def _attacked_by_cheaper(b, square, color):
        for sq in b.attackers(not color, square):
            attacker = b.piece_at(sq)
            if attacker and PIECE_VALUES.get(attacker.piece_type, 0) < my_value:
                return True
        return False
    escapes = (_attacked_by_cheaper(board, move.from_square, me)
               and not _attacked_by_cheaper(after, move.to_square, me))

    # 3. Devient défendue (elle ne l'était pas au départ).
    becomes_defended = (not board.attackers(me, move.from_square)
                        and bool(after.attackers(me, move.to_square)))

    # 4. Pièce adverse nouvellement attaquée par la pièce jouée -- la plus
    #    chère si plusieurs, c'est la seule qu'on citera.
    new_targets = []
    for sq in after_att & ~before_att:
        target = after.piece_at(sq)
        if target is not None and target.color != me:
            new_targets.append((PIECE_VALUES.get(target.piece_type, 0), sq))
    new_attack_square = max(new_targets)[1] if new_targets else None

    return {"new_squares": new_squares, "escapes_attack": escapes,
            "becomes_defended": becomes_defended,
            "new_attack_square": new_attack_square}
```

`PIECE_VALUES` existe déjà au niveau module de `move_intent.py` — vérifie-le
(`grep -n "PIECE_VALUES" move_intent.py`) et **ne le redéfinis pas**. Si le nom
diffère, utilise celui du fichier.

- [ ] **Step 5: injecter le contraste dans `_mk`**

Dans `detect_move_intent`, changer la signature :

```python
def detect_move_intent(board, chosen, why_motif=None, why_detail=None, prophylaxis=None):
```

et ajouter à sa docstring, après le paragraphe `why_motif / why_detail` :

```
    prophylaxis : dict {"san", "reason"} rendu par prophylaxis.prevented_by, ou
        None. Ce module ne parle JAMAIS au moteur -- le fait est calculé par
        l'appelant (web_bridge) et simplement reporté sur l'intent.
```

Puis, juste avant la définition de `_mk`, calculer une seule fois :

```python
    # Contraste géométrique : les mêmes quatre comptages quel que soit le kind
    # retenu ci-dessous -> calculés UNE fois et déversés par _mk, plutôt que
    # répétés dans les dix branches de retour.
    contrast = _geometric_contrast(board, move)
```

et étendre `_mk` pour qu'il les pose sur chaque intent :

```python
    def _mk(kind, forcing, delta, capture_undefended=False, capture_line_gain=False,
            move_tags=tags, file_status=None):
        return MoveIntent(
            kind=kind, forcing=forcing,
            from_square=move.from_square, to_square=move.to_square,
            moved_piece=moved_piece, captured_piece=captured_piece,
            material_delta=delta, gives_check=gives_check,
            capture_undefended=capture_undefended,
            capture_line_gain=capture_line_gain,
            file_status=file_status,
            why_motif=why_motif,
            tags=move_tags,
            prophylaxis=prophylaxis,
            **contrast,
        )
```

Attention : `detect_move_intent` a un **second** chemin de construction de
`MoveIntent`, en tête de fonction, pour le cas MATE (il n'utilise pas `_mk`).
Ajoute-y aussi `prophylaxis=prophylaxis` et `**contrast` **si** `contrast` est
déjà calculé à ce point ; sinon laisse ce chemin tel quel — un mat forcé n'a pas
besoin d'être expliqué par du contraste géométrique, et le seuil de la Task 4 le
supprimerait de toute façon. Choisis la seconde option et écris-le en commentaire
d'une ligne.

- [ ] **Step 6: lancer les tests, vérifier qu'ils passent**

```
python test_move_intent.py
```
Attendu : aucune assertion échouée. Si `test_contraste_fuite_devant_une_piece_moins_chere`
échoue, affiche la position (`python -c "import chess; print(chess.Board('...'))"`)
et corrige la FEN, pas l'assertion.

- [ ] **Step 7: commit**

```bash
git add move_intent.py test_move_intent.py
git commit -m "MoveIntent : contraste geometrique et report de la prophylaxie"
```

---

### Task 3 : le texte de la cause, à un seul endroit

**Files:**
- Modify: `fragment_library.py` (`fragments_for_intent`, + nouvelle `_explain_cause`)
- Modify: `test_fragment_library.py`

**Interfaces:**
- Consumes: les champs `MoveIntent` de la Task 2.
- Produces: `_explain_cause(intent, voice, ctx) -> Optional[str]` (clause,
  initiale minuscule, sans ponctuation finale) ; `FragmentContext.explain: bool`
  (défaut `True`) — quand `False`, aucune cause n'est ajoutée.

Pourquoi un seul endroit : les onze fonctions `_frag_*` produisent déjà un dict
via `_f(observation, plan, cause)`. Remplir la cause **dans** chacune, ce serait
onze diffs, onze occasions de se tromper d'accord, et onze textes à garder
cohérents. `fragments_for_intent` est le point de passage obligé de toutes :
c'est là que ça se pose, **sans écraser** une cause déjà fournie par le fragment
(`_frag_capture_free` pose son concept `why`, il est plus précis).

- [ ] **Step 1: écrire les tests qui échouent**

Ajouter à `test_fragment_library.py` :

```python
# --- Cause explicative ------------------------------------------------------

def _mk_intent(**kw):
    import move_intent as mi
    base = dict(kind=mi.DEVELOP, forcing=False, from_square=chess.F1,
                to_square=chess.C4, moved_piece=chess.BISHOP)
    base.update(kw)
    return mi.MoveIntent(**base)


def test_cause_prophylaxie_nomme_le_coup_empeche():
    it = _mk_intent(prophylaxis={"san": "Cxf2", "reason": "captured"})
    for voix in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(it, voix)
        assert frag["cause"], voix
        assert "Cxf2" in frag["cause"], (voix, frag["cause"])


def test_cause_contraste_nouvelle_attaque():
    it = _mk_intent(new_attack_square=chess.C6)
    frag = fragment_library.fragments_for_intent(it, "popular")
    assert frag["cause"] and "c6" in frag["cause"], frag["cause"]


def test_cause_absente_quand_explain_est_faux():
    ctx = fragment_library.FragmentContext()
    ctx.explain = False
    it = _mk_intent(prophylaxis={"san": "Cxf2", "reason": "captured"})
    frag = fragment_library.fragments_for_intent(it, "popular", ctx)
    assert not frag["cause"], frag["cause"]


def test_cause_absente_quand_aucun_fait():
    it = _mk_intent()
    frag = fragment_library.fragments_for_intent(it, "popular")
    assert not frag["cause"], frag["cause"]


def test_cause_nexerce_aucun_jugement():
    """Meme famille de garde que celle qui surveille 'mieux / meilleur / pire'."""
    import move_intent as mi
    interdits = ("mieux", "meilleur", "meilleure", "pire", "la plus faible",
                 "la plus forte", "excellent", "mauvais")
    cas = [
        _mk_intent(prophylaxis={"san": "Cxf2", "reason": "captured"}),
        _mk_intent(prophylaxis={"san": "d5", "reason": "blocked"}),
        _mk_intent(new_attack_square=chess.C6),
        _mk_intent(escapes_attack=True),
        _mk_intent(becomes_defended=True),
    ]
    for it in cas:
        for voix in ("popular", "creative", "classical"):
            cause = (fragment_library.fragments_for_intent(it, voix) or {}).get("cause") or ""
            bas = cause.lower()
            for mot in interdits:
                assert mot not in bas, (voix, cause, mot)


def test_cause_est_une_clause_pas_une_phrase():
    """Contrat de fragment : initiale minuscule, pas de point final -- le
    tissage compose 'observation -- cause' (narration_weaver._lead_sentence)."""
    cas = [_mk_intent(prophylaxis={"san": "Cxf2", "reason": "captured"}),
           _mk_intent(new_attack_square=chess.C6),
           _mk_intent(escapes_attack=True)]
    for it in cas:
        for voix in ("popular", "creative", "classical"):
            cause = (fragment_library.fragments_for_intent(it, voix) or {}).get("cause")
            if not cause:
                continue
            assert cause[0].islower(), cause
            assert not cause.endswith((".", "!", "?")), cause


def test_cause_du_fragment_nest_pas_ecrasee():
    """_frag_capture_free pose deja un concept 'why' plus precis que le
    contraste geometrique -- il doit gagner."""
    import move_intent as mi
    it = _mk_intent(kind=mi.CAPTURE_FREE, forcing=True, captured_piece=chess.KNIGHT,
                    why_motif="fork", new_attack_square=chess.C6)
    frag = fragment_library.fragments_for_intent(it, "popular")
    assert frag["cause"] and "c6" not in frag["cause"], frag["cause"]
```

Le fichier importe déjà `chess` et `fragment_library` — vérifie-le en tête, et
n'ajoute un import que s'il manque.

- [ ] **Step 2: lancer les tests, vérifier qu'ils échouent**

```
python test_fragment_library.py
```
Attendu : `AssertionError` sur `frag["cause"]` (valant `None` aujourd'hui).

- [ ] **Step 3: ajouter `explain` au contexte**

Dans `fragment_library.py`, dataclass `FragmentContext`, ajouter le champ et sa
ligne de docstring :

```
    explain     : le commentaire a-t-il le DROIT d'expliquer ? Posé par
                  narration_v2.render selon l'écart entre les deux meilleurs
                  coups (voir le seuil, Task 4) : quand le coup s'impose de
                  lui-même, une explication est du bruit. False -> aucune
                  cause n'est ajoutée.
```

```python
    explain: bool = True
```

- [ ] **Step 4: écrire `_explain_cause`**

À ajouter juste avant `def fragments_for_intent` :

```python
# Ordre de priorité des explications : la prophylaxie d'abord (ce que le coup
# ENLÈVE à l'adversaire -- le seul « pourquoi » profond d'un coup calme), puis
# le contraste géométrique (ce qu'il CHANGE -- moins profond, toujours vrai).
# On n'en rend JAMAIS deux : la cause est une apposition, pas un paragraphe.
def _explain_cause(intent, voice, ctx=None):
    """
    Clause « pourquoi ce coup », ou None. Initiale minuscule, sans ponctuation
    finale (le tissage compose « observation -- cause »).

    Chaque formulation ci-dessous est adossée à un fait CALCULÉ (voir
    MoveIntent : prophylaxis, new_attack_square, escapes_attack,
    becomes_defended). Aucun jugement comparatif : on dit ce qui est compté,
    jamais que c'est bien.
    """
    if ctx is not None and not getattr(ctx, "explain", True):
        return None
    if intent is None:
        return None

    proph = getattr(intent, "prophylaxis", None)
    if proph and proph.get("san"):
        san = proph["san"]
        if proph.get("reason") == "captured":
            if voice == CREATIVE:
                return f"la pièce qui préparait {san} n'est plus là"
            if voice == CLASSICAL:
                return f"le coup supprime la pièce qui rendait {san} possible"
            return f"ça enlève {san} à l'adversaire"
        if voice == CREATIVE:
            return f"{san} ne passe plus"
        if voice == CLASSICAL:
            return f"{san} n'est plus jouable après ce coup"
        return f"ça empêche {san}"

    target = getattr(intent, "new_attack_square", None)
    if target is not None:
        case = _sq(target)
        if voice == CREATIVE:
            return f"la pièce en {case} se retrouve sous le feu"
        if voice == CLASSICAL:
            return f"le coup crée une attaque sur {case}"
        return f"ça attaque la pièce en {case}"

    if getattr(intent, "escapes_attack", False):
        if voice == CREATIVE:
            return "la pièce sort de la ligne de tir"
        if voice == CLASSICAL:
            return "la pièce quitte l'attaque qu'elle subissait"
        return "ça sort la pièce de l'attaque"

    if getattr(intent, "becomes_defended", False):
        if voice == CREATIVE:
            return "elle arrive sur une case où elle est soutenue"
        if voice == CLASSICAL:
            return "la pièce arrive défendue, ce qu'elle n'était pas"
        return "et là, elle est défendue"

    return None
```

`_sq` et les constantes `CREATIVE` / `CLASSICAL` existent déjà dans le fichier —
vérifie leurs noms exacts avant d'écrire (`grep -n "^CREATIVE\|^CLASSICAL\|def _sq" fragment_library.py`).

Note sur `new_squares` : il est calculé (Task 2) mais **volontairement pas
narré**. « Le fou contrôle 7 cases de plus » est un comptage vrai et parfaitement
creux ; le champ reste disponible pour un futur seuil ou une future formulation,
il n'a pas sa place dans une phrase aujourd'hui. Écris cette phrase en
commentaire au-dessus de `_explain_cause`.

- [ ] **Step 5: brancher dans `fragments_for_intent`**

Remplacer la dernière ligne de `fragments_for_intent` :

```python
    return fn(intent, voice, ctx)
```

par :

```python
    frag = fn(intent, voice, ctx)
    # La cause du fragment gagne si elle existe (_frag_capture_free pose le
    # concept why détecté, plus précis qu'un comptage géométrique). Sinon on
    # remplit ici, à l'UNIQUE point de passage des onze fragments d'intention,
    # plutôt que d'éparpiller la même logique dans chacun.
    if frag is not None and not frag.get("cause"):
        frag["cause"] = _explain_cause(intent, voice, ctx)
    return frag
```

- [ ] **Step 6: lancer les tests, vérifier qu'ils passent**

```
python test_fragment_library.py
```
Attendu : tous les tests du fichier passent, **y compris** les gardes d'accents,
d'accord de genre et de contraction déjà présentes (elles balayent les textes du
module ; tes nouvelles formulations doivent y survivre).

- [ ] **Step 7: commit**

```bash
git add fragment_library.py test_fragment_library.py
git commit -m "Fragments : remplit la cause a partir de la prophylaxie et du contraste"
```

---

### Task 4 : le seuil

**Files:**
- Modify: `narration_v2.py` (`Selection`, `build_selection`, `render`)
- Modify: `test_narration_v2.py`

**Interfaces:**
- Consumes: `FragmentContext.explain` (Task 3), champ `prophylaxis` de
  `detect_move_intent` (Task 2).
- Produces:
  - `Selection.gap_cp: int = 0`
  - `EXPLAIN_GAP_MAX_CP = 60` (constante module, commentée comme *à calibrer*)
  - `render(..., prophylaxis=None)` — nouveau paramètre nommé, défaut `None`.

- [ ] **Step 1: écrire les tests qui échouent**

Ajouter à `test_narration_v2.py` (le fichier a déjà un helper
`make_candidates(cp, second_eval_loss=..., ...)` — réutilise-le) :

```python
# --- Seuil d'explication ----------------------------------------------------
# La cause n'est rendue que quand le coup NE S'IMPOSE PAS de lui-meme. C'est ce
# qui distingue un commentaire explicatif d'un bavardage : sans ce garde-fou,
# une explication s'ajoute a chaque coup et le texte redevient du bruit.

def _render_avec_ecart(second_loss, prophylaxis=None):
    import chess
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1")
    move = board.parse_san("Bc4")
    chosen = {"move_uci": move.uci(), "move_san": "Bc4", "cp": 30, "eval_loss": 0,
              "pv_uci": [move.uci()]}
    cands = [chosen, {"move_uci": "b1c3", "move_san": "Nc3", "cp": 30 - second_loss,
                      "eval_loss": second_loss, "pv_uci": ["b1c3"],
                      "is_check": False, "is_capture": False}]
    sel = narration_v2.build_selection(board, cands)
    return narration_v2.render(sel, "popular", chosen=chosen, board=board,
                               prophylaxis=prophylaxis)


def test_seuil_ecart_faible_explique():
    proph = {"san": "d5", "reason": "blocked"}
    out = _render_avec_ecart(10, prophylaxis=proph)
    assert "d5" in out["text"], out["text"]


def test_seuil_ecart_large_nexplique_pas():
    proph = {"san": "d5", "reason": "blocked"}
    out = _render_avec_ecart(400, prophylaxis=proph)
    assert "d5" not in out["text"], out["text"]
    assert out["text"], "le paragraphe doit rester complet, seule la cause tombe"


def test_gap_cp_lu_sur_le_second_candidat():
    import chess
    board = chess.Board()
    cands = [{"move_uci": "e2e4", "move_san": "e4", "cp": 30, "eval_loss": 0},
             {"move_uci": "d2d4", "move_san": "d4", "cp": -20, "eval_loss": 50}]
    assert narration_v2.build_selection(board, cands).gap_cp == 50


def test_gap_cp_vaut_zero_sans_second_candidat():
    import chess
    cands = [{"move_uci": "e2e4", "move_san": "e4", "cp": 30, "eval_loss": 0}]
    assert narration_v2.build_selection(chess.Board(), cands).gap_cp == 0
```

- [ ] **Step 2: lancer les tests, vérifier qu'ils échouent**

```
python test_narration_v2.py
```
Attendu : `AttributeError: 'Selection' object has no attribute 'gap_cp'`.

- [ ] **Step 3: ajouter `gap_cp` et la constante**

Dans `narration_v2.py`, au niveau module (près des autres constantes) :

```python
# Écart, en centipawns, au-delà duquel le coup s'impose tout seul et n'a pas
# besoin d'être expliqué (voir Selection.gap_cp). VALEUR À CALIBRER en
# pratique -- volontairement une constante nommée, réglable sans toucher à la
# logique. 60cp ≈ « plus d'un demi-pion d'écart avec le 2e coup » : à ce
# niveau-là le choix ne se discute plus, et une explication devient du
# remplissage.
EXPLAIN_GAP_MAX_CP = 60
```

Dans la docstring de `Selection`, après `eval_cp` :

```
    gap_cp   : perte d'éval du DEUXIÈME meilleur coup par rapport au premier
               (candidates[1]["eval_loss"]) -- mesure à quel point le meilleur
               coup se détache. 0 si un seul candidat. Profil-indépendant.
```

et le champ, après `eval_cp` :

```python
    gap_cp: int = 0
```

Dans `build_selection`, juste après le calcul de `eval_cp` :

```python
    gap_cp = 0
    if len(candidates) > 1:
        gap_cp = candidates[1].get("eval_loss") or 0
```

et dans le `return` :

```python
    return Selection(lead=lead, supports=supports, eval_cp=eval_cp, gap_cp=gap_cp,
                     bricks=bricks)
```

- [ ] **Step 4: appliquer le seuil dans `render`**

Signature :

```python
def render(selection, profile_id, chosen=None, why_motif=None, why_detail=None,
           board=None, caution_text=None, recent_kinds=None, prophylaxis=None):
```

Ajouter à la docstring, après `recent_kinds` :

```
    prophylaxis : dict {"san", "reason"} rendu par prophylaxis.prevented_by
        (calculé par l'appelant, un appel moteur par POSITION -- voir
        web_bridge). None = pas de fait prophylactique pour ce coup.
```

Après la construction de `ctx` (`ctx.recent_kinds = ...`) :

```python
    # Le seuil : la cause ne se rend QUE si le coup ne s'impose pas de
    # lui-même. Garde-fou contre l'effet cumulé des deux sources
    # d'explication -- sans lui, une cause s'ajoute à chaque coup, et l'audit
    # multi-scénarios a déjà montré qu'un texte qui revient devient du bruit.
    ctx.explain = selection.gap_cp <= EXPLAIN_GAP_MAX_CP
```

et passer la prophylaxie à la détection :

```python
            intent = mi.detect_move_intent(board, chosen, why_motif, why_detail,
                                           prophylaxis=prophylaxis)
```

- [ ] **Step 5: lancer les tests, vérifier qu'ils passent**

```
python test_narration_v2.py
python test_fragment_library.py
python test_move_intent.py
```
Attendu : tous verts. Les deux derniers ne doivent pas régresser.

- [ ] **Step 6: commit**

```bash
git add narration_v2.py test_narration_v2.py
git commit -m "Narration : seuil d'explication sur l'ecart avec le 2e coup"
```

---

### Task 5 : câblage dans `web_bridge`

**Files:**
- Modify: `web_bridge.py` (`BridgeState.__init__`, `handle_single_profile`)

**Interfaces:**
- Consumes: `prophylaxis.opponent_threat` / `prevented_by` (Task 1),
  `narration_v2.render(..., prophylaxis=...)` (Task 4).
- Produces: rien pour d'autres tasks.

Point clé : le coup menaçant ne dépend **que de l'échiquier**, jamais du profil.
Un seul appel moteur par position, mis en cache par FEN comme `_theme_cache` et
`_selection_cache` juste à côté. La vérification « la menace est-elle morte ? »,
elle, dépend du coup choisi — mais elle est du python-chess pur, donc gratuite,
et se refait par profil.

- [ ] **Step 1: ajouter l'import et le cache**

En tête de `web_bridge.py`, à côté des autres imports du projet :

```python
import prophylaxis
```

Dans `BridgeState.__init__`, juste après `self._scenario_cache = {}` :

```python
        # Menace adverse (coup nul + 1 analyse profondeur 10, ~87 ms) pour LA
        # POSITION COURANTE -- clé = fen, valeur = chess.Move ou None. Ne
        # dépend PAS du profil (c'est l'échiquier qui menace, pas le coup
        # qu'on choisit) : un seul appel moteur par position, partagé par les
        # 3 profils. Tourne sur scenario_engine (moteur dédié, 1 thread) pour
        # ne jamais retarder la flèche principale.
        self._threat_cache_key = None
        self._threat_cache_value = None
```

- [ ] **Step 2: écrire la méthode de calcul**

Ajouter à `BridgeState`, juste avant `_attach_scenario_async` :

```python
    def _opponent_threat(self, fen, board):
        """
        Coup que l'adversaire jouerait s'il avait le trait (voir
        prophylaxis.opponent_threat), mis en cache pour CETTE position.

        Best-effort intégral : toute défaillance rend None, ce qui retire
        seulement l'explication prophylactique du commentaire -- jamais le
        commentaire lui-même. L'exception est journalisée, pas avalée (un
        `except` muet a déjà allongé un diagnostic de plusieurs heures dans ce
        projet).
        """
        with self.lock:
            if self._threat_cache_key == fen:
                return self._threat_cache_value
        threat = None
        try:
            with self._timed_engine_lock("threat", lock=self.scenario_engine_lock):
                threat = prophylaxis.opponent_threat(self.scenario_engine, board)
        except Exception as e:
            print(f"⚠ Menace adverse indisponible : {e}")
        with self.lock:
            self._threat_cache_key = fen
            self._threat_cache_value = threat
        return threat
```

Vérifie la signature réelle de `_timed_engine_lock` avant d'écrire cet appel
(`grep -n "def _timed_engine_lock" web_bridge.py`) et adapte-toi à elle ;
`_attach_scenario_async` en donne un exemple d'usage correct.

- [ ] **Step 3: brancher sur le rendu**

Dans `handle_single_profile`, dans le bloc `try:` qui appelle
`narration_v2.render` (autour de `web_bridge.py:1290`), juste avant l'appel :

```python
                # Prophylaxie : ce que le coup EMPÊCHE. La menace est calculée
                # une fois par position (cache), la preuve qu'elle disparaît est
                # du python-chess pur et se refait par profil, puisqu'elle
                # dépend du coup choisi.
                proph = None
                try:
                    threat = self._opponent_threat(fen, board)
                    if threat is not None:
                        proph = prophylaxis.prevented_by(
                            board, chess.Move.from_uci(chosen["move_uci"]), threat)
                except Exception as e:
                    print(f"⚠ Prophylaxie indisponible ({profile_id}) : {e}")
```

et ajouter l'argument :

```python
                woven = narration_v2.render(
                    selection, profile_id, chosen=chosen,
                    why_motif=why_motif, why_detail=why_detail, board=board,
                    recent_kinds=self._recent_intent_kinds.get(profile_id, ()),
                    prophylaxis=proph,
                )
```

- [ ] **Step 4: vérifier que rien n'est cassé**

Aucun test unitaire ne couvre `handle_single_profile`. La vérification est un
import et une inspection statique :

```
python -c "import web_bridge; print('import ok')"
python -m py_compile web_bridge.py && echo compile ok
python test_web_bridge.py
```
Attendu : les trois passent.

- [ ] **Step 5: commit**

```bash
git add web_bridge.py
git commit -m "Cable la prophylaxie : une analyse par position, cachee par FEN"
```

---

### Task 6 : vérification de bout en bout

**Files:**
- Create: `audit_causes.py`

**Interfaces:**
- Consumes: tout le pipeline.
- Produces: un rapport imprimé, pas d'API.

- [ ] **Step 1: écrire le script d'audit**

```python
"""
audit_causes.py
Les commentaires expliquent-ils, et disent-ils vrai ?

Rejoue une vraie partie dans le pipeline de PRODUCTION (analyse -> selection par
profil -> prophylaxie -> narration) et imprime, pour chaque coup blanc : le coup,
l'ecart avec le 2e candidat, le fait prophylactique detecte, et le paragraphe.

Trois choses a lire dans la sortie :
  1. les causes apparaissent-elles sur les coups a CHOIX SERRE (ecart faible) ?
  2. restent-elles absentes sur les coups EVIDENTS (ecart large) ?
  3. chaque cause est-elle VRAIE ? (le SAN cite doit etre un coup adverse
     reellement plus disponible -- verifie ici independamment)
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import chess

import engine_analysis
import human_profile
import narration_v2
import prophylaxis
import why_detector

ENGINE = r"C:\Users\Triv\Desktop\Vivado\test\Coach.EXE\CoachEchecs\stockfish.exe"
GAME = """e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5
Nxb5 cxb5 Bxb5 Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6""".split()


def main():
    eng = engine_analysis.ChessCoachEngine(ENGINE, threads=2, hash_mb=256)
    board = chess.Board()
    faux = []
    try:
        for san in GAME:
            if board.turn == chess.WHITE:
                res, _ = eng.analyze_candidates(board.fen(), multipv=4, depth=14)
                cands = res["candidates"]
                chosen = human_profile.select_move(cands, 2, "popular", board=board)
                if chosen:
                    threat = prophylaxis.opponent_threat(eng, board)
                    move = chess.Move.from_uci(chosen["move_uci"])
                    proph = prophylaxis.prevented_by(board, move, threat)
                    sel = narration_v2.build_selection(board, cands)
                    wm, wd = why_detector.detect_why(board, chosen)
                    out = narration_v2.render(sel, "popular", chosen=chosen,
                                              why_motif=wm, why_detail=wd,
                                              board=board, prophylaxis=proph)
                    print(f"\n--- {board.fullmove_number}. {chosen['move_san']}"
                          f"   ecart={sel.gap_cp}cp  explique={sel.gap_cp <= narration_v2.EXPLAIN_GAP_MAX_CP}")
                    print(f"    menace adverse : {threat}   prophylaxie : {proph}")
                    print(f"    {out['text']}")

                    # Verification INDEPENDANTE du fait affiche : si le texte
                    # cite un SAN, ce coup doit vraiment etre devenu illegal.
                    if proph:
                        after = board.copy()
                        after.push(move)
                        after.push(chess.Move.null())
                        if any(after.san(m) == proph["san"] for m in after.legal_moves):
                            faux.append(f"{chosen['move_san']} : {proph['san']} est encore legal")
            board.push_san(san)
    finally:
        eng.engine.quit()

    print("\n===== FAITS FAUX =====")
    print("aucun" if not faux else "\n".join(" - " + f for f in faux))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: lancer l'audit**

```
cd C:\Users\Triv\Desktop\Vivado\test\Chess-main
PYTHONIOENCODING=utf-8 python audit_causes.py
```

- [ ] **Step 3: lire la sortie et rapporter**

Ne « corrige » rien à l'aveugle. Rapporte factuellement :
- combien de coups sur 14 portent une cause ;
- la section `FAITS FAUX` (elle doit dire `aucun` — si non, **c'est un bug à
  corriger avant de commiter**, pas une observation à noter) ;
- si les causes tombent sur les coups évidents et apparaissent sur les serrés —
  c'est ce qui dit si `EXPLAIN_GAP_MAX_CP = 60` est bien calibré. Si toutes les
  causes tombent ou aucune, propose une autre valeur **avec les chiffres
  d'écart observés à l'appui**, ne la devine pas.

- [ ] **Step 4: relancer l'audit de non-régression**

```
PYTHONIOENCODING=utf-8 python audit_narration.py
PYTHONIOENCODING=utf-8 python audit_scenarios.py
PYTHONIOENCODING=utf-8 python audit_mats.py
```
Attendu : `audit_mats.py` doit toujours afficher `0 problème(s)`. Les deux autres
n'ont pas de verdict automatique — vérifie qu'aucun paragraphe n'est vide et
qu'aucune cause n'apparaît sur un mat forcé.

- [ ] **Step 5: commit**

```bash
git add audit_causes.py
git commit -m "Audit : verifie que les causes apparaissent au bon moment et disent vrai"
```

---

## Self-review

**Couverture de la spec :**

| Exigence de la spec | Task |
|---|---|
| A. Prophylaxie, menace par coup nul, depth 10-12, multipv=1 | 1 |
| A. Garde `is_check()` non négociable | 1 (test dédié) |
| A. Deux causes nommables : pièce capturée / coup devenu illégal | 1 (`reason`) |
| A. Ne jamais dire « M est devenu moins bon » | 1 (seul `not in legal_moves` compte) |
| A. Ne bloque jamais la flèche | 5 (moteur dédié, cache par FEN) — **avec l'écart documenté plus haut** |
| B. 4 faits comptables sur `MoveIntent` | 2 |
| B. Portés en fin de dataclass comme `capture_undefended` | 2 |
| C. Seuil sur `candidates[1]["eval_loss"]` | 4 |
| C. Constante nommée, réglable, à calibrer | 4 (`EXPLAIN_GAP_MAX_CP`) + 6 (calibration mesurée) |
| Erreur best-effort MAIS journalisée | 1, 5 |
| Tests prophylaxie (3 cas de la spec) | 1 |
| Tests contraste (1 par fait) | 2 |
| Tests seuil (2 rendus) | 4 |
| Garde « rien d'inventé » | 3 (`test_cause_nexerce_aucun_jugement`) |
| Vérification de bout en bout par les audits | 6 |
| Hors périmètre : `variation_narrator` | traité à part, commit `119a5e1` |
| Hors périmètre : concepts de finale | non traité, reste documenté |

**Cohérence des types :** `prevented_by` rend `{"san", "reason"}` (Task 1) →
`MoveIntent.prophylaxis` (Task 2) → `_explain_cause` lit `.get("san")` /
`.get("reason")` (Task 3) → `render(prophylaxis=...)` (Task 4) → `web_bridge`
(Task 5). `Selection.gap_cp` (Task 4) est lu par `render` et par `audit_causes`
(Task 6). `FragmentContext.explain` est écrit en Task 4, lu en Task 3.

**Le fait qui reste à ne PAS narrer :** `new_squares` est calculé et jamais
affiché — c'est délibéré et écrit dans le code (Task 3, step 4).
