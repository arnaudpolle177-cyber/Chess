"""
board_overlay.py
Overlay transparent affiché PAR-DESSUS l'échiquier à l'écran (à l'endroit
calibré) : entoure la pièce à jouer et sa case de destination, avec une
couleur par coup proposé (1er, 2e, 3e meilleur coup en multi-PV).

Repose sur "-transparentcolor", une fonctionnalité Tkinter spécifique à
Windows : les pixels de cette couleur deviennent invisibles ET laissent
passer les clics de souris vers la fenêtre en dessous (le site d'échecs).
Seuls les traits des cercles eux-mêmes (quelques pixels) capturent le clic
à cet endroit précis.
"""
import tkinter as tk

# Couleur "trou" : transparente + cliquable au travers. Choisie pour ne
# jamais être utilisée ailleurs dans le dessin (cercles, traits...).
TRANSPARENT_KEY = "#0a0a0a"

# Une couleur par rang de coup : 1er (vert), 2e (cyan), 3e (magenta)
LINE_COLORS = ["#39ff14", "#00d9ff", "#ff2fd6"]

FILES = "abcdefgh"


class BoardOverlay:
    def __init__(self, master, board_region):
        """
        master       : le root Tk de CoachOverlay (on crée un Toplevel enfant,
                        donc pas de 2e mainloop, pas de souci de thread).
        board_region : dict {"left", "top", "width", "height"} tel que
                        renvoyé par capture_utils.load_board_config().
        """
        self.region = board_region

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)  # pas de barre de titre / bordure
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            # -transparentcolor n'existe que sous Windows. Sur un autre OS,
            # l'overlay reste visible mais opaque (dégradé, pas un crash).
            pass

        self.win.geometry(
            f"{board_region['width']}x{board_region['height']}"
            f"+{board_region['left']}+{board_region['top']}"
        )

        self.canvas = tk.Canvas(
            self.win,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            width=board_region["width"],
            height=board_region["height"],
        )
        self.canvas.pack(fill="both", expand=True)

    def _square_center(self, square):
        """
        square: ex. 'e4' -> (x, y, taille_case) en pixels dans le canvas.
        Convention identique à tout le reste du projet : rangée 0 = rang 8
        (voir template_builder.STARTING_POSITION / board_reader).
        """
        file_idx = FILES.index(square[0])
        rank = int(square[1])
        row = 8 - rank
        col = file_idx
        cell_w = self.region["width"] / 8
        cell_h = self.region["height"] / 8
        x = col * cell_w + cell_w / 2
        y = row * cell_h + cell_h / 2
        return x, y, min(cell_w, cell_h)

    def update_moves(self, lines):
        """
        lines : liste de dicts (comme renvoyés par engine_analysis), avec au
        moins la clé 'move_uci' (ex: 'e2e4'). Triée du meilleur coup au
        moins bon. On affiche les 3 premiers, un jeu de couleur chacun.
        """
        self.canvas.delete("all")
        for i, entry in enumerate(lines[: len(LINE_COLORS)]):
            move_uci = entry.get("move_uci", "")
            if len(move_uci) < 4:
                continue
            from_sq, to_sq = move_uci[0:2], move_uci[2:4]
            color = LINE_COLORS[i]
            width = 5 if i == 0 else 3

            for sq in (from_sq, to_sq):
                x, y, size = self._square_center(sq)
                r = size * 0.38
                self.canvas.create_oval(
                    x - r, y - r, x + r, y + r, outline=color, width=width
                )

            # Petit trait pointillé reliant départ -> arrivée, même couleur,
            # pour lever toute ambiguïté sur le sens du coup.
            fx, fy, _ = self._square_center(from_sq)
            tx, ty, _ = self._square_center(to_sq)
            self.canvas.create_line(
                fx, fy, tx, ty, fill=color, width=2, dash=(4, 3)
            )

    def clear(self):
        self.canvas.delete("all")

    def destroy(self):
        self.win.destroy()
