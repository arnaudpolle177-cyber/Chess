# ♟ Coach d'échecs — Installation

## 1. Télécharger
Récupère le fichier `CoachEchecs-windows.zip` depuis la page **Releases** du
dépôt GitHub, puis décompresse-le n'importe où (ex: sur le Bureau).

Le dossier contient :
- `CoachEchecs.exe` — le programme
- `stockfish.exe` — le moteur d'échecs (en réalité Berserk, déjà inclus, rien à installer)
- `opening_book.bin` — livre d'ouvertures (optionnel, si présent)

## 2. Activer le script sur ton site
Installe l'extension **Tampermonkey** dans ton navigateur (Chrome, Edge,
Firefox...), puis colle le contenu de `chess_coach_bridge.user.js` dans un
nouveau script Tampermonkey. Ce script lit directement le plateau affiché
sur ta page — pas de capture d'écran, pas de calibration.

## 3. Lancer le coach
Double-clique sur `CoachEchecs.exe` → choisis **1. Lancer le coach** dans
le menu. Une fenêtre s'ouvre avec 3 profils de jeu (Pragmatique, Tactique,
Textbook), chacun proposant son propre coup — visible directement sous
forme de flèche sur ton échiquier en ligne.

- Le **slider** en haut de la fenêtre règle le niveau (1800-2200 /
  2300-2700 / 2800-3200 Elo).
- Le bouton **⇄** change le camp pour lequel le coach donne des conseils
  (les deux joueurs peuvent utiliser le même coach, chacun de son côté).

## Astuce
Si Windows affiche un avertissement "éditeur inconnu" (normal pour un exe
non signé numériquement), clique sur "Informations complémentaires" puis
"Exécuter quand même".
