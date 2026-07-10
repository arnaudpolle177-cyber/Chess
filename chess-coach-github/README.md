# ♟ Coach d'échecs en temps réel

Un programme desktop qui lit ton échiquier à l'écran (par capture d'écran +
reconnaissance visuelle, sans toucher au code de ta page), interroge le
moteur Stockfish, et affiche le meilleur coup avec une explication dans une
petite fenêtre "coach" toujours visible.

## Installation

1. **Python 3.9+** requis.
2. Installe les dépendances :
   ```bash
   pip install -r requirements.txt
   ```
3. **Installe Stockfish** (le moteur d'échecs, gratuit et open-source) :
   - macOS : `brew install stockfish`
   - Linux : `sudo apt install stockfish` (ou `sudo dnf install stockfish`)
   - Windows : télécharge le binaire sur https://stockfishchess.org/download/
     et note le chemin vers `stockfish.exe`

## Utilisation

### Étape 1 — Calibration (une seule fois, ou si tu changes de fenêtre/taille)
```bash
python main.py --calibrate
```
Un voile semi-transparent apparaît sur tout l'écran. Clique-glisse un
rectangle qui entoure **exactement** l'échiquier (les 8x8 cases, sans les
bordures/coordonnées si possible). Relâche pour valider.

### Étape 2 — Apprentissage des pièces (une seule fois par thème/style de pièces)
Affiche la partie en **position de départ standard** sur ton site, puis :
```bash
python main.py --learn
```
Le programme capture la position et apprend automatiquement à quoi
ressemble chaque pièce. Si tu changes le thème visuel des pièces sur ton
site plus tard, relance cette étape.

### Étape 3 — Lancer le coach
```bash
python main.py --stockfish /chemin/vers/stockfish
```
(ou définis la variable d'environnement `STOCKFISH_PATH`, ou mets
`stockfish` dans ton PATH système et omets l'option)

La fenêtre coach apparaît en haut à gauche de l'écran, toujours au premier
plan, et se rafraîchit automatiquement toutes les 3 secondes (réglable avec
`--interval`).

- Bouton **🔄 Rafraîchir** : relance l'analyse immédiatement
- Bouton **⇄ Changer de camp** : bascule entre "meilleur coup pour les
  Blancs" et "meilleur coup pour les Noirs" (le programme ne devine pas
  automatiquement à qui c'est le tour, tu le lui dis)

## Mode d'explication

Par défaut (`--explain-mode local`), les explications sont générées par des
règles simples (capture, échec, développement, contrôle du centre...) —
gratuit et fonctionne hors-ligne.

Pour des explications plus riches et pédagogiques, utilise le mode API :
```bash
export ANTHROPIC_API_KEY="ta_clé_api"
python main.py --explain-mode api
```
(nécessite un compte sur https://console.anthropic.com et une clé API —
facturé à l'usage)

## Limites connues

- Ne détecte pas automatiquement les roques possibles, la prise en passant,
  ni le nombre de coups depuis la dernière capture — l'analyse Stockfish
  reste très bonne malgré tout, ces infos affinent surtout la fin de partie.
- Si l'échiquier bouge, se redimensionne, ou change de thème, il faut
  recalibrer / réapprendre les pièces.
- La reconnaissance suppose un plateau vu de face, sans rotation ni
  perspective (donc pas de mode "plateau 3D").

## Compiler en .exe pour tes amis (automatique via GitHub Actions)

Ce dépôt inclut `.github/workflows/build.yml`. Pour publier une version prête
à l'emploi :

1. Pousse ce projet sur un dépôt GitHub (public ou privé, peu importe).
2. Crée un tag de version et pousse-le :
   ```bash
   git tag v1.0
   git push origin v1.0
   ```
3. GitHub Actions compile automatiquement `main.py` en `CoachEchecs.exe`
   (via PyInstaller), télécharge la dernière version de Stockfish pour
   Windows, et publie une **Release** avec un fichier
   `CoachEchecs-windows.zip` contenant tout ce qu'il faut.
4. Envoie le lien de cette Release à tes amis — ils suivent
   `README_UTILISATEUR.md` (aucune installation de Python requise de leur
   côté).

Tu peux aussi déclencher un build sans tag depuis l'onglet **Actions** du
dépôt (bouton "Run workflow"), le zip sera alors disponible en tant
qu'artifact du run.

## Structure du projet

```
chess-coach/
├── main.py                # point d'entrée
├── capture_utils.py        # capture écran + calibration
├── template_builder.py     # apprentissage des pièces
├── board_reader.py         # reconnaissance + génération FEN
├── engine_analysis.py      # intégration Stockfish
├── explain.py               # génération des explications
├── overlay_ui.py            # fenêtre coach (Tkinter)
├── requirements.txt
└── templates/                # images des pièces apprises (généré)
```
