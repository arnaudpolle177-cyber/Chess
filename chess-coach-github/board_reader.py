"""
board_reader.py
Lit le plateau à l'écran en temps réel et produit un FEN.
"""
import cv2
import numpy as np
from capture_utils import capture_region, load_board_config
from template_builder import split_into_squares, load_templates


def best_match(square_img, templates):
    """
    Compare une case capturée à tous les templates connus,
    retourne la clé du meilleur match ("piece_P", "empty_light", etc.)
    """
    best_key, best_score = None, -1.0
    h, w = square_img.shape[:2]

    for key, tmpl in templates.items():
        tmpl_resized = cv2.resize(tmpl, (w, h))
        result = cv2.matchTemplate(square_img, tmpl_resized, cv2.TM_CCOEFF_NORMED)
        score = result.max()
        if score > best_score:
            best_score = score
            best_key = key

    return best_key, best_score


def read_board_to_grid(min_confidence=0.45):
    """
    Capture l'écran et retourne une grille 8x8 (liste de listes)
    avec les pièces reconnues ('.' pour case vide, 'P','n', etc.)
    Retourne aussi le score de confiance minimum observé (utile pour debug).
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Pas de calibration. Lance la calibration d'abord.")

    templates = load_templates()
    if not templates:
        raise RuntimeError("Pas de templates. Lance l'apprentissage des pièces d'abord.")

    img = capture_region(config)
    squares = split_into_squares(img)

    grid = [["." for _ in range(8)] for _ in range(8)]
    min_score = 1.0

    for (row, col), square_img in squares.items():
        key, score = best_match(square_img, templates)
        min_score = min(min_score, score)

        if key is None or key.startswith("empty"):
            grid[row][col] = "."
        else:
            # key format: "piece_X"
            grid[row][col] = key.split("_")[1]

    return grid, min_score


def grid_to_fen(grid, active_color="w", castling="KQkq", en_passant="-",
                 halfmove="0", fullmove="1"):
    """
    Convertit une grille 8x8 en chaîne FEN.
    grid[0] = rang 8 (haut), grid[7] = rang 1 (bas), comme aux échecs affichés normalement.
    """
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
