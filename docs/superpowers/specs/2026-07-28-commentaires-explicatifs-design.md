# Rendre les commentaires explicatifs

Date : 2026-07-28

## Contexte

Le chantier précédent (« narration ancrée sur le coup ») a rendu le commentaire
**cohérent** avec la flèche : il nomme désormais la pièce et la case du coup
proposé. Il ne l'a pas rendu **explicatif**. Aujourd'hui le coach dit *quoi*
puis *quoi ensuite*, jamais *pourquoi celui-là* :

> « le fou sort en c4. Développe, puis roque. »

Le contrat de fragment prévoit pourtant trois emplacements — `{observation,
cause, plan}` — dont un, `cause`, est exactement celui du « pourquoi ». Mesure
sur `fragment_library.py` (comptage AST des appels `_f(...)` avec 3e argument
non nul) :

| | |
|---|---|
| Fragments remplissant `cause` | **5 sur 24** |
| Parmi les fragments d'INTENTION (ceux qui pilotent l'essentiel des commentaires) | **1 sur 11** (`_frag_capture_free`) |

L'emplacement existe, il n'est presque jamais rempli.

## Ce qu'on ne fera pas, et pourquoi

Une quatrième piste avait été envisagée puis écartée : expliquer en montrant la
ligne du coup rejeté (« plutôt que Cc3, après quoi l'adversaire joue d5 »). Elle
s'appuierait sur `variation_narrator`, dont un audit de cette session montre
qu'il **affirme des faits faux** — sur 8 positions d'une vraie partie, il
annonce « cette rupture de pion ouvre la position » sur `Qxb7`, `Bc4` et `Bg5`,
trois coups où aucune rupture de pion n'apparaît dans la ligne. Bâtir dessus
serait construire sur du faux. À traiter dans un lot séparé.

## Principe

Trois composants indépendants, dont le troisième conditionne les deux autres :

- **A. Prophylaxie** — ce que le coup EMPÊCHE. Le seul « pourquoi » profond d'un
  coup calme : un coup tranquille est rarement bon pour ce qu'il fait, il est
  bon pour ce qu'il enlève à l'adversaire.
- **B. Contraste géométrique** — ce que le coup CHANGE, compté sans moteur.
  Moins profond, toujours vrai, jamais cher.
- **C. Le seuil** — la `cause` n'est rendue que quand le coup ne s'impose pas de
  lui-même. C'est ce qui distingue un commentaire explicatif d'un bavardage.

## Composants

### A. Détecteur de prophylaxie — `prophylaxis.py` (fichier neuf)

**Le fait à prouver** : le meilleur coup adverse d'avant n'est plus disponible
après mon coup.

**Méthode, en deux temps, dont un seul coûte un appel moteur :**

1. **La menace adverse** — analyser la position courante avec le trait inversé
   (`board.push(chess.Move.null())`, supporté par python-chess), profondeur 10 à
   12, `multipv=1`. On obtient le coup `M` que l'adversaire jouerait s'il avait
   le trait. Un seul appel, mesuré à **87 ms (depth 10) / 154 ms (depth 12)** sur
   le moteur de fond existant (1 thread, hash 128 Mo).
   **Garde obligatoire, et non négociable** : le coup nul est illégal quand on
   est en échec, et python-chess **ne lève aucune erreur** dans ce cas — vérifié
   en pratique, `board.push(chess.Move.null())` sur une position en échec produit
   silencieusement une position invalide (`is_valid()` retourne `False`). Sans
   garde explicite sur `board.is_check()`, on enverrait au moteur une position
   illégale et on exploiterait ce qu'il en retourne. C'est le piège principal de
   ce composant.

2. **L'effet de mon coup** — python-chess pur, aucun appel supplémentaire. On ne
   réclame la prophylaxie que dans le cas **indiscutable** : après mon coup, `M`
   n'est plus légal. Deux causes possibles, toutes deux nommables :
   - la pièce qui devait jouer `M` a été capturée ;
   - la case d'arrivée de `M` est désormais occupée ou contrôlée de telle sorte
     que le coup n'existe plus (clouage, interposition).

   On ne tente PAS de dire « `M` est devenu moins bon » : ce jugement demanderait
   une seconde analyse et resterait discutable. `M` illégal est un fait binaire,
   vérifiable, et suffisant.

**Sortie** : `None`, ou un dict portant le SAN de `M` et la raison de sa
disparition — de quoi écrire « ce coup enlève d5 à l'adversaire » sans rien
inventer.

**Où ça tourne** : en tâche de fond, après l'affichage de la flèche, sur le
`scenario_engine` déjà en place (voir `web_bridge._attach_scenario_async`, même
schéma). La flèche n'attend jamais la prophylaxie.

### B. Contraste géométrique — dans `move_intent.py`

Faits comptables, calculés sur la position avant/après, sans moteur :

- cases nouvellement contrôlées par la pièce jouée ;
- pièce à moi qui était attaquée par moins chère qu'elle et ne l'est plus
  (fuite) ;
- pièce à moi non défendue qui le devient ;
- pièce adverse nouvellement attaquée.

Chacun est un comptage, donc indiscutable. La frontière à ne pas franchir : « il
vise f7, défendu par le seul roi » est un comptage ; « f7, la case la plus
faible » est un jugement. C'est cette discipline qui a manqué à « va chercher
mieux », corrigé dans le lot précédent.

Portés sur `MoveIntent` comme les preuves de capture existantes
(`capture_undefended`, `capture_line_gain`), en fin de dataclass.

### C. Le seuil — dans `narration_v2.render`

`candidates[1]["eval_loss"]` mesure à quel point le meilleur coup se détache. La
règle :

- **écart large** — le coup s'impose, une explication est du bruit : `cause`
  omise, on garde observation + plan ;
- **écart faible** — c'est une vraie décision, l'explication se mérite : `cause`
  rendue.

Un seul nombre, déjà calculé. Il est aussi le garde-fou contre l'effet cumulé de
A et B : sans lui, deux sources d'explication s'ajouteraient à chaque coup, et
l'audit multi-scénarios a déjà montré qu'un texte qui revient devient du bruit.

La valeur de seuil est une **constante nommée, à calibrer en pratique** — pas une
valeur devinée une fois pour toutes. Elle doit être réglable sans toucher à la
logique.

## Flux de données

```
analyze_candidates ──> candidates[0..n]
                          │  candidates[1].eval_loss ──────> seuil (C)
                          ▼
                       chosen ──> move_intent  ──> contraste géométrique (B)
                          │                            │
   scenario_engine ───────┘                            │
   (tâche de fond)                                     ▼
   null move + depth 10-12 ──> prophylaxis (A) ──> MoveIntent.cause_*
                                                       │
                                                       ▼
                                          fragment_library : champ `cause`
                                                       │
                                                       ▼
                                            narration_weaver.weave_intent
```

## Gestion d'erreur

Même principe best-effort que le reste du pipeline : toute défaillance de la
détection laisse le commentaire actuel intact (observation + plan), jamais un
crash, jamais un texte vide. Mais — leçon du lot précédent — l'exception doit
être **journalisée**, pas avalée en silence : un `except Exception` muet a
allongé un diagnostic de plusieurs heures.

## Tests

- `prophylaxis` : cas où `M` devient illégal parce que sa pièce est capturée ;
  cas où `M` reste légal (aucune prophylaxie réclamée) ; position en échec (coup
  nul illégal → détection court-circuitée, aucune exception).
- Contraste géométrique : un cas par fait comptable, sur des FEN écrites à la
  main, sans moteur.
- Seuil : deux rendus, écart large et écart faible, vérifiant la présence ou
  l'absence de la `cause`.
- Garde « rien d'inventé » : aucun texte de `cause` ne doit contenir de
  jugement comparatif — même famille de garde que celle qui surveille déjà
  « mieux / meilleur / pire ».
- **Vérification de bout en bout** : rejouer `audit_narration.py` et
  `audit_scenarios.py` et constater que les `cause` apparaissent sur les coups
  au choix serré, restent absentes sur les coups évidents, et qu'aucune n'est
  fausse.

## Hors périmètre

- `variation_narrator` et ses ruptures de pion inventées : lot séparé, plus
  urgent qu'il n'y paraît puisqu'il s'agit de faits faux affichés.
- Les idées de finale (opposition, case-clé, pion passé, zugzwang), documentées
  dans `2026-07-28-narration-audit-scenarios.md` : elles demandent leurs propres
  détecteurs et constituent un chantier à part entière.
