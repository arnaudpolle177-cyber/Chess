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
import time
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


# Une pièce réelle couvre typiquement entre ~3% et ~80% de la surface de
# sa case (les petites pièces comme le pion ou certains rendus du roi
# peuvent descendre assez bas). En dehors de cette plage : soit du bruit
# épars (quelques pixels isolés), soit une teinte uniforme (surlignage du
# dernier coup, highlight de sélection, etc.) -> on considère alors la
# case comme vide.
MIN_PIECE_BLOB_RATIO = 0.03
MAX_PIECE_BLOB_RATIO = 0.80


def keep_largest_component(mask):
    """
    Ne garde que le plus gros "blob" connecté du masque, et calcule sa
    proportion de la surface totale. Une vraie pièce forme un blob compact
    assez centré (typiquement 15-70% de la case). Un surlignage de case
    (teinte uniforme appliquée par certains sites sur la case de départ/
    arrivée du dernier coup) colore au contraire TOUTE la case de façon
    homogène -> soit un blob qui couvre presque 100% de la case, soit du
    bruit épars sans blob cohérent. Dans les deux cas, ce n'est pas une
    pièce, et on veut pouvoir le détecter pour l'ignorer.
    """
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return mask, 0.0  # aucun pixel de premier plan du tout

    areas = stats[1:, cv2.CC_STAT_AREA]  # on saute le label 0 (fond)
    largest_label = 1 + int(np.argmax(areas))
    largest_area = int(areas.max())

    clean = np.zeros_like(mask)
    clean[labels == largest_label] = 255

    ratio = largest_area / mask.size
    return clean, ratio


def compute_shape_mask(square_img, bg_img):
    """
    Calcule un masque binaire (0/255, un seul canal) de la silhouette de
    la pièce présente sur square_img, indépendant de la couleur de la case
    (bg_img = référence de la case vide, même couleur claire/sombre).

    Retourne (mask, blob_ratio, looks_like_piece) :
    - mask              : masque du plus gros blob connecté
    - blob_ratio        : proportion de la case couverte par ce blob (0-1)
    - looks_like_piece  : False si le blob ne ressemble pas à une vraie
                           pièce (trop petit = bruit, trop grand = teinte
                           uniforme genre surlignage du dernier coup)
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

    mask, blob_ratio = keep_largest_component(mask)
    looks_like_piece = MIN_PIECE_BLOB_RATIO <= blob_ratio <= MAX_PIECE_BLOB_RATIO
    return mask, blob_ratio, looks_like_piece


def foreground_brightness(square_img, mask):
    """
    Luminosité moyenne des pixels du COEUR de la pièce (pas de la case,
    et pas non plus des pixels de contour). On érode le masque avant
    l'échantillonnage pour exclure les pixels de bordure où l'anti-
    aliasing mélange la couleur de la pièce avec celle du fond -> ces
    pixels flous tirent la luminosité vers une valeur intermédiaire et
    brouillent la distinction blanc/noir (une pièce blanche pouvait
    ressortir avec une luminosité aussi basse que ~70 à cause d'eux,
    la faisant classer à tort comme pièce noire).
    """
    gray = cv2.cvtColor(square_img, cv2.COLOR_BGR2GRAY)

    kernel = np.ones((3, 3), np.uint8)
    core_mask = cv2.erode(mask, kernel, iterations=1)
    # Si l'érosion a tout effacé (pièce trop fine/petite), retombe sur le
    # masque complet plutôt que de n'avoir aucun pixel à mesurer.
    if not np.any(core_mask == 255):
        core_mask = mask

    fg_pixels = gray[core_mask == 255]
    if fg_pixels.size == 0:
        return float(gray.mean())
    return float(fg_pixels.mean())


def build_templates_from_starting_position(num_samples=3, delay_seconds=0.4):
    """
    Capture le plateau (qui doit être affiché en position de départ) et
    sauvegarde :
    - un masque de forme par type de pièce (shape_p.png ... shape_k.png)
    - des références de couleur (blanc/noir) dans color_ref.json, à la
      fois globales et détaillées par type de pièce
    - les 2 références de case vide (empty_light.png / empty_dark.png)

    num_samples > 1 : capture PLUSIEURS fois la position de départ (avec
    une petite pause entre chaque) et fusionne les résultats. Ça lisse le
    bruit d'une capture unique (compression, léger scintillement de rendu,
    animation résiduelle) et donne des templates plus fiables dès le
    départ, plutôt que de dépendre d'un seul instantané qui pourrait être
    légèrement imparfait.
    """
    config = load_board_config()
    if config is None:
        raise RuntimeError("Aucune calibration trouvée. Lance la calibration d'abord.")

    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    # --- Cases vides (claire + sombre), sur le tout premier échantillon ---
    img = capture_region(config)
    squares = split_into_squares(img)

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

    # --- Masques de forme + luminosités, fusionnés sur tous les échantillons ---
    shape_masks = {letter: None for letter in PIECE_LETTERS}
    brightness_white, brightness_black = [], []
    brightness_by_piece = {letter: {"white": [], "black": []} for letter in PIECE_LETTERS}
    seen_letters = set()

    for sample_idx in range(num_samples):
        sample_img = img if sample_idx == 0 else capture_region(config)
        sample_squares = split_into_squares(sample_img)

        for (row, col), square_img in sample_squares.items():
            piece = STARTING_POSITION[row][col]
            if piece == ".":
                continue

            color_key = "light" if (row + col) % 2 == 0 else "dark"
            mask, blob_ratio, _ = compute_shape_mask(square_img, empty_bg[color_key])
            letter = piece.lower()

            if shape_masks[letter] is None:
                shape_masks[letter] = mask
            else:
                # Fusionne avec toutes les occurrences précédentes (couleur
                # de case différente ET/OU échantillon différent) -> masque
                # plus robuste, moins sensible aux artefacts d'une capture.
                prev = shape_masks[letter]
                prev_resized = cv2.resize(prev, (mask.shape[1], mask.shape[0]))
                shape_masks[letter] = cv2.bitwise_or(prev_resized, mask)

            brightness = foreground_brightness(square_img, mask)
            color_key_piece = "white" if piece.isupper() else "black"
            brightness_by_piece[letter][color_key_piece].append(brightness)
            if piece.isupper():
                brightness_white.append(brightness)
            else:
                brightness_black.append(brightness)

            seen_letters.add(letter)

        if sample_idx < num_samples - 1:
            time.sleep(delay_seconds)

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

    by_piece_ref = {}
    for letter, values in brightness_by_piece.items():
        if values["white"] and values["black"]:
            by_piece_ref[letter] = {
                "white": sum(values["white"]) / len(values["white"]),
                "black": sum(values["black"]) / len(values["black"]),
            }
        # Le roi et la dame n'ont qu'une seule couleur possible chacun côté
        # départ (impossible d'avoir 2 dames blanches en position de
        # départ) -> pas de référence par-pièce fiable pour eux, ils
        # utiliseront le repli sur la référence globale (classify_color
        # gère ça automatiquement).

    color_ref = {
        "white": sum(brightness_white) / len(brightness_white),
        "black": sum(brightness_black) / len(brightness_black),
        "by_piece": by_piece_ref,
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
