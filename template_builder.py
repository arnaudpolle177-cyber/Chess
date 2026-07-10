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

    IMPORTANT : les templates de pièces sont enregistrés APRÈS avoir
    soustrait l'arrière-plan de la case (claire ou sombre) où elles ont
    été capturées. Sans ça, une pièce apprise sur une case claire (ex: la
    dame blanche sur d1) n'est plus reconnue dès qu'elle se déplace sur une
    case sombre — ce qui arrive quasi systématiquement après un seul coup,
    et fait planter la lecture du plateau au bout de quelques coups.
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Aucune calibration trouvée. Lance la calibration d'abord.")

    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    img = capture_region(config)
    squares = split_into_squares(img)

    # --- Passage 1 : cases vides (claire + sombre), servent aussi de
    # référence de fond pour extraire la silhouette des pièces ensuite.
    empty_bg = {}
    for (row, col), square_img in squares.items():
        piece = STARTING_POSITION[row][col]
        if piece != ".":
            continue
        color_key = "light" if (row + col) % 2 == 0 else "dark"
        if color_key not in empty_bg:
            empty_bg[color_key] = square_img
            cv2.imwrite(os.path.join(TEMPLATES_DIR, f"empty_{color_key}.png"), square_img)

    if "light" not in empty_bg or "dark" not in empty_bg:
        raise RuntimeError(
            "Impossible de capturer une case vide claire ET une case vide sombre. "
            "Vérifie la calibration."
        )

    # --- Passage 2 : pièces, arrière-plan soustrait.
    saved = set()
    for (row, col), square_img in squares.items():
        piece = STARTING_POSITION[row][col]
        if piece == "." or piece in saved:
            continue

        color_key = "light" if (row + col) % 2 == 0 else "dark"
        silhouette = cv2.absdiff(square_img, empty_bg[color_key])

        # IMPORTANT : on ajoute "_white"/"_black" dans le nom de fichier.
        # Sans ça, "piece_r.png" (tour noire) et "piece_R.png" (tour
        # blanche) sont considérés comme LE MÊME FICHIER sur Windows
        # (NTFS n'est pas sensible à la casse), et l'un écrase l'autre :
        # on perdait alors la moitié des templates de pièces.
        color = "white" if piece.isupper() else "black"
        fname = f"piece_{piece.lower()}_{color}.png"
        cv2.imwrite(os.path.join(TEMPLATES_DIR, fname), silhouette)
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
