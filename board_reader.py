"""
board_reader.py
Lit le plateau à l'écran en temps réel et produit un FEN.

Reconnaissance en 2 étapes indépendantes de la couleur de la case
(voir le commentaire en tête de template_builder.py pour le détail du bug
que ça corrige) :
1. FORME : masque binaire de la case comparé aux 6 masques de forme appris
   (pion/cavalier/fou/tour/dame/roi) -> détermine le TYPE de pièce.
2. COULEUR : luminosité moyenne des pixels de la pièce comparée aux
   références blanc/noir apprises -> détermine la COULEUR de la pièce.
"""
import os
import time
import cv2
import chess
import numpy as np
from capture_utils import capture_region, load_board_config
from template_builder import (
    split_into_squares,
    load_templates,
    load_color_ref,
    compute_shape_mask,
    foreground_brightness,
    PIECE_LETTERS,
    MIN_PIECE_BLOB_RATIO,
    MAX_PIECE_BLOB_RATIO,
)

# Si la différence moyenne avec la case vide de référence est en-dessous de
# ce seuil (0-255), on considère la case vide sans même tenter de matcher
# une pièce dessus.
EMPTY_DIFF_THRESHOLD = 12


def match_shape(mask, shape_templates):
    """
    Compare un masque de forme capturé aux 6 masques de forme connus.
    Retourne (lettre du type de pièce, score de confiance 0-1).
    """
    best_letter, best_score = None, -1.0
    h, w = mask.shape[:2]

    for letter, tmpl in shape_templates.items():
        tmpl_resized = cv2.resize(tmpl, (w, h))
        result = cv2.matchTemplate(mask, tmpl_resized, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())
        if score > best_score:
            best_score = score
            best_letter = letter

    return best_letter, best_score


def classify_color(letter, brightness, color_ref):
    """
    Retourne 'white' ou 'black' selon la référence de luminosité la plus
    proche. Utilise en priorité la référence spécifique à ce TYPE de pièce
    (plus précise : certains types de pièces ont un rendu graphique un peu
    différent des autres dans certains sets), et retombe sur la référence
    globale (moyenne toutes pièces confondues) si absente ou si l'ancien
    format de color_ref est chargé (rétro-compatibilité).
    """
    by_piece = color_ref.get("by_piece", {})
    ref = by_piece.get(letter)
    if ref is None or "white" not in ref or "black" not in ref:
        ref = color_ref  # repli sur les moyennes globales

    dist_white = abs(brightness - ref["white"])
    dist_black = abs(brightness - ref["black"])
    return "white" if dist_white <= dist_black else "black"


