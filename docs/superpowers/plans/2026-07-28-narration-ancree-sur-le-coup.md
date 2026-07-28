# Narration ancrée sur le coup — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire que le paragraphe de commentaire parle TOUJOURS du coup montré par la flèche, au lieu de décrire la position indépendamment de lui.

**Architecture:** `move_intent.detect_move_intent` gagne une intention `MATE`, un champ `tags` (faits secondaires du même coup) et quatre intentions calmes. `fragment_library` gagne les fragments correspondants. `narration_v2.render` perd sa branche `if intent.forcing` : l'intention pilote toujours le paragraphe, le thème de position n'entre qu'en appui et seulement s'il est géométriquement cohérent avec le coup.

**Tech Stack:** Python 3.11, python-chess, aucune dépendance nouvelle. Tests = scripts `test_*.py` maison, sans framework (voir `test_move_intent.py` existant).

## Global Constraints

- Spec de référence : `docs/superpowers/specs/2026-07-28-narration-ancree-sur-le-coup-design.md`.
- Aucun fait inventé : tout ce qu'un fragment affirme doit être calculé depuis la position ou l'intent. C'est la règle centrale du projet.
- Contrat de fragment (voir en-tête de `fragment_library.py`) : chaque valeur est une CLAUSE — minuscule initiale (sauf notation SAN), SANS ponctuation finale.
- Trois voix obligatoires pour chaque fragment : `popular`, `creative`, `classical` (constantes `POPULAR`/`CREATIVE`/`CLASSICAL` dans `fragment_library.py`).
- Aucun appel moteur dans `move_intent`, `fragment_library`, `narration_weaver`, `narration_v2` : python-chess pur.
- Les tests se lancent `python test_xxx.py` depuis la racine du dépôt et impriment `test_xxx : OK` ou sortent en code 1.
- Ne pas toucher `narration.py` (narration v1), conservée en repli.

---

### Task 1 : Accord du nom de pièce et cohérence du mot employé

Corrige les fautes constatées à l'audit : « le tour adverse » (accord) et « le **pion** tombe… prends la **pièce** » dans la même phrase.

**Files:**
- Modify: `fragment_library.py` (helper près de `_piece_type_name`, ligne 611 ; fragments `_frag_capture_free` ligne 645, `_frag_capture_trade` ligne 752)
- Test: `test_fragment_library.py`

**Interfaces:**
- Produces: `fragment_library._piece_with_article(piece_type, definite=True) -> str` — rend `"la tour"`, `"le cavalier"`, `"la dame"`, `"le pion"`. `definite=False` rend `"une tour"` / `"un cavalier"`.

- [ ] **Step 1: Write the failing test**

Ajouter dans `test_fragment_library.py`, et l'appeler depuis `main()` :

```python
def test_piece_article_accord():
    import chess
    check(fragment_library._piece_with_article(chess.ROOK) == "la tour",
          "tour est FEMININ : 'la tour', jamais 'le tour'")
    check(fragment_library._piece_with_article(chess.QUEEN) == "la dame",
          "dame est FEMININ")
    check(fragment_library._piece_with_article(chess.KNIGHT) == "le cavalier",
          "cavalier est masculin")
    check(fragment_library._piece_with_article(chess.ROOK, definite=False) == "une tour",
          "article indefini feminin")
    check(fragment_library._piece_with_article(None) == "la piece"
          or fragment_library._piece_with_article(None) == "la pièce",
          "type inconnu -> repli feminin coherent avec 'piece'")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_fragment_library.py`
Expected: FAIL — `module 'fragment_library' has no attribute '_piece_with_article'`

- [ ] **Step 3: Write minimal implementation**

Dans `fragment_library.py`, juste après `_piece_type_name` (ligne 611) :

```python
# Genre grammatical des noms de pièces -- "tour" et "dame" sont FÉMININS.
# Les fragments écrivaient "le {nom}" en dur, d'où "le tour adverse" observé
# en pratique. Le repli "pièce" est féminin lui aussi, donc cohérent.
_PIECE_IS_FEMININE = {chess.ROOK, chess.QUEEN}


def _piece_with_article(piece_type, definite=True):
    """
    Nom FR d'un type de pièce précédé de son article accordé :
    "la tour", "le cavalier", "une dame", "un fou". None/inconnu -> "la pièce".
    """
    name = _piece_type_name(piece_type)
    feminine = piece_type in _PIECE_IS_FEMININE or name == "pièce"
    if definite:
        return f"{'la' if feminine else 'le'} {name}"
    return f"{'une' if feminine else 'un'} {name}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_fragment_library.py`
Expected: PASS — `test_fragment_library : OK`

- [ ] **Step 5: Utiliser le helper dans les fragments de prise**

Dans `_frag_capture_free` (ligne 645), remplacer les constructions `f"le {prise}"` / `f"le {par}"` par le helper, et faire suivre le PLAN du mot réellement employé. Le corps devient :

```python
def _frag_capture_free(intent, voice, ctx):
    # Prise NETTE : soit la pièce ne peut pas être reprise, soit l'échange
    # laisse un gain net (voir move_intent._capture_is_free). Le texte reste
    # donc vrai dans les DEUX cas -- on n'affirme pas "non défendue", ce qui
    # serait faux pour une prise défendue mais gagnante à l'échange.
    dest = _sq(intent.to_square)
    prise = _piece_with_article(intent.captured_piece)
    par = _piece_with_article(intent.moved_piece)
    prise_nu = _piece_type_name(intent.captured_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        obs = f"{prise} adverse {where} tombe sans compensation".replace("  ", " ").rstrip()
        plan = f"prends {'ce' if prise_nu != 'tour' and prise_nu != 'dame' else 'cette'} {prise_nu}, puis enchaîne pendant que tu tiens l'avantage matériel"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{par} capture {prise} {where} avec un gain net de matériel".replace("  ", " ")
        plan = "encaisse le matériel, puis convertis proprement l'avantage"
        return _f(obs, plan, None)
    # popular
    obs = f"{prise} adverse {where} tombe sans reprise à ta hauteur".replace("  ", " ")
    plan = f"prends {prise_nu}, c'est du matériel gagné".replace("prends pion", "prends le pion")
    return _f(obs, plan, None)
```

