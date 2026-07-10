"""
template_builder.py
Apprend automatiquement à quoi ressemblent les pièces en capturant
la position de départ standard des échecs (toujours la même !).

CORRECTIF IMPORTANT (v2) :
Les templates précédents étaient des pixels bruts après simple soustraction
de l'arrière-plan (absdiff). Problème : sur les pixels de contour d'une
pièce (anti-aliasing), la couleur de la case reste partiellement mélangée
dans le pixel. Cette teinte résiduelle diffère entre une case claire et une
case sombre -> un template appris sur une case claire ne matchait plus
correctement la même pièce posée sur une case sombre (et vice-versa),
ce qui cassait la reconnaissance dès qu'une pièce changeait de couleur de
case (donc après quasiment chaque coup).

Nouvelle approche, en 2 temps, tous les deux indépendants de la couleur
de case :
1. FORME : la silhouette est binarisée (masque noir/blanc pur, seuillage
   d'Otsu) -> ne garde que "ce pixel appartient à la pièce" ou pas, sans
   aucune trace de la couleur de fond. Un seul masque de forme par type de
   pièce (pion, cavalier, fou, tour, dame, roi), fusionné à partir de
   toutes les occurrences disponibles dans la position de départ (les
   tours, cavaliers et fous apparaissent une fois sur case claire et une
   fois sur case sombre -> on combine les deux pour un masque plus robuste).
2. COULEUR : calculée séparément, à partir de la luminosité moyenne des
   pixels de la pièce elle-même (pas de la case) -> une pièce blanche est
   claire, une pièce noire est sombre, peu importe la case en dessous.
"""
import json
import os
import cv2
import numpy as np
from capture_utils import capture_region, load_board_config
from app_paths import get_base_dir

TEMPLATES_DIR = os.path.join(get_base_dir(), "templates")
COLOR_REF_PATH = os.path.join(TEMPLATES_DIR, "color_ref.json")

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

PIECE_LETTERS = ["p", "n", "b", "r", "q", "k"]


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


def compute_shape_mask(square_img, bg_img):
    """
    Calcule un masque binaire (0/255, un seul canal) de la silhouette de
    la pièce présente sur square_img, indépendant de la couleur de la case
    (bg_img = référence de la case vide, même couleur claire/sombre).
    """
    h, w = square_img.shape[:2]
    bg_resized = cv2.resize(bg_img, (w, h))
    diff = cv2.absdiff(square_img, bg_resized)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Nettoie les petits résidus de bruit isolés
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def foreground_brightness(square_img, mask):
    """Luminosité moyenne des pixels de la pièce elle-même (pas de la case)."""
    gray = cv2.cvtColor(square_img, cv2.COLOR_BGR2GRAY)
    fg_pixels = gray[mask == 255]
    if fg_pixels.size == 0:
        return float(gray.mean())
    return float(fg_pixels.mean())


def build_templates_from_starting_position():
    """
    Capture le plateau (qui doit être affiché en position de départ) et
    sauvegarde :
    - un masque de forme par type de pièce (shape_p.png ... shape_k.png)
    - deux références de couleur (blanc/noir) dans color_ref.json
    - les 2 références de case vide (empty_light.png / empty_dark.png)
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Aucune calibration trouvée. Lance la calibration d'abord.")

    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    img = capture_region(config)
    squares = split_into_squares(img)

    # --- Passage 1 : cases vides (claire + sombre) ---
    empty_bg = {}
    for (row, col), square_img in squares.items():
        if STARTING_POSITION[row][col] != ".":
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

    # --- Passage 2 : masques de forme (fusionnés sur toutes les occurrences) ---
    shape_masks = {letter: None for letter in PIECE_LETTERS}
    brightness_white, brightness_black = [], []
    seen_letters = set()

    for (row, col), square_img in squares.items():
        piece = STARTING_POSITION[row][col]
        if piece == ".":
            continue

        color_key = "light" if (row + col) % 2 == 0 else "dark"
        mask = compute_shape_mask(square_img, empty_bg[color_key])
        letter = piece.lower()

        if shape_masks[letter] is None:
            shape_masks[letter] = mask
        else:
            # Fusionne avec l'occurrence précédente (ex: 2e tour, sur
            # l'autre couleur de case) -> masque plus robuste, moins
            # sensible aux artefacts de bord d'une seule capture.
            prev = shape_masks[letter]
            prev_resized = cv2.resize(prev, (mask.shape[1], mask.shape[0]))
            shape_masks[letter] = cv2.bitwise_or(prev_resized, mask)

        brightness = foreground_brightness(square_img, mask)
        if piece.isupper():
            brightness_white.append(brightness)
        else:
            brightness_black.append(brightness)

        seen_letters.add(letter)

    missing = set(PIECE_LETTERS) - seen_letters
    if missing:
        raise RuntimeError(
            f"Pièces non capturées: {missing}. "
            "Vérifie que le plateau est bien en position de départ et bien calibré."
        )

    saved_paths = []
    for letter, mask in shape_masks.items():
        path = os.path.join(TEMPLATES_DIR, f"shape_{letter}.png")
        cv2.imwrite(path, mask)
        saved_paths.append(path)

    color_ref = {
        "white": sum(brightness_white) / len(brightness_white),
        "black": sum(brightness_black) / len(brightness_black),
    }
    with open(COLOR_REF_PATH, "w") as f:
        json.dump(color_ref, f)
    saved_paths.append(COLOR_REF_PATH)

    saved_paths.append(os.path.join(TEMPLATES_DIR, "empty_light.png"))
    saved_paths.append(os.path.join(TEMPLATES_DIR, "empty_dark.png"))

    return saved_paths


def load_templates():
    """
    Charge en mémoire :
    - les masques de forme (clés "shape_p", "shape_n", ...)
    - les références de case vide (clés "empty_light", "empty_dark")
    Retourne un dict {clé: image numpy}. Vide si aucun template appris.
    """
    templates = {}
    if not os.path.isdir(TEMPLATES_DIR):
        return templates
    for fname in os.listdir(TEMPLATES_DIR):
        if not fname.endswith(".png"):
            continue
        path = os.path.join(TEMPLATES_DIR, fname)
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        key = fname.replace(".png", "")
        templates[key] = img
    return templates


def load_color_ref():
    """Charge les références de luminosité (blanc/noir) pour la classification
    de couleur des pièces. Retourne None si pas encore appris."""
    if not os.path.exists(COLOR_REF_PATH):
        return None
    with open(COLOR_REF_PATH) as f:
        return json.load(f)
