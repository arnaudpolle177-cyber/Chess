"""
board_reader.py
Lit le plateau à l'écran en temps réel et produit un FEN.
"""
import os
import cv2
import numpy as np
from capture_utils import capture_region, load_board_config
from template_builder import split_into_squares, load_templates

# Si la différence moyenne avec la case vide de référence est en-dessous de
# ce seuil (0-255), on considère la case vide sans même tenter de matcher
# une pièce dessus.
EMPTY_DIFF_THRESHOLD = 12


def best_match(square_img, templates):
    """
    Compare une case capturée (déjà "nettoyée" de son arrière-plan) à tous
    les templates de pièces connus, retourne la clé du meilleur match
    ("piece_r_black", "piece_q_white", etc.) et le score de confiance.
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

    Retourne (grid, min_score, debug_info) :
    - min_score   : confiance minimum observée sur l'ensemble du plateau
    - debug_info  : dict {"image": <capture brute>, "squares": [...]}
                     utile pour sauvegarder un rapport de diagnostic quand
                     la position lue est invalide (voir save_debug_capture).

    La reconnaissance des pièces se fait après soustraction de
    l'arrière-plan (case claire ou sombre) : ainsi, une pièce est reconnue
    de la même façon quelle que soit la couleur de la case sur laquelle
    elle se trouve, y compris après plusieurs coups.
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Pas de calibration. Lance la calibration d'abord.")

    templates = load_templates()
    if not templates:
        raise RuntimeError("Pas de templates. Lance l'apprentissage des pièces d'abord.")

    empty_bg = {
        "light": templates.get("empty_light"),
        "dark": templates.get("empty_dark"),
    }
    if empty_bg["light"] is None or empty_bg["dark"] is None:
        raise RuntimeError(
            "Templates de cases vides manquants. Relance l'apprentissage des pièces "
            "(les anciens templates ne sont plus compatibles)."
        )

    piece_templates = {k: v for k, v in templates.items() if k.startswith("piece_")}

    img = capture_region(config)
    squares = split_into_squares(img)

    grid = [["." for _ in range(8)] for _ in range(8)]
    min_score = 1.0
    debug_squares = []

    for (row, col), square_img in squares.items():
        color_key = "light" if (row + col) % 2 == 0 else "dark"
        h, w = square_img.shape[:2]
        bg_resized = cv2.resize(empty_bg[color_key], (w, h))
        silhouette = cv2.absdiff(square_img, bg_resized)
        diff_mean = float(silhouette.mean())

        if diff_mean < EMPTY_DIFF_THRESHOLD:
            grid[row][col] = "."
            debug_squares.append({
                "row": row, "col": col, "result": "empty",
                "diff_mean": diff_mean, "score": None,
            })
            continue

        key, score = best_match(silhouette, piece_templates)
        score = float(score)
        min_score = min(min_score, score)

        if key is None:
            grid[row][col] = "."
            fen_char = "."
        else:
            # key format: "piece_<lettre>_<white|black>"
            # ex: "piece_r_white" -> tour blanche -> "R"
            #     "piece_r_black" -> tour noire  -> "r"
            parts = key.split("_")
            letter = parts[1]
            color = parts[2] if len(parts) > 2 else "black"
            fen_char = letter.upper() if color == "white" else letter.lower()
            grid[row][col] = fen_char

        debug_squares.append({
            "row": row, "col": col, "result": fen_char,
            "diff_mean": diff_mean, "matched_key": key, "score": score,
        })

    debug_info = {"image": img, "squares": debug_squares}
    return grid, min_score, debug_info


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