Simplifier ensuite : remplacer les deux expressions ternaires ci-dessus par un
seul helper démonstratif ajouté sous `_piece_with_article` :

```python
def _piece_demonstrative(piece_type):
    """"ce cavalier" / "cette tour" -- accord identique à _piece_with_article."""
    name = _piece_type_name(piece_type)
    feminine = piece_type in _PIECE_IS_FEMININE or name == "pièce"
    return f"{'cette' if feminine else 'ce'} {name}"
```

et écrire dans les deux voix concernées :

```python
    plan = f"prends {_piece_demonstrative(intent.captured_piece)}, c'est du matériel gagné"
```

Appliquer le même remplacement dans `_frag_capture_trade` (ligne 752) partout où
un nom de pièce est précédé d'un article écrit en dur.

- [ ] **Step 6: Ajouter le test de non-régression sur la phrase complète**

```python
def test_capture_free_accorde_et_coherent():
    import chess, move_intent
    # Tour noire en d7 non défendue, tour blanche en d1 la prend.
    board = chess.Board("3r3k/8/8/8/8/8/8/3RK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d7", "pv_uci": ["d1d7"]})
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        blob = " ".join(v for v in frag.values() if v)
        check("le tour" not in blob, f"[{voice}] accord : jamais 'le tour'")
        check("pièce" not in blob or "tour" in blob,
              f"[{voice}] on nomme la piece reellement prise, pas 'piece'")
```

- [ ] **Step 7: N'affirmer « sans reprise » que si c'est prouvé**

`_capture_is_free` (`move_intent.py:58`) conclut par TROIS chemins : case non
défendue (preuve directe), bilan de ligne positif, ou motif `why` d'appoint.
Seul le premier autorise à écrire « tombe sans reprise » — les deux autres
couvrent aussi une prise défendue mais gagnante à l'échange, où la phrase est
factuellement fausse (observé sur `Rxd7` et `Bxd7+`, tous deux repris).

