# Narration ancrée sur le coup affiché

Date : 2026-07-28

## Contexte

Le coach affiche une flèche (le coup recommandé) et un paragraphe de commentaire.
En pratique, le commentaire parle rarement du coup montré : « la flèche montre le
fou bouger mais le commentaire parle du pion ».

Cause structurelle, lue dans le code puis mesurée. `narration_v2.render()`
(ligne 207) ne construit un texte à partir du coup que si l'intention est
FORÇANTE :

```python
if intent is not None and intent.forcing:
    ...                      # texte construit sur LE COUP
return nw.weave(selection.lead, ...)   # sinon : thème de POSITION, coup ignoré
```

`move_intent.detect_move_intent` ne connaît que 6 intentions forçantes (échec,
échec paré, prise nette, échange, sacrifice, promotion). Tout le reste retombe
sur `QUIET`, et `fragment_library._INTENT_FUNCS` n'a volontairement aucun
fragment pour `quiet` (ligne 804) : le paragraphe est alors calculé **sans jamais
regarder le coup**.

### Mesure

Audit d'une vraie partie (Morphy — Opera 1858) rejouée dans le pipeline de
production : 17 positions, coup proposé + intent + thème + texte final.

| intent | occurrences |
|---|---|
| `quiet` | 8 / 17 (47 %) |
| `capture_free` | 6 |
| `sacrifice` | 2 |
| `gives_check` | 1 |

Quatre défauts distincts, tous reproduits :

1. **Les coups calmes ne sont pas racontés.** `Bc4`, `Be3`, `Bg5`, `O-O-O`,
   `Rd1` reçoivent un commentaire de position qui aurait été identique sans le
   coup. C'est le symptôme signalé.
2. **Répétition.** Coups 1-2-3 : le même paragraphe mot pour mot. Coups 6, 7, 9,
   12, 14 : la même phrase sur la paire de fous. Le thème ne bouge que si la
   structure bouge, alors que la flèche change à chaque coup.
3. **Information perdue sur les forçants.** `Bxb5+` donne échec : le texte n'en
   dit rien, `CAPTURE_FREE` primant sur `GIVES_CHECK` et un seul fragment étant
   rendu. Plus grave, `Rd8#` est **mat** et sort « l'adversaire doit réagir tout
   de suite » — il n'existe aucune intention `MATE`. `Qb8+`, le sacrifice de dame
   qui force ce mat, sort « la compensation est bien réelle ».
4. **Fautes de fond.** « le tour adverse » (accord), « le **pion** tombe… prends
   la **pièce** » (dans la même phrase), « tombe sans reprise » affirmé sur des
   cases qui sont reprises. Les 6 prises sortent la même phrase gabarit.

Gaspillage à noter : coup 14 `Rd1`, `why_detector` **détecte** `open_file`, puis
l'information est jetée parce que l'intention est `quiet`.

## Principe

**Le paragraphe part toujours du coup affiché.** Le thème de position devient un
appui, jamais le socle.

Concrètement, `render()` perd sa branche `if intent.forcing` : il y a toujours
une intention à raconter, et le thème n'est tissé en secondaire que s'il passe
`_intent_is_coherent_with_theme` (`narration_v2.py:68`) — garde qui existe déjà
mais n'est appliquée aujourd'hui qu'aux coups forçants.

## Composants

### 1. `move_intent.py` — compléter la classification

`detect_move_intent(board, chosen, ...)` reçoit déjà `chosen`, le dict candidat
produit par `engine_analysis.analyze_candidates`. Ce dict porte **déjà**
`is_castle`, `is_developing_minor`, `is_pawn_center_push`, `to_square_central`,
`is_king_move` — aujourd'hui utilisés seulement par le scoring de
`human_profile`. On les réutilise plutôt que de recalculer.

Ajouts :

- `MATE`, priorité absolue au-dessus de toutes les autres (`board.push(move)` +
  `is_checkmate()`). Corrige `Rd8#`.
- Un champ `tags: frozenset` en plus de `kind`, pour les faits secondaires du
  même coup — d'abord `gives_check`. Le fragment du `kind` principal peut alors
  mentionner l'échec sans changer de catégorie. Corrige `Bxb5+`.
