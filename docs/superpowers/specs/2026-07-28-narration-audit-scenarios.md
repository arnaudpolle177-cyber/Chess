# Audit multi-scénarios de la narration — constat (2026-07-28)

Contexte : le chantier « narration ancrée sur le coup » a fait tomber le taux
de coups non racontés de 8/17 à 2/17 sur une partie d'attaque de référence
(Morphy, Opera 1858 — voir `audit_narration.py`). Cette partie ne couvre pas
les finales, les positions fermées, la défense sous attaque, ni les positions
égales sans jeu. Cet audit couvre ces cas via `audit_scenarios.py`, sur 6
scénarios, avec le moteur de production (Stockfish, profil `popular`, palier
2), et **avec `recent_kinds` effectivement branché** (voir méthode).

## Méthode et fiabilité du run

Le run précédent (celui laissé par l'agent interrompu) n'était pas fiable et
a été refait :

- **`recent_kinds` n'était pas branché.** Le harnais appelait
  `narration_v2.render(...)` sans le paramètre `recent_kinds`, alors que
  `web_bridge.py` (production, ligne 1296-1307) le fait systématiquement
  (historique des 4 derniers `intent_kind` par profil). Sans ce branchement,
  l'audit aurait mesuré une répétition qui n'existe pas forcément en
  production, et inversement aurait pu manquer une vraie absence de variation.
  **Corrigé** dans `audit_scenarios.py` (variable `recent_kinds`, mise à jour
  après chaque coup, reproduisant exactement la logique de `web_bridge.py`).
  Confirmé actif : sur des repositions consécutives, les formulations
  alternent bien (`_pick_variant` reçoit un historique non vide).

- **3 des 6 FEN de finale fournies dans le brief étaient illégales** (roi noir
  absent — ex. `8/5ppp/8/8/8/8/5PPP/6K1 w`). Nourri d'une position sans roi,
  le moteur renvoie un score dégénéré (sentinelle de mat convertie en cp), et
  la narration l'a fidèlement répété : **« Ta position est nettement
  meilleure ... un avantage d'environ 1000.0 pions »** dans le run de
  smoke-test. Ce n'était pas un bug de narration — le module a correctement
  raconté une entrée invalide. **Corrigé** : roi noir ajouté en symétrie dans
  les 3 FEN (`audit_scenarios.py`). Une deuxième itération a montré que la
  version symétrique naïve (rois boîtés par leurs propres pions) offrait un
  mat au dos immédiat (`Rxa8#` / `Qxd8#` dès le 1er coup) — corrigé une
  seconde fois en donnant du air (luft) aux rois, pour que les scénarios
  « finale tours+pions » et « finale de dames » déroulent réellement
  plusieurs coups au lieu de s'arrêter au premier.

- Les contrôles automatiques du brief avaient deux faux positifs, corrigés
  dans `audit_scenarios.py` :
  1. `case = r["coup"][-2:]` ne retirait pas le `+`/`#` d'échec/mat
     (`"Rxa8+"[-2:]` = `"8+"`, absent de tout texte qui dit correctement
     « en a8 ») — faisait crier « ne cite pas la case » sur **toute** capture
     ou échec. Le roque (`O-O`) n'a de toute façon pas de case au sens de ce
     contrôle. Corrigé : `.rstrip("+#")` + roque exempté comme `quiet`.
  2. Le contrôle d'accord `"une fou" in texte` matchait en sous-chaîne dans
     **« une fourchette »** (fork) — faux positif observé sur `Nxc3`. Corrigé
     avec une frontière de mot (`\b`) via `re.search`.

- Trois runs complets ont été faits après ces corrections (dépôt final :
  `audit_scenarios_report.txt`, dernier run). Les résultats sont stables
  d'un run à l'autre (même architecture de fautes, léger bruit sur les
  lignes de jeu choisies par le moteur selon la profondeur/le threading).

## 1. FAUX — le texte affirme quelque chose que la position ne soutient pas

### Attribution de pièce incorrecte dans `rook_file`

`fragment_library.py:_frag_rook_file` (ligne ~962) a un `plan` codé en dur :
`"les tours aiment les colonnes ouvertes, garde-la"` — **quelle que soit la
pièce qui a réellement joué**. Or `rook_file` se déclenche aussi pour une
**dame** arrivant sur une colonne ouverte (`move_intent.ROOK_FILE`, voir
`move_intent.py:54` : « tour/dame arrivant sur une colonne ouverte »).

Reproduit à 4 reprises sur 2 scénarios différents et 3 runs :

- Scénario « position fermée », coup 4. Qd8 :
  > « La dame se poste sur la colonne ouverte en d8. **Les tours aiment les
  > colonnes ouvertes**, garde-la. »
- Scénario « défense sous attaque », coup 1 (run 1). Qa5 : même schéma.
- Scénario « finale de dames », coup 2. Qe8 / coup 3. Qe7 : même schéma.

**Pourquoi c'est une faute** : l'observation nomme correctement la dame
(« la dame se poste... »), puis le plan qui suit affirme un fait sur « les
tours » — une pièce qui n'a pas joué ce coup-ci. Un joueur lisant ce
paragraphe peut légitimement se demander où est passée une tour. C'est
exactement le type d'incohérence pièce-vs-texte que le chantier « narration
ancrée sur le coup » visait à éliminer (comparer au bug historique « le fou
bouge, le texte parle du pion »), et il a survécu dans ce fragment précis
parce que `_frag_rook_file` ne relit jamais `intent.moved_piece` pour le
`plan`, seulement pour l'`observation`.

## 2. CREUX — texte correct mais vide de sens pédagogique

### 2.1 Finale roi+pions : un seul paragraphe, servi 7 à 8 fois sur 8 coups

Scénario « finale roi+pions » (`6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1`), sur
les 3 runs : taux `quiet` de 8/8, 7/8, 8/8. Sur ces coups `quiet`, le
paragraphe est **mot pour mot identique**, quel que soit le coup joué (coup
de roi OU de pion, Blanc OU Noir) :

> « Sans les dames, ton roi devient une pièce active. Avance-le vers le
> centre, il peut participer sans risque désormais. »

Servi tel quel pour `1. f4`, `1... Kf8`, `2. Kf1`, `2... Ke7`, `3. Ke1`,
`3... Kd6`, `4. Kd2`, `4... Kd5` (run final) — sept répétitions consécutives
détectées par le contrôle automatique #2 sur ce seul scénario.

**Pourquoi c'est creux** : c'est précisément le risque anticipé par le brief.
`fragment_library.py:_frag_endgame` (ligne 319) n'a que deux branches : un
pion passé existe, ou non. Sans pion passé, la branche « popular » est un
texte **fixe**, sans aucune des idées concrètes d'une finale de pions
(opposition, création d'un pion passé, zugzwang, quel roi est en avance,
quelle case-clé viser). Contrairement aux fragments d'intention
(`_frag_reposition`, `_frag_develop`, `_frag_castle`) qui utilisent
`_pick_variant(options, intent, ctx)` pour varier la formulation via
`ctx.recent_kinds`, **les fragments de thème (`_frag_endgame`,
`_frag_strategic`, `_frag_opening`, etc.) ne consultent jamais
`ctx.recent_kinds`** — ce n'est pas un oubli de branchement côté harnais
(`recent_kinds` est bien passé, confirmé actif sur les repositions), c'est
que la couche thème/quiet n'a tout simplement aucun mécanisme de variation.
Le repli `quiet` — celui qui, par design, doit laisser parler le thème de
position — est justement celui qui échoue le plus visiblement à le faire
dans une finale.

### 2.2 Le même défaut structurel touche aussi `STRATEGIC_ADVANTAGE` et `OPENING`

Même architecture, même symptôme, à moindre fréquence (parce que ces
scénarios génèrent plus de coups forçants qui prennent la main) :

- Scénario « finale tours+pions » (quiet 4-5/8) : le paragraphe
  `STRATEGIC_ADVANTAGE` revient à l'identique sur 3-4 coups d'affilée,
  seul le chiffre d'ampleur change (« un avantage d'environ 8.8 pions »,
  puis 9.2, puis 10.3) :
  > « Ta position est nettement meilleure, sans coup immédiat à calculer --
  > un avantage d'environ *N* pions. Par ailleurs, sans les dames, ton roi
  > devient une pièce active. Cherche à échanger les pièces quand
  > l'occasion se présente pour réduire son contre-jeu. »