def read_board_to_grid():
    """
    Capture l'écran et retourne (grid, min_score, debug_info) :
    - grid       : grille 8x8 avec les pièces reconnues ('.' pour case vide,
                   'P','n', etc.)
    - min_score  : confiance minimum observée (score de forme) sur tout le
                   plateau
    - debug_info : dict {"image": <capture brute>, "squares": [...]} utile
                   pour sauvegarder un rapport de diagnostic (voir
                   save_debug_capture) quand la position lue est invalide
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Pas de calibration. Lance la calibration d'abord.")

    templates = load_templates()
    if not templates:
        raise RuntimeError("Pas de templates. Lance l'apprentissage des pièces d'abord.")

    color_ref = load_color_ref()
    if color_ref is None:
        raise RuntimeError(
            "Référence de couleur manquante. Relance l'apprentissage des pièces "
            "(les anciens templates ne sont plus compatibles avec cette version)."
        )

    empty_bg = {
        "light": templates.get("empty_light"),
        "dark": templates.get("empty_dark"),
    }
    if empty_bg["light"] is None or empty_bg["dark"] is None:
        raise RuntimeError(
            "Templates de cases vides manquants. Relance l'apprentissage des pièces."
        )

    shape_templates = {
        letter: templates[f"shape_{letter}"]
        for letter in PIECE_LETTERS
        if f"shape_{letter}" in templates
    }
    if len(shape_templates) < len(PIECE_LETTERS):
        raise RuntimeError(
            "Masques de forme incomplets. Relance l'apprentissage des pièces "
            "(les anciens templates ne sont plus compatibles avec cette version)."
        )

    img = capture_region(config)
    squares = split_into_squares(img)

    grid = [["." for _ in range(8)] for _ in range(8)]
    min_score = 1.0
    debug_squares = []

    for (row, col), square_img in squares.items():
        color_key = "light" if (row + col) % 2 == 0 else "dark"
        bg = empty_bg[color_key]
        h, w = square_img.shape[:2]
        bg_resized = cv2.resize(bg, (w, h))
        diff_mean = float(cv2.absdiff(square_img, bg_resized).mean())

        if diff_mean < EMPTY_DIFF_THRESHOLD:
            grid[row][col] = "."
            debug_squares.append({
                "row": row, "col": col, "result": "empty",
                "diff_mean": diff_mean, "score": None,
            })
            continue

        mask, blob_ratio, looks_like_piece = compute_shape_mask(square_img, bg)

        if not looks_like_piece:
            # Le pixel diffère bien du fond (diff_mean au-dessus du seuil),
            # mais la forme ne ressemble pas à une pièce : soit du bruit
            # épars, soit une teinte uniforme (surlignage du dernier coup,
            # sélection de case, etc.) -> on traite la case comme vide
            # plutôt que de deviner une pièce au hasard.
            grid[row][col] = "."
            debug_squares.append({
                "row": row, "col": col, "result": "empty (rejeté: pas une forme de pièce)",
                "diff_mean": diff_mean, "blob_ratio": blob_ratio,
            })
            continue

        letter, shape_score = match_shape(mask, shape_templates)
        min_score = min(min_score, shape_score)

        brightness = foreground_brightness(square_img, mask)
        color = classify_color(letter, brightness, color_ref)

        if letter is None:
            fen_char = "."
        else:
            fen_char = letter.upper() if color == "white" else letter.lower()

        grid[row][col] = fen_char

        debug_squares.append({
            "row": row, "col": col, "result": fen_char,
            "diff_mean": diff_mean, "blob_ratio": blob_ratio,
            "shape_letter": letter, "shape_score": shape_score,
            "brightness": brightness, "color_guess": color,
        })

    debug_info = {"image": img, "squares": debug_squares}
    return grid, min_score, debug_info


def read_board_with_retries(active_color="w", max_attempts=3, delay_seconds=0.35):
    """
    Capture et lit le plateau, en RÉESSAYANT automatiquement (jusqu'à
    max_attempts fois, avec une petite pause entre les tentatives) tant que
    la position obtenue n'est pas valide aux échecs.

    Pourquoi : une capture peut tomber pile pendant l'animation de
    glissement d'une pièce, ou juste après un clic (surlignage temporaire),
    produisant une lecture incohérente sur UNE frame. Retenter quelques
    centaines de ms plus tard résout la plupart de ces cas transitoires
    sans aucune intervention de l'utilisateur.

    Retourne un dict :
    {
        "grid": ..., "min_score": ..., "debug_info": ...,
        "fen": ..., "board": chess.Board ou None, "valid": bool,
        "attempts": nombre de tentatives effectuées,
    }
    Si toutes les tentatives échouent, retourne quand même le résultat de
    la DERNIÈRE tentative (valid=False) pour permettre un diagnostic utile
    (debug_info correspond à cette dernière tentative).
    """
    last_result = None

    for attempt in range(1, max_attempts + 1):
        grid, min_score, debug_info = read_board_to_grid()
        fen = grid_to_fen(grid, active_color=active_color)

        try:
            board = chess.Board(fen)
            valid = board.is_valid()
        except ValueError:
            # FEN structurellement invalide (ex: plus de 8 colonnes sur une
            # rangée) -> ne devrait pas arriver vu comment grid_to_fen est
            # construit, mais on se protège quand même.
            board = None
            valid = False

        last_result = {
            "grid": grid, "min_score": min_score, "debug_info": debug_info,
            "fen": fen, "board": board, "valid": valid, "attempts": attempt,
        }

        if valid:
            return last_result

        if attempt < max_attempts:
            time.sleep(delay_seconds)

    return last_result


def save_debug_capture(debug_info, fen_attempted, reason=""):
    """
    Sauvegarde un rapport de diagnostic (capture d'écran + détail case par
    case) dans un dossier debug_captures/, horodaté. Appelé quand la
    position lue est invalide, pour pouvoir comprendre après coup ce qui a
    été mal reconnu, au lieu de deviner à l'aveugle.
    """
    import json
    import time
    from app_paths import get_base_dir

    debug_dir = os.path.join(get_base_dir(), "debug_captures")
    os.makedirs(debug_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    img_path = os.path.join(debug_dir, f"{stamp}_board.png")
    cv2.imwrite(img_path, debug_info["image"])

    report_path = os.path.join(debug_dir, f"{stamp}_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "reason": reason,
            "fen_attempted": fen_attempted,
            "squares": debug_info["squares"],
        }, f, indent=2, ensure_ascii=False, default=str)

    return debug_dir


def compute_castling_rights(grid):
    """
    Déduit les droits de roque encore ENVISAGEABLES à partir de la position
    actuelle : le roi et la tour concernés doivent être sur leurs cases de
    départ. C'est une approximation (on ne peut pas savoir depuis une
    simple image si le roi est déjà revenu sur sa case de départ après
    l'avoir quittée), mais elle règle un bug bien plus grave : l'ancienne
    version indiquait TOUJOURS "KQkq" (tous les roques disponibles), ce qui
    rendait la position INVALIDE aux yeux de python-chess dès que le roi ou
    une tour avait bougé -> "position illisible" en boucle même quand la
    reconnaissance visuelle était parfaite.
    grid[0] = rang 8 (haut), grid[7] = rang 1 (bas).
    """
    rights = ""
    if grid[7][4] == "K":
        if grid[7][7] == "R":
            rights += "K"
        if grid[7][0] == "R":
            rights += "Q"
    if grid[0][4] == "k":
        if grid[0][7] == "r":
            rights += "k"
        if grid[0][0] == "r":
            rights += "q"
    return rights if rights else "-"


def grid_to_fen(grid, active_color="w", castling=None, en_passant="-",
                 halfmove="0", fullmove="1"):
    """
    Convertit une grille 8x8 en chaîne FEN.
    grid[0] = rang 8 (haut), grid[7] = rang 1 (bas), comme aux échecs affichés normalement.

    castling=None (par défaut) -> déduit automatiquement des positions du
    roi et des tours (voir compute_castling_rights). Passe une valeur
    explicite pour forcer un droit de roque précis si besoin.
    """
    if castling is None:
        castling = compute_castling_rights(grid)

    fen_rows = []
    for row in grid:
        fen_row = ""
        empty_count = 0
        for cell in row:
            if cell == ".":
                empty_count += 1
            else:
                if empty_count > 0:
                    fen_row += str(empty_count)
                    empty_count = 0
                fen_row += cell
        if empty_count > 0:
            fen_row += str(empty_count)
        fen_rows.append(fen_row)

    board_part = "/".join(fen_rows)
    return f"{board_part} {active_color} {castling} {en_passant} {halfmove} {fullmove}"
