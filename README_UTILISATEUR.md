# ♟ Coach d'échecs — Installation (2 minutes, aucun code requis)

## 1. Télécharger
Récupère le fichier `CoachEchecs-windows.zip` depuis la page **Releases** du
dépôt GitHub, puis décompresse-le n'importe où (ex: sur le Bureau).

Le dossier contient :
- `CoachEchecs.exe` — le programme
- `stockfish.exe` — le moteur d'échecs (déjà inclus, rien à installer)

## 2. Premier lancement — Calibration
Ouvre ton échiquier en ligne (ta partie doit être visible à l'écran), puis
double-clique sur `CoachEchecs.exe` et tape dans la fenêtre qui s'ouvre :

```
CoachEchecs.exe --calibrate
```

Un voile apparaît sur l'écran : clique-glisse un rectangle exactement autour
des 8x8 cases de l'échiquier, puis relâche.

## 3. Apprentissage des pièces (une seule fois)
Remets l'échiquier en position de départ, puis :

```
CoachEchecs.exe --learn
```

## 4. Lancer le coach
```
CoachEchecs.exe
```

La fenêtre "Coach d'échecs" apparaît en haut à gauche et te montre le
meilleur coup pour le camp actif. Utilise le bouton **⇄ Changer de camp**
pour voir le conseil de l'autre côté — le coach est le même pour les deux
joueurs, à vous de décider ensemble comment vous l'utilisez pendant vos
parties (pendant le coup, après le coup, etc.).

## Astuce
Si Windows affiche un avertissement "éditeur inconnu" (normal pour un exe
non signé numériquement), clique sur "Informations complémentaires" puis
"Exécuter quand même".

## Si le plateau change de thème ou de taille
Relance simplement les étapes 2 et 3.