- Quatre intentions calmes, couvrant l'essentiel du corpus observé :
  `DEVELOP` (cavalier ou fou quittant la rangée de fond — c'est exactement ce
  que teste `is_developing_minor`, `engine_analysis.py:141`),
  `CASTLE` (via `is_castle`), `ROOK_FILE` (tour/dame arrivant sur une colonne
  ouverte ou semi-ouverte, via `why_detector._open_file_status`), `REPOSITION`
  (pièce déjà développée qui change de poste sans capture).
- `QUIET` reste, mais devient le vrai résidu : lui seul laisse parler le thème
  de position.

Ces intentions ne sont pas « forçantes » au sens actuel (elles ne priment pas sur
une tactique) : le champ `forcing` garde son sens, mais il ne conditionne plus
l'existence d'un texte de coup.

### 2. `fragment_library.py` — fragments des nouvelles intentions

Un jeu `{observation, plan}` par nouvelle intention × 3 voix
(popular/creative/classical), au contrat de clause existant (minuscule initiale,
sans ponctuation finale). Enregistrés dans `_INTENT_FUNCS`.

Corrections dans les fragments existants :
- accord du nom de pièce (« la tour », « la dame ») — table de genre, pas de
  concaténation naïve ;
- le mot employé doit être celui de la pièce réellement prise (pas « pion » puis
  « pièce » dans la même phrase) ;
- « tombe sans reprise » n'est écrit que si `_capture_is_free` l'a prouvé par la
  case non défendue, pas par le simple bilan de ligne.

### 3. Anti-répétition

`render()` reçoit l'historique récent des `(kind, variante)` rendus pour ce
profil et écarte la variante identique quand le même couple revient. Le choix de
variante est déjà déterministe et dérivé de la position
(`fragment_library`) : il suffit d'y injecter cet historique. État porté par
`web_bridge.BridgeState` à côté de `_selection_cache`, remis à zéro en début de
partie.

## Flux de données

Inchangé jusqu'à `render()` :

```
analyze_candidates → select_move → chosen (dict candidat)
                                      │
        why_detector.detect_why ──────┤
                                      ▼
                        move_intent.detect_move_intent
                                      │  MoveIntent(kind, tags, …)
                                      ▼
   fragment_library.fragments_for_intent ─→ narration_weaver.weave_intent
                                      │
             thème de position ───────┘  (secondaire, si cohérent)
```

## Gestion d'erreur

On garde le principe best-effort existant : toute exception dans la chaîne
d'intention laisse retomber sur le tissage de thème actuel, jamais un crash
(`narration_v2.py:204`). Une réserve : ce `except Exception` est aujourd'hui
totalement silencieux et a rendu ce diagnostic plus long. Il doit journaliser le
motif sur la console, comme le fait déjà le bloc appelant de `web_bridge.py`
(ligne 1305).

## Tests

- `test_move_intent.py` : un cas par nouvelle intention (`MATE`, `DEVELOP`,
  `CASTLE`, `ROOK_FILE`, `REPOSITION`) et un cas `tags` (prise qui donne échec),
  sur des FEN écrites à la main — pas d'appel moteur.
- `test_fragment_library.py` : chaque nouvelle intention rend un fragment non
  vide dans les 3 voix, et respecte le contrat de clause.
- Anti-répétition : deux rendus successifs du même `kind` sur deux positions
  différentes ne donnent pas le même texte.
- **Vérification de bout en bout** : rejouer l'audit
  (`audit_narration.py`, harnais de la session) sur la même partie et vérifier
  que chaque paragraphe nomme la pièce ou la case du coup proposé, que `Rd8#`
  est annoncé comme mat, et qu'aucun paragraphe n'est répété à l'identique deux
  coups de suite. C'est la mesure qui a produit ce diagnostic ; c'est elle qui
  doit constater la correction.

## Hors périmètre

- `variation_narrator.py` (« la suite proposée »), signalé comme rarement
  pertinent lui aussi. Il mérite le même audit chiffré, dans un lot séparé.
- La refonte visuelle de la fenêtre commentaire : ici on ne change que le texte
  produit, pas la mise en page.
- La narration v1 (`narration.generate_narration`), conservée en repli tant que
  la transition v2 n'est pas terminée.