- Scénario « position égale sans jeu » (quiet 2-5/6) : même schéma sur
  `OPENING` :
  > « Tes pièces mineures ne sont pas encore toutes sorties. Développe-les
  > pour pouvoir roquer, puis relie tes tours. »
  Répété à l'identique sur `1... c6`(non, coup quiet) `2. c4`, `2... g6`
  dans le run final (3 coups d'affilée).

**Pourquoi c'est creux** : même cause que 2.1 — `_frag_strategic` et
`_frag_opening` (`fragment_library.py`, mêmes lignes ~380-504) sont chacun un
texte quasi fixe par voix, sans variation ni lien avec le coup précis joué.
Ce n'est donc pas un problème isolé au fragment `ENDGAME`, mais un manque
générique de variation dans TOUTE la couche « thème de position », qui reste
le repli de tous les coups `quiet` — la seule chose qui varie est
l'insertion de faits (ampleur en pions, cause de déséquilibre), jamais la
formulation.

### 2.3 `rook_file` n'a aucune variante (contrairement à `reposition`)

Le `plan` de `_frag_rook_file` est un texte unique, jamais fait varier par
`_pick_variant`, alors que ce coup revient plusieurs fois par partie (une
tour/dame prend souvent plusieurs colonnes ouvertes successives). Mineur
comparé à 2.1/2.2, mais signalé car `_frag_reposition` documente
explicitement (commentaire ligne 971) avoir ajouté des variantes pour ce
type de répétition — `rook_file` ne l'a pas reçu.

### Ce qui est correct par conception (pas une faute)

`_frag_reposition` (fragment_library.py:966) refuse délibérément tout
jugement sur la case d'arrivée (« pas de mieux/meilleur/bon poste ») car
« on ne peut pas affirmer POURQUOI sans détecteur dédié » — c'est cohérent
avec la règle « aucun fait inventé » du projet, et donc **pas** un cas creux
au sens fautif : c'est un choix de prudence documenté. Mais il confirme,
en creux, qu'il n'existe aujourd'hui aucun détecteur pour les idées de
finale (opposition, activation du roi, pion passé) — ce sont elles qui
manquent, pas la prudence de `reposition`.

## 3. Taux de `quiet` résiduel par type de position

| Scénario | Run 1 | Run 2 | Run 3 (retenu) |
|---|---|---|---|
| finale roi+pions | 8/8 | 7/8 | 8/8 |
| finale tours+pions | 0/1 (mat immédiat, FEN à corriger) | 2/8 | 5/8 |
| finale de dames | 0/1 (mat immédiat) | 1/6 | 2/6 |
| position fermée | 1/10 | 1/10 | 1/10 |
| défense sous attaque | 1/8 | 1/8 | 1/8 |
| position égale sans jeu | 3/6 | 2/6 | 3/6 |

La finale roi+pions retombe massivement en `quiet` (7-8/8), exactement
l'hypothèse du brief — et c'est là que la faiblesse 2.1 frappe le plus fort
puisque `quiet` y est la norme, pas l'exception.

## 4. Répétitions (paragraphe identique au coup précédent)

Toutes détectées par le contrôle automatique #2 du harnais (liste complète
dans `audit_scenarios_report.txt`, section FAUTES). Concentrées presque
exclusivement dans la finale roi+pions (6-7 répétitions consécutives par
run) ; 1-2 répétitions isolées ailleurs (finale tours+pions, position égale
sans jeu), cohérent avec 2.2.

## Décompte

- **FAUX** : 1 type de faute (attribution de pièce incorrecte dans
  `rook_file`), constatée sur 4 coups distincts, 2 scénarios, les 3 runs.
- **CREUX** : 3 occurrences du même défaut architectural (fragments de thème
  sans variation ni lien au coup : `ENDGAME`, `STRATEGIC_ADVANTAGE`,
  `OPENING`) + 1 défaut mineur isolé (`rook_file` sans variante).

## Pistes (à trancher avec l'utilisateur, rien n'a été corrigé en aveugle)

1. `_frag_rook_file` : lire `intent.moved_piece` dans le `plan` comme c'est
   déjà fait dans l'`observation`, au lieu de « les tours » en dur. Correction
   ciblée, faible risque.
2. Le vrai chantier, plus gros : donner à la couche thème/`quiet` un
   mécanisme de variation (au moins `_pick_variant` branché sur
   `ctx.recent_kinds`, comme les fragments d'intention), et/ou des
   détecteurs de finale dédiés (`KING_ACTIVATION`, `PASSED_PAWN`,
   opposition) pour que `ENDGAME` cesse d'être un texte figé quand `quiet`
   domine à ce point. C'est un lot à part entière, pas une correction de
   fragment isolée.
