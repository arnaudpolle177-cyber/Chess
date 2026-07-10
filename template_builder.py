"""
template_builder.py
Apprend automatiquement à quoi ressemblent les pièces en capturant
la position de départ standard des échecs (toujours la même !).
Sauvegarde une image "template" par type de pièce dans templates/.
"""
import os
import cv2
import numpy as np
from capture_utils import capture_region, load_board_config
from app_paths import get_base_dir

TEMPLATES_DIR = os.path.join(get_base_dir(), "templates")

# Position de départ standard, rangée par rangée (rang 8 en haut -> rang 1 en bas)
# Majuscule = blanc, minuscule = noir, '.' = case vide
STARTING_POSITION = [
    list("rnbqkbnr"),
    list("pppppppp"),
    list("........"),
    list("........"),
    list("........"),
    list("........"),
    list("PPPPPPPP"),
    list("RNBQKBNR"),
]


def split_into_squares(board_img):
    """Découpe l'image du plateau (8x8) en 64 sous-images carrées."""
    h, w = board_img.shape[:2]
    sq_h, sq_w = h // 8, w // 8
    squares = {}
    for row in range(8):
        for col in range(8):
            y0, y1 = row * sq_h, (row + 1) * sq_h
            x0, x1 = col * sq_w, (col + 1) * sq_w
            squares[(row, col)] = board_img[y0:y1, x0:x1]
    return squares


def build_templates_from_starting_position():
    """
    Capture le plateau (qui doit être affiché en position de départ)
    et sauvegarde un template par pièce + 2 templates de cases vides
    (case claire vide et case sombre vide).
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Aucune calibration trouvée. Lance la calibration d'abord.")

    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    img = capture_region(config)
    squares = split_into_squares(img)

    saved = set()
    empty_light_saved = False
    empty_dark_saved = False

    for (row, col), square_img in squares.items():
        piece = STARTING_POSITION[row][col]
        is_light_square = (row + col) % 2 == 0

        if piece == ".":
            # Sauvegarde un exemple de case vide claire et une sombre
            if is_light_square and not empty_light_saved:
                cv2.imwrite(os.path.join(TEMPLATES_DIR, "empty_light.png"), square_img)
                empty_light_saved = True
            elif not is_light_square and not empty_dark_saved:
                cv2.imwrite(os.path.join(TEMPLATES_DIR, "empty_dark.png"), square_img)
                empty_dark_saved = True
        else:
            if piece not in saved:
                cv2.imwrite(os.path.join(TEMPLATES_DIR, f"piece_{piece}.png"), square_img)
                saved.add(piece)

    missing = set("rnbqkpRNBQKP") - saved
    if missing:
        raise RuntimeError(
            f"Pièces non capturées: {missing}. "
            "Vérifie que le plateau est bien en position de départ et bien calibré."
        )

    return list(TEMPLATES_DIR + f"/{f}" for f in os.listdir(TEMPLATES_DIR))


def load_templates():
    """Charge tous les templates sauvegardés en mémoire."""
    templates = {}
    if not os.path.isdir(TEMPLATES_DIR):
        return templates
    for fname in os.listdir(TEMPLATES_DIR):
        path = os.path.join(TEMPLATES_DIR, fname)
        img = cv2.imread(path)
        if img is None:
            continue
        key = fname.replace(".png", "")
        templates[key] = img
    return templates