Exposer la preuve sur l'intent. Dans `move_intent.py`, ajouter un champ à
`MoveIntent` — en DERNIÈRE position parmi les champs à valeur par défaut (le
champ `tags` de la tâche 3 n'existe pas encore à ce stade) :

```python
    capture_undefended: bool = False   # la case d'arrivée n'a AUCUN défenseur adverse
```

le documenter :

```
    capture_undefended : preuve DIRECTE que la pièce prise ne peut pas être
                  reprise (aucun défenseur sur la case). Distinct de "la prise
                  est gagnante" : un échange favorable sur une case défendue
                  est gagnant SANS être imprenable. Seul ce booléen autorise un
                  fragment à écrire "sans reprise".
```

et le renseigner à la construction de l'intent `CAPTURE_FREE` :

```python
            capture_undefended=not board.attackers(not board.turn, move.to_square),
```

Puis, dans `_frag_capture_free`, réserver la formulation « sans reprise » à ce
cas et prévoir un repli honnête :

```python
    # popular
    if intent.capture_undefended:
        obs = f"{prise} adverse {where} n'est pas défendu".replace("  ", " ")
    else:
        obs = f"l'échange {where} tourne à ton avantage".replace("  ", " ")
```

- [ ] **Step 8: Tester l'affirmation**

```python
def test_sans_reprise_seulement_si_case_non_defendue():
    import chess, move_intent
    # Tour en d7 DEFENDUE par le roi e8 : la prise reste gagnante mais la
    # piece EST reprenable -> interdit d'ecrire "sans reprise".
    board = chess.Board("4k3/3r4/8/8/8/8/8/3QK3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d7", "pv_uci": ["d1d7", "e8d7"]})
    if intent.kind == move_intent.CAPTURE_FREE:
        check(intent.capture_undefended is False,
              "case defendue par le roi : capture_undefended doit etre False")
        for voice in ("popular", "creative", "classical"):
            frag = fragment_library.fragments_for_intent(intent, voice)
            blob = " ".join(v for v in frag.values() if v)
            check("sans reprise" not in blob,
                  f"[{voice}] 'sans reprise' est faux ici : {blob!r}")
```

- [ ] **Step 9: Run tests**

Run: `python test_fragment_library.py` puis `python test_move_intent.py`
Expected: les deux PASS

- [ ] **Step 10: Commit**

```bash
git add fragment_library.py move_intent.py test_fragment_library.py test_move_intent.py
git commit -m "Accorde le nom de piece et n'affirme 'sans reprise' que si c'est prouve"
```

---

### Task 2 : Intention MATE

À l'audit, `Rd8#` (mat) sortait « l'adversaire doit réagir tout de suite ». Aucune intention `MATE` n'existe.

**Files:**
- Modify: `move_intent.py` (constantes ligne 36-45, `detect_move_intent` ligne 216)
- Modify: `fragment_library.py` (`_INTENT_FUNCS` ligne 772)
- Test: `test_move_intent.py`, `test_fragment_library.py`

**Interfaces:**
- Consumes: `fragment_library._piece_with_article` (Task 1)
- Produces: constante `move_intent.MATE = "mate"`, incluse dans `FORCING_KINDS` ; `fragment_library._frag_mate(intent, voice, ctx)`

- [ ] **Step 1: Write the failing test**

Dans `test_move_intent.py` :

```python
def test_mate_prime_sur_tout():
    # Mat du couloir : Td8# -- c'est AUSSI un echec, la priorite doit
    # neanmoins ressortir MATE, jamais gives_check.
    board = chess.Board("6k1/5ppp/8/8/8/8/8/3R2K1 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d8", "pv_uci": ["d1d8"]})
    check(intent is not None, "un coup de mat doit produire un intent")
    check(intent.kind == move_intent.MATE,
          f"Td8# doit etre MATE, obtenu {intent.kind}")
    check(intent.forcing is True, "le mat est forcement forcant")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_move_intent.py`
Expected: FAIL — `module 'move_intent' has no attribute 'MATE'`

- [ ] **Step 3: Write minimal implementation**

Dans `move_intent.py`, ajouter la constante au bloc des forçantes (ligne 36) :

```python
MATE = "mate"                    # le coup fait MAT -- prime sur tout le reste
```

et l'inclure dans le frozenset :

```python
FORCING_KINDS = frozenset({MATE, CHECK_ESCAPE, CAPTURE_FREE, SACRIFICE,
                           GIVES_CHECK, PROMOTION, CAPTURE_TRADE})
```

Dans `detect_move_intent`, insérer le test EN PREMIER, avant la détection
`CHECK_ESCAPE`, juste après avoir construit l'objet `chess.Move` :

```python
    # Priorité 0 : le coup fait MAT. Rien d'autre ne mérite d'être raconté --
    # observé en pratique, un mat sortait sous "gives_check" ("l'adversaire
    # doit réagir tout de suite", alors qu'il ne peut plus rien).
    board.push(move)
    is_mate = board.is_checkmate()
    board.pop()
    if is_mate:
        return MoveIntent(
            kind=MATE, forcing=True,
            from_square=move.from_square, to_square=move.to_square,
            moved_piece=board.piece_type_at(move.from_square),
            captured_piece=(board.piece_at(move.to_square).piece_type
                            if board.piece_at(move.to_square) else None),
            material_delta=_immediate_material_delta(board, move),
            gives_check=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_move_intent.py`
Expected: PASS

- [ ] **Step 5: Écrire le fragment MATE**

Dans `fragment_library.py`, avant `_INTENT_FUNCS` :

```python
def _frag_mate(intent, voice, ctx):
    # Le coup fait MAT. Aucune nuance à apporter : c'est la fin de la partie,
    # le seul message utile est "joue-le".
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        obs = f"{par} {where} met le roi adverse échec et mat".replace("  ", " ")
        plan = "c'est fini, joue-le"
        return _f(obs, plan, None)
    if voice == CLASSICAL:
        obs = f"{par} {where} donne mat, la partie s'arrête ici".replace("  ", " ")
        plan = "joue ce coup, aucune autre considération n'a de valeur"
        return _f(obs, plan, None)
    # popular
    obs = f"{par} {where} fait mat".replace("  ", " ")
    plan = "joue-le, la partie est gagnée"
    return _f(obs, plan, None)
```

et l'enregistrer :

```python
_INTENT_FUNCS = {
    "mate": _frag_mate,
    "check_escape": _frag_check_escape,
    ...
}
```

- [ ] **Step 6: Tester le fragment**

Dans `test_fragment_library.py` :

```python
def test_fragment_mate_dans_les_3_voix():
    import chess, move_intent
    board = chess.Board("6k1/5ppp/8/8/8/8/8/3R2K1 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "d1d8", "pv_uci": ["d1d8"]})
    for voice in ("popular", "creative", "classical"):
        frag = fragment_library.fragments_for_intent(intent, voice)
        check(frag is not None, f"[{voice}] MATE doit avoir un fragment")
        blob = " ".join(v for v in frag.values() if v).lower()
        check("mat" in blob, f"[{voice}] le texte doit dire que c'est mat")
```

- [ ] **Step 7: Run tests**

Run: `python test_move_intent.py` puis `python test_fragment_library.py`
Expected: les deux PASS

- [ ] **Step 8: Commit**

```bash
git add move_intent.py fragment_library.py test_move_intent.py test_fragment_library.py
git commit -m "Ajoute l'intention MATE : un mat n'est plus raconte comme un echec ordinaire"
```

---

### Task 3 : Champ `tags` — l'échec d'une prise n'est plus perdu

À l'audit, `Bxb5+` donnait échec ; le texte n'en disait rien, `CAPTURE_FREE` primant sur `GIVES_CHECK` et un seul fragment étant rendu.

**Files:**
- Modify: `move_intent.py` (dataclass `MoveIntent` ligne 83, `detect_move_intent`)
- Modify: `narration_weaver.py` (`weave_intent` ligne 262)
- Test: `test_move_intent.py`

**Interfaces:**
- Produces: `MoveIntent.tags: frozenset` — vide par défaut, contient `"gives_check"` quand le coup donne échec sans être classé `GIVES_CHECK` ni `MATE`.

- [ ] **Step 1: Write the failing test**

```python
def test_prise_qui_donne_echec_porte_le_tag():
    # Fou blanc prend en b5 AVEC echec (roi noir en e8, diagonale a4-e8).
    board = chess.Board("4k3/8/8/1p6/8/8/8/4K2B w - - 0 1")
    board.set_piece_at(chess.A4, chess.Piece(chess.BISHOP, chess.WHITE))
    board.remove_piece_at(chess.H1)
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "a4b5", "pv_uci": ["a4b5"]})
    check(intent.kind == move_intent.CAPTURE_FREE,
          f"reste classe comme une prise, obtenu {intent.kind}")
    check("gives_check" in intent.tags,
          "l'echec ne doit pas etre perdu : tag gives_check attendu")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_move_intent.py`
Expected: FAIL — `'MoveIntent' object has no attribute 'tags'`

- [ ] **Step 3: Write minimal implementation**

Dans la dataclass `MoveIntent` (ligne 106), ajouter le champ en DERNIER (les
champs à défaut doivent suivre) :

```python
    tags: frozenset = frozenset()
```

et documenter dans la docstring de la classe :

```
    tags        : faits SECONDAIRES du même coup, à mentionner sans changer de
                  catégorie. Aujourd'hui : "gives_check" quand le coup donne
                  échec alors que son kind principal est autre chose (une
                  prise, un sacrifice) -- sans ce champ, l'échec était
                  purement et simplement perdu (observé sur Fxb5+).
```

Puis, juste avant chaque `return MoveIntent(...)` autre que `MATE` et
`GIVES_CHECK`, calculer une fois en tête de fonction :

```python
    # Faits secondaires : vrais mais non structurants pour le classement.
    board.push(move)
    also_checks = board.is_check()
    board.pop()
    tags = frozenset({"gives_check"}) if also_checks else frozenset()
```

et passer `tags=tags` à chaque construction de `MoveIntent` sauf `MATE` et
`GIVES_CHECK` (où l'échec est déjà le sujet principal).

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_move_intent.py`
Expected: PASS

- [ ] **Step 5: Mentionner le tag dans le tissage**

Dans `narration_weaver.weave_intent` (ligne 262), après avoir obtenu
l'observation du fragment d'intention, suffixer la mention d'échec :

```python
    # Un fait secondaire vrai du même coup (voir MoveIntent.tags) : on
    # l'accroche à l'observation plutôt que d'en faire une phrase, pour ne pas
    # diluer l'idée principale.
    if "gives_check" in getattr(intent, "tags", frozenset()):
        observation = f"{observation}, avec échec"
```

(Adapter le nom de la variable locale à celui déjà utilisé dans la fonction.)

- [ ] **Step 6: Tester le rendu complet**

Dans `test_narration_weaver.py` :

```python
def test_echec_secondaire_apparait_dans_le_texte():
    import chess, move_intent, fragment_library
    board = chess.Board("4k3/8/8/1p6/B7/8/8/4K3 w - - 0 1")
    intent = move_intent.detect_move_intent(
        board, {"move_uci": "a4b5", "pv_uci": ["a4b5"]})
    out = narration_weaver.weave_intent(intent, None, "popular")
    check("échec" in out["text"].lower(),
          f"l'echec du coup doit apparaitre, texte obtenu : {out['text']!r}")
```

- [ ] **Step 7: Run tests**

Run: `python test_move_intent.py` puis `python test_narration_weaver.py`
Expected: les deux PASS

- [ ] **Step 8: Commit**

```bash
git add move_intent.py narration_weaver.py test_move_intent.py test_narration_weaver.py
git commit -m "MoveIntent.tags : l'echec d'une prise n'est plus perdu au classement"
```

---

### Task 4 : Quatre intentions calmes

47 % des coups audités sont `quiet` et ne sont jamais racontés. On leur donne une classification, en réutilisant les drapeaux que `engine_analysis` pose déjà sur chaque candidat (`is_castle`, `is_developing_minor`) et le détecteur de colonne de `why_detector`.

**Files:**
- Modify: `move_intent.py`
- Test: `test_move_intent.py`

**Interfaces:**
- Consumes: `why_detector._open_file_status(board, move)` (existant)
- Produces: constantes `DEVELOP = "develop"`, `CASTLE = "castle"`, `ROOK_FILE = "rook_file"`, `REPOSITION = "reposition"` — toutes hors de `FORCING_KINDS`.

- [ ] **Step 1: Write the failing test**

```python
def test_intentions_calmes():
    # Roque : le drapeau is_castle est deja pose par engine_analysis, mais
    # detect_move_intent doit rester correct meme sans lui (board fait foi).
    b = chess.Board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1")
    i = move_intent.detect_move_intent(b, {"move_uci": "e1g1", "pv_uci": ["e1g1"]})
    check(i.kind == move_intent.CASTLE, f"O-O doit etre CASTLE, obtenu {i.kind}")

    # Developpement : fou quittant la rangee de fond, sans capture.
    b = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    i = move_intent.detect_move_intent(b, {"move_uci": "f1c4", "pv_uci": ["f1c4"]})
    check(i.kind == move_intent.DEVELOP, f"Fc4 doit etre DEVELOP, obtenu {i.kind}")

    # Tour sur colonne ouverte (colonne d vide de pions).
    b = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1")
    i = move_intent.detect_move_intent(b, {"move_uci": "d1d5", "pv_uci": ["d1d5"]})
    check(i.kind == move_intent.ROOK_FILE, f"Td5 doit etre ROOK_FILE, obtenu {i.kind}")

    # Aucune de ces categories -> QUIET residuel (poussee de pion sur l'aile).
    b = chess.Board("4k3/ppp2ppp/8/8/8/8/PPP2PPP/4K3 w - - 0 1")
    i = move_intent.detect_move_intent(b, {"move_uci": "a2a3", "pv_uci": ["a2a3"]})
    check(i.kind == move_intent.QUIET, f"a3 doit rester QUIET, obtenu {i.kind}")

    for kind in (move_intent.DEVELOP, move_intent.CASTLE, move_intent.ROOK_FILE,
                 move_intent.REPOSITION):
        check(kind not in move_intent.FORCING_KINDS,
              f"{kind} est calme : ne doit PAS primer sur une tactique")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_move_intent.py`
Expected: FAIL — `module 'move_intent' has no attribute 'CASTLE'`

- [ ] **Step 3: Write minimal implementation**

Dans `move_intent.py`, sous le bloc `# NON FORÇANTE :` (ligne 42) :

```python
# CALMES (non forçantes : elles ne priment jamais sur une tactique, mais elles
# DÉCRIVENT le coup, ce que QUIET seul ne permettait pas -- 47% des coups
# tombaient dans ce trou et recevaient un commentaire de position sans rapport
# avec la flèche, mesuré à l'audit du 2026-07-28).
DEVELOP = "develop"          # cavalier/fou quittant la rangée de fond
CASTLE = "castle"            # le roque
ROOK_FILE = "rook_file"      # tour/dame arrivant sur une colonne ouverte ou semi-ouverte
REPOSITION = "reposition"    # pièce déjà développée qui change de poste, sans capture
QUIET = "quiet"              # résidu : rien de saillant -> le thème de position parle
```

Puis, dans `detect_move_intent`, remplacer le `return` final `QUIET` par la
cascade suivante (à placer APRÈS toutes les catégories forçantes, pour qu'une
tactique garde toujours la priorité) :

```python
    # --- Catégories calmes (aucune ne prime sur une tactique) ---------------
    from_rank = chess.square_rank(move.from_square)
    moved = board.piece_type_at(move.from_square)
    common = dict(
        from_square=move.from_square, to_square=move.to_square,
        moved_piece=moved, captured_piece=None, material_delta=0,
        gives_check=False, tags=tags,
    )

    if board.is_castling(move):
        return MoveIntent(kind=CASTLE, forcing=False, **common)

    home_rank = 0 if board.turn == chess.WHITE else 7
    if moved in (chess.KNIGHT, chess.BISHOP) and from_rank == home_rank:
        return MoveIntent(kind=DEVELOP, forcing=False, **common)

    # _open_file_status(board, file_index, my_side) -> "open" | "half_open" | None
    # (why_detector.py:125). On regarde la colonne d'ARRIVÉE de la tour/dame.
    if moved in (chess.ROOK, chess.QUEEN):
        status = why_detector._open_file_status(
            board, chess.square_file(move.to_square), board.turn)
        if status in ("open", "half_open"):
            return MoveIntent(kind=ROOK_FILE, forcing=False, **common)

    if moved in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN) and from_rank != home_rank:
        return MoveIntent(kind=REPOSITION, forcing=False, **common)

    return MoveIntent(kind=QUIET, forcing=False, **common)
```

Ajouter l'import en tête de `move_intent.py` :

```python
import why_detector
```

**Attention au cycle d'import :** `why_detector` n'importe que `chess`, donc
`move_intent -> why_detector` est sûr. Vérifier par `python -c "import move_intent"`
après l'ajout.

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_move_intent.py`
Expected: PASS

- [ ] **Step 5: Vérifier qu'aucune tactique n'a été dégradée**

Run: `python test_move_intent.py` (suite complète, y compris les tests
préexistants sur `CAPTURE_FREE` / `SACRIFICE` / `GIVES_CHECK`)
Expected: PASS — aucune régression : les catégories forçantes sont testées AVANT la cascade calme.

- [ ] **Step 6: Commit**

```bash
git add move_intent.py test_move_intent.py
git commit -m "Quatre intentions calmes : les coups tranquilles sont enfin classes"
```

---

### Task 5 : Fragments des intentions calmes

**Files:**
- Modify: `fragment_library.py`
- Test: `test_fragment_library.py`

**Interfaces:**
- Consumes: `move_intent.DEVELOP/CASTLE/ROOK_FILE/REPOSITION` (Task 4), `_piece_with_article` (Task 1)
- Produces: `_frag_develop`, `_frag_castle`, `_frag_rook_file`, `_frag_reposition`, enregistrés dans `_INTENT_FUNCS`

- [ ] **Step 1: Write the failing test**

```python
def test_fragments_calmes_nomment_le_coup():
    import chess, move_intent
    cas = [
        ("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1", "f1c4", "c4"),
        ("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 1", "e1g1", None),
        ("4k3/ppp2ppp/8/8/8/8/PPP2PPP/3RK3 w - - 0 1", "d1d5", "d5"),
    ]
    for fen, uci, case in cas:
        board = chess.Board(fen)
        intent = move_intent.detect_move_intent(board, {"move_uci": uci, "pv_uci": [uci]})
        for voice in ("popular", "creative", "classical"):
            frag = fragment_library.fragments_for_intent(intent, voice)
            check(frag is not None,
                  f"[{voice}] {uci} ({intent.kind}) doit avoir un fragment, pas None")
            blob = " ".join(v for v in frag.values() if v)
            check(blob.strip() != "", f"[{voice}] {uci} : fragment vide")
            if case:
                check(case in blob,
                      f"[{voice}] {uci} : le texte doit citer la case {case}, obtenu {blob!r}")
            check(blob[0].islower() or blob[0].isdigit(),
                  f"[{voice}] {uci} : contrat de clause, minuscule initiale")
            check(not blob.rstrip().endswith("."),
                  f"[{voice}] {uci} : contrat de clause, pas de point final")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_fragment_library.py`
Expected: FAIL — `fragments_for_intent` retourne `None` (kind absent de `_INTENT_FUNCS`)

- [ ] **Step 3: Write minimal implementation**

Dans `fragment_library.py`, avant `_INTENT_FUNCS` :

```python
def _frag_develop(intent, voice, ctx):
    # Une mineure sort de la rangée de fond. Fait vérifiable : la pièce et sa
    # case d'arrivée, lues sur l'intent.
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        return _f(f"{par} entre dans la partie {where}".replace("  ", " ").rstrip(),
                  "sors tes pièces d'abord, les idées viendront après", None)
    if voice == CLASSICAL:
        return _f(f"{par} se développe {where}".replace("  ", " ").rstrip(),
                  "termine ton développement avant d'ouvrir le jeu", None)
    return _f(f"{par} sort {where}".replace("  ", " ").rstrip(),
              "développe, puis roque", None)


def _frag_castle(intent, voice, ctx):
    # Le roque : deux effets réels et simultanés (roi à l'abri, tour reliée).
    if voice == CREATIVE:
        return _f("ton roi se met à l'abri et ta tour rejoint le jeu",
                  "mets-toi en sécurité, tu attaqueras plus librement ensuite", None)
    if voice == CLASSICAL:
        return _f("le roque met le roi en sécurité et active la tour",
                  "sécurise le roi avant d'entamer une opération au centre", None)
    return _f("tu roques : roi à l'abri, tour connectée",
              "roque maintenant, c'est le bon moment", None)


def _frag_rook_file(intent, voice, ctx):
    # Tour/dame arrivant sur une colonne ouverte ou semi-ouverte (fait calculé
    # par why_detector._open_file_status, voir move_intent).
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        return _f(f"{par} prend la colonne {where}".replace("  ", " ").rstrip(),
                  "une colonne ouverte, c'est une autoroute : occupe-la avant lui", None)
    if voice == CLASSICAL:
        return _f(f"{par} occupe la colonne ouverte {where}".replace("  ", " ").rstrip(),
                  "double ensuite sur la colonne pour en tirer profit", None)
    return _f(f"{par} se poste sur la colonne ouverte {where}".replace("  ", " ").rstrip(),
              "les tours aiment les colonnes ouvertes, garde-la", None)


def _frag_reposition(intent, voice, ctx):
    # Pièce déjà développée qui change de poste, sans capture : on ne peut pas
    # affirmer POURQUOI sans détecteur dédié, donc on décrit le fait seul.
    dest = _sq(intent.to_square)
    par = _piece_with_article(intent.moved_piece)
    where = f"en {dest}" if dest else ""
    if voice == CREATIVE:
        return _f(f"{par} va chercher mieux {where}".replace("  ", " ").rstrip(),
                  "améliore ta pièce la moins bien placée, c'est souvent le meilleur coup", None)
    if voice == CLASSICAL:
        return _f(f"{par} se replace {where}".replace("  ", " ").rstrip(),
                  "améliore la pièce la moins active avant de forcer le jeu", None)
    return _f(f"{par} change de poste {where}".replace("  ", " ").rstrip(),
              "repositionne, il n'y a rien de forcé ici", None)
```

Enregistrer les quatre :

```python
_INTENT_FUNCS = {
    "mate": _frag_mate,
    "check_escape": _frag_check_escape,
    "capture_free": _frag_capture_free,
    "sacrifice": _frag_sacrifice,
    "gives_check": _frag_gives_check,
    "promotion": _frag_promotion,
    "capture_trade": _frag_capture_trade,
    "develop": _frag_develop,
    "castle": _frag_castle,
    "rook_file": _frag_rook_file,
    "reposition": _frag_reposition,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_fragment_library.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fragment_library.py test_fragment_library.py
git commit -m "Fragments des intentions calmes dans les 3 voix"
```

---

### Task 6 : `render()` s'ancre toujours sur le coup

C'est le cœur du changement : supprimer la branche `if intent.forcing`.

**Files:**
- Modify: `narration_v2.py:191-215`
- Test: `test_narration_v2.py`

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: comportement — `render()` rend un texte issu de l'intention dès qu'un fragment existe, quel que soit `forcing` ; le thème n'est gardé en appui que s'il passe `_intent_is_coherent_with_theme`.

- [ ] **Step 1: Write the failing test**

Dans `test_narration_v2.py` :

```python
def test_coup_calme_est_raconte_pas_la_position():
    import chess
    board = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    candidates = [{
        "move_uci": "f1c4", "move_san": "Bc4", "cp": 30, "eval_loss": 0,
        "score": "+0.30", "pv_uci": ["f1c4"], "pv_san": ["Bc4"],
        "is_capture": False, "is_check": False, "is_castle": False,
        "is_king_move": False, "is_developing_minor": True,
        "is_pawn_center_push": False, "to_square_central": False,
        "win_prob": None, "moving_piece_value": 3, "captured_piece_value": None,
    }]
    out = narration_v2.narrate(board, candidates, "popular", chosen=candidates[0])
    check("c4" in out["text"],
          f"un coup calme doit citer sa case d'arrivee, obtenu {out['text']!r}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_narration_v2.py`
Expected: FAIL — le texte parle du thème d'ouverture, ne contient pas `c4`

- [ ] **Step 3: Write minimal implementation**

Dans `narration_v2.py`, remplacer le bloc lignes 207-215 par :

```python
    # Le paragraphe part TOUJOURS du coup affiché : décrire la position sans
    # regarder la flèche produisait "le fou bouge, le texte parle du pion"
    # (mesuré : 47% des coups, voir la spec du 2026-07-28). Le thème de
    # position n'entre plus qu'en APPUI, et seulement s'il est géométriquement
    # cohérent avec le coup -- garde qui existait déjà mais n'était appliquée
    # qu'aux coups forçants.
    if intent is not None:
        kept_theme = selection.lead if _intent_is_coherent_with_theme(intent, selection.lead) else None
        woven = nw.weave_intent(intent, kept_theme, profile_id, ctx, caution_text=caution_text)
        if woven.get("text"):
            return woven
        # Repli : kind sans fragment (QUIET résiduel) -> tissage de thème.

    return nw.weave(selection.lead, selection.supports, profile_id, ctx, caution_text=caution_text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_narration_v2.py`
Expected: PASS

- [ ] **Step 5: Journaliser l'exception silencieuse**

Toujours dans `narration_v2.py`, ligne 204, le `except Exception` masquait
totalement les échecs de détection. Le remplacer par :

```python
        except Exception as e:
            # Best-effort : jamais bloquant. Mais SILENCIEUX auparavant, ce qui
            # a rendu le diagnostic beaucoup plus long -- on journalise comme
            # le fait déjà l'appelant (web_bridge.py, ligne 1305).
            print(f"⚠ Intention de coup indisponible ({profile_id}) : {e}")
            intent = None
```

- [ ] **Step 6: Vérifier l'absence de régression sur les forçants**

Run: `python test_narration_v2.py` puis `python test_bricks_mirror.py`
Expected: les deux PASS

- [ ] **Step 7: Commit**

```bash
git add narration_v2.py test_narration_v2.py
git commit -m "Le paragraphe s'ancre toujours sur le coup, le theme passe en appui"
```

---

### Task 7 : Anti-répétition

À l'audit, les coups 1-2-3 sortaient le même paragraphe mot pour mot, et les 6 prises la même phrase.

**Files:**
- Modify: `narration_v2.py` (signature `render`)
- Modify: `web_bridge.py:1288` (passage de l'historique) et `BridgeState.__init__` (état, près de `_selection_cache` ligne 306)
- Test: `test_narration_v2.py`

**Interfaces:**
- Produces: `render(..., recent_kinds=None)` — `recent_kinds` est une séquence des `kind` rendus récemment pour ce profil, du plus ancien au plus récent. `None` = comportement actuel.

- [ ] **Step 1: Write the failing test**

```python
def test_pas_deux_fois_le_meme_texte_de_suite():
    import chess
    def cand(uci, san, developing):
        return [{
            "move_uci": uci, "move_san": san, "cp": 20, "eval_loss": 0,
            "score": "+0.20", "pv_uci": [uci], "pv_san": [san],
            "is_capture": False, "is_check": False, "is_castle": False,
            "is_king_move": False, "is_developing_minor": developing,
            "is_pawn_center_push": False, "to_square_central": False,
            "win_prob": None, "moving_piece_value": 3, "captured_piece_value": None,
        }]
    b1 = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    c1 = cand("f1c4", "Bc4", True)
    t1 = narration_v2.narrate(b1, c1, "popular", chosen=c1[0])["text"]

    b2 = chess.Board("rnbqkb1r/pppp1ppp/5n2/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 0 1")
    c2 = cand("g1f3", "Nf3", True)
    out2 = narration_v2.narrate(b2, c2, "popular", chosen=c2[0],
                                recent_kinds=["develop"])
    check(out2["text"] != t1,
          f"deux DEVELOP consecutifs ne doivent pas donner le meme texte : {t1!r}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_narration_v2.py`
Expected: FAIL — `narrate() got an unexpected keyword argument 'recent_kinds'`

- [ ] **Step 3: Write minimal implementation**

Ajouter le paramètre à `render` ET à `narrate` (qui le relaie), et le
transmettre au weaver via `ctx`. Dans `narration_v2.render` :

```python
def render(selection, profile_id, chosen=None, why_motif=None, why_detail=None,
           board=None, caution_text=None, recent_kinds=None):
```

Documenter dans la docstring :

```
    recent_kinds : kinds d'intention déjà rendus pour CE profil sur les
        positions précédentes (du plus ancien au plus récent). Sert à ne pas
        répéter la même formulation plusieurs coups d'affilée -- observé en
        pratique : trois coups de développement de suite sortaient le même
        paragraphe mot pour mot. None -> aucune contrainte.
```

Puis passer l'information dans le `FragmentContext` construit ligne 186 :

```python
    ctx = FragmentContext(
        board=board, chosen=chosen, why_motif=why_motif, why_detail=why_detail,
        eval_cp=selection.eval_cp,
    )
    ctx.recent_kinds = tuple(recent_kinds or ())
```

Dans `fragment_library`, chaque `_frag_*` d'intention ayant plusieurs
formulations doit choisir sa variante en tenant compte de
`getattr(ctx, "recent_kinds", ())` : si le `kind` courant apparaît déjà dans
`recent_kinds`, décaler d'un cran dans la liste des formulations. Ajouter pour
cela un helper unique, à placer près de `_f` :

```python
def _pick_variant(options, intent, ctx):
    """
    Choisit une formulation parmi `options` (liste non vide, de la plus
    naturelle à la moins), en s'écartant de celle déjà servie récemment pour ce
    même kind. Déterministe : même position + même historique -> même texte.
    """
    recent = getattr(ctx, "recent_kinds", ()) if ctx else ()
    seen = sum(1 for k in recent if k == intent.kind)
    base = (intent.to_square or 0) % len(options)
    return options[(base + seen) % len(options)]
```

et donner au minimum DEUX formulations à `_frag_develop`, `_frag_capture_free`
et `_frag_reposition` (les trois kinds les plus fréquents à l'audit), en les
passant par `_pick_variant`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_narration_v2.py`
Expected: PASS

- [ ] **Step 5: Brancher l'historique en production**

Dans `web_bridge.py`, `BridgeState.__init__`, près de `_selection_cache`
(ligne 306) :

```python
        # Kinds d'intention déjà racontés par profil (voir narration_v2.render,
        # recent_kinds) -- évite de resservir la même formulation plusieurs
        # coups d'affilée. Borné à 4 : au-delà, la répétition ne se voit plus.
        self._recent_intent_kinds = {}
```

Au point d'appel (ligne 1288) :

```python
                woven = narration_v2.render(
                    selection, profile_id, chosen=chosen,
                    why_motif=why_motif, why_detail=why_detail, board=board,
                    recent_kinds=self._recent_intent_kinds.get(profile_id, ()),
                )
                if woven.get("text"):
                    kind = (woven.get("intent_kind")
                            or (woven.get("lead") and str(woven["lead"])) or "")
                    history = list(self._recent_intent_kinds.get(profile_id, ()))
                    history.append(kind)
                    self._recent_intent_kinds[profile_id] = tuple(history[-4:])
```

Pour que `woven["intent_kind"]` existe, `narration_weaver.weave_intent` doit
l'ajouter à son dict de retour :

```python
    result["intent_kind"] = intent.kind
```

Remettre l'historique à zéro là où `_selection_cache.invalidate()` est déjà
appelé (nouvelle partie / Rafraîchir) :

```python
        self._recent_intent_kinds.clear()
```

- [ ] **Step 6: Run tests**

Run: `python test_narration_v2.py`, `python test_narration_weaver.py`, `python test_bricks_mirror.py`
Expected: les trois PASS

- [ ] **Step 7: Commit**

```bash
git add narration_v2.py narration_weaver.py fragment_library.py web_bridge.py test_narration_v2.py
git commit -m "Anti-repetition : la meme intention consecutive change de formulation"
```

---

### Task 8 : Vérification de bout en bout par l'audit

C'est la mesure qui a produit le diagnostic ; c'est elle qui doit constater la correction.

**Files:**
- Modify: `audit_narration.py`

- [ ] **Step 1: Rejouer l'audit d'origine**

Run: `PYTHONIOENCODING=utf-8 python audit_narration.py > audit_apres.txt 2>&1`
(sous PowerShell : `$env:PYTHONIOENCODING="utf-8"; python audit_narration.py > audit_apres.txt`)

- [ ] **Step 2: Vérifier les quatre critères**

Lire `audit_apres.txt` et confirmer, sur la partie Morphy — Opera 1858 :

1. `Rd8#` est annoncé comme **mat** (et non « l'adversaire doit réagir »).
2. `Bxb5+` mentionne l'**échec**.
3. Chaque paragraphe des coups `Bc4`, `Be3`, `Bg5`, `O-O-O`, `Rd1` cite la pièce ou la case du coup proposé.
4. Aucun paragraphe n'est identique au précédent deux coups de suite (coups 1-2-3 en particulier).

Si l'un des quatre échoue, revenir à la tâche correspondante — ne pas corriger dans l'audit.

- [ ] **Step 3: Commit**

```bash
git add audit_narration.py
git commit -m "Audit de verification apres ancrage de la narration sur le coup"
```

---

### Task 9 : Audit approfondi multi-scénarios

Demande explicite : après la correction, chercher d'autres fautes et affiner les commentaires sur des situations que la partie d'attaque ne couvre pas.

**Files:**
- Create: `audit_scenarios.py`

**Interfaces:**
- Consumes: le même pipeline que `audit_narration.py`
- Produces: un rapport par scénario + un relevé automatique des fautes détectables

- [ ] **Step 1: Écrire le harnais multi-scénarios**

Créer `audit_scenarios.py` sur le modèle de `audit_narration.py`, mais alimenté
par une liste de positions FEN couvrant ce que la partie d'attaque ne montre
pas. Chaque entrée : `(nom, fen, nombre de coups à dérouler)`.

```python
SCENARIOS = [
    ("finale tours+pions", "8/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", 8),
    ("finale roi+pions", "8/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", 8),
    ("position fermee", "r1bqk2r/pp2bppp/2n1pn2/2pp4/2PP4/2N1PN2/PP2BPPP/R1BQK2R w KQkq - 0 1", 10),
    ("defense sous attaque", "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 0 1", 8),
    ("finale de dames", "8/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1", 6),
    ("position egale sans jeu", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 6),
]
```

Pour chaque scénario, dérouler N coups en jouant à chaque fois le coup
recommandé, et imprimer la même ligne que `audit_narration.py` (coup / intent /
thème / texte).

- [ ] **Step 2: Ajouter des contrôles automatiques**

Le harnais doit relever seul, sans lecture humaine :

```python
FAUTES = []

def controler(rows):
    for i, r in enumerate(rows):
        # 1. Le texte cite-t-il la piece ou la case du coup propose ?
        case = r["coup"][-2:]
        if case not in r["texte"] and r["intent"] != "quiet":
            FAUTES.append(f"{r['coup']} ({r['intent']}) : le texte ne cite pas {case}")
        # 2. Repetition immediate
        if i > 0 and r["texte"] == rows[i - 1]["texte"]:
            FAUTES.append(f"{r['coup']} : texte identique au coup precedent")
        # 3. Accord et coherence de vocabulaire
        for faute in ("le tour", "le dame", "un tour", "une cavalier", "une fou"):
            if faute in r["texte"]:
                FAUTES.append(f"{r['coup']} : accord fautif '{faute}'")
        # 4. Affirmation non prouvee
        if "sans reprise" in r["texte"] and r["why"] not in ("undefended", "not_recaptured"):
            FAUTES.append(f"{r['coup']} : 'sans reprise' affirme avec why={r['why']}")
        # 5. Contrat de clause casse par le tissage
        if r["texte"] and r["texte"][0].islower():
            FAUTES.append(f"{r['coup']} : paragraphe commence en minuscule")
```

- [ ] **Step 3: Lancer et lire**

Run: `PYTHONIOENCODING=utf-8 python audit_scenarios.py > audit_scenarios.txt 2>&1`

Lire la section `FAUTES` en fin de rapport, puis lire les paragraphes eux-mêmes :
les contrôles automatiques attrapent les fautes de forme, **pas** la pertinence.
Chercher à la lecture les cas où le texte est grammaticalement correct mais
pédagogiquement creux — typiquement les finales, où `REPOSITION` risque de
produire « change de poste » sur des coups qui ont une idée précise
(opposition, création d'un pion passé, activation du roi).

- [ ] **Step 4: Rapporter, ne pas corriger en aveugle**

Écrire les fautes trouvées dans un nouveau document
`docs/superpowers/specs/<date>-narration-audit-scenarios.md` — constat + cas
reproductible pour chacune. Décider avec l'utilisateur lesquelles traiter : une
finale mal commentée peut appeler une intention supplémentaire
(`KING_ACTIVATION`, `PASSED_PAWN`), ce qui est un lot à part entière.

- [ ] **Step 5: Commit**

```bash
git add audit_scenarios.py docs/superpowers/specs/
git commit -m "Audit multi-scenarios : finales, positions fermees, defense"
```
