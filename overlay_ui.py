"""
overlay_ui.py
Petite fenêtre toujours au premier plan qui affiche :
- les 3 meilleurs coups (multi-PV)
- l'évaluation de chacun
- une explication en langage clair pour le meilleur coup
"""

import tkinter as tk
import queue

from board_overlay import BoardOverlay
from engine_analysis import PROGRESSIVE_DEPTHS
import human_profile

MAX_LINES_DISPLAYED = 3


class CoachOverlay:
    # Couleurs alignées sur PROFILE_STYLE dans chess_coach_bridge.user.js,
    # dans l'ORDRE de human_profile.PROFILE_IDS (popular, creative,
    # classical) -- bleu, rose, blanc.
    PROFILE_COLORS = ["#89dceb", "#f38ba8", "#f5f5f5"]
    PROFILE_LABELS = {"popular": "Pragmatique", "creative": "Tactique", "classical": "Textbook"}

    def __init__(self, on_refresh_click=None, on_toggle_side_click=None, on_elo_change=None, board_region=None):
        self.root = tk.Tk()
        self.root.title("Coach d'échecs")
        self.root.attributes("-topmost", True)
        self.root.geometry("380x460+40+40")
        self.root.configure(bg="#1e1e2e")

        # Overlay dessiné directement sur le plateau à l'écran (cercles de
        # couleur sur la pièce à jouer + sa case de destination). Nécessite
        # que la calibration ait déjà été faite (board_region non None).
        self.board_overlay = None
        if board_region:
            try:
                self.board_overlay = BoardOverlay(self.root, board_region)
            except Exception as e:
                print(f"⚠ Overlay plateau désactivé (calibration manquante ou erreur : {e})")

        title = tk.Label(
            self.root, text="♟ Coach d'échecs", fg="white", bg="#1e1e2e",
            font=("Arial", 14, "bold")
        )
        title.pack(pady=(10, 2))

        # --- Niveau Elo (3 paliers, voir human_profile.ELO_TIERS) ---
        elo_frame = tk.Frame(self.root, bg="#1e1e2e")
        elo_frame.pack(pady=(2, 6), padx=8, fill="x")

        self.elo_value_label = tk.Label(
            elo_frame, text=f"Niveau : {human_profile.ELO_TIERS[human_profile.DEFAULT_ELO_TIER].label} Elo",
            fg="#f9e2af", bg="#1e1e2e", font=("Arial", 10, "bold")
        )
        self.elo_value_label.pack()

        self._elo_debounce_job = None

        def _on_scale_change(value):
            tier_id = int(round(float(value)))
            tier = human_profile.ELO_TIERS.get(tier_id)
            if tier:
                self.elo_value_label.config(text=f"Niveau : {tier.label} Elo")

            # Debounce : le Scale Tkinter déclenche ce callback à CHAQUE
            # valeur traversée pendant qu'on fait glisser le curseur, pas
            # seulement au relâchement. Sans ça, glisser rapidement de 1 à 3
            # pouvait lancer 2-3 recalculs complets (3 profils chacun) qui
            # se chevauchaient -- plusieurs threads martelant le moteur
            # en même temps, terrain propice aux crashs observés
            # en pratique. On annule tout recalcul déjà programmé et on en
            # reprogramme un nouveau : seul le DERNIER niveau choisi, une
            # fois le glissement stabilisé (~400ms sans changement),
            # déclenche vraiment un recalcul.
            if self._elo_debounce_job is not None:
                self.root.after_cancel(self._elo_debounce_job)

            def _fire():
                self._elo_debounce_job = None
                if on_elo_change:
                    on_elo_change(tier_id)

            self._elo_debounce_job = self.root.after(400, _fire)

        self.elo_scale = tk.Scale(
            elo_frame, from_=1, to=3, resolution=1, orient="horizontal",
            showvalue=False, bg="#1e1e2e", fg="white", troughcolor="#313244",
            highlightthickness=0, command=_on_scale_change,
        )
        self.elo_scale.set(human_profile.DEFAULT_ELO_TIER)
        self.elo_scale.pack(fill="x")

        # --- Zone des meilleurs coups (3 profils) ---
        self.lines_frame = tk.Frame(self.root, bg="#1e1e2e")
        self.lines_frame.pack(pady=(2, 6), padx=8, fill="x")

        self.line_labels = []
        profile_ids = human_profile.PROFILE_IDS
        for i in range(MAX_LINES_DISPLAYED):
            color = self.PROFILE_COLORS[i] if i < len(self.PROFILE_COLORS) else "#ffffff"
            profile_name = self.PROFILE_LABELS.get(profile_ids[i], "") if i < len(profile_ids) else ""

            row = tk.Frame(self.lines_frame, bg="#313244")
            row.pack(fill="x", pady=2)

            name_lbl = tk.Label(
                row, text=profile_name, fg=color, bg="#313244",
                font=("Arial", 8, "bold"), width=9, anchor="w"
            )
            name_lbl.pack(side="left", padx=(6, 0), pady=(4, 0), anchor="n")

            move_lbl = tk.Label(
                row, text="—", fg=color, bg="#313244",
                font=("Arial", 12, "bold"), width=8, anchor="w"
            )
            move_lbl.pack(side="left", padx=(4, 4), pady=4)

            score_lbl = tk.Label(
                row, text="—", fg="#f9e2af", bg="#313244",
                font=("Arial", 10, "bold"), width=7, anchor="w"
            )
            score_lbl.pack(side="left", pady=4)

            pv_lbl = tk.Label(
                row, text="—", fg="#cdd6f4", bg="#313244",
                font=("Arial", 9), anchor="w", justify="left", wraplength=170
            )
            pv_lbl.pack(side="left", padx=(4, 6), pady=4, fill="x", expand=True)

            self.line_labels.append({"move": move_lbl, "score": score_lbl, "pv": pv_lbl, "name": name_lbl})

        # --- Explication texte (basée sur le meilleur coup) ---
        self.explanation_text = tk.Text(
            self.root, height=6, width=44, wrap="word",
            bg="#313244", fg="white", font=("Arial", 10), relief="flat"
        )
        self.explanation_text.pack(pady=6, padx=8)

        btn_frame = tk.Frame(self.root, bg="#1e1e2e")
        btn_frame.pack(pady=4)

        refresh_btn = tk.Button(
            btn_frame, text="🔄 Rafraîchir", command=on_refresh_click,
            bg="#89b4fa", relief="flat"
        )
        refresh_btn.pack(side="left", padx=5)

        side_btn = tk.Button(
            btn_frame, text="⇄ Changer de camp", command=on_toggle_side_click,
            bg="#f38ba8", relief="flat"
        )
        side_btn.pack(side="left", padx=5)

        # --- File d'attente thread-safe --------------------------------
        # Le thread d'analyse ne doit JAMAIS appeler Tkinter directement
        # (ni même root.after() depuis un autre thread : ce n'est pas
        # garanti thread-safe non plus). Il dépose ici des messages, et
        # c'est le thread principal (celui du mainloop) qui les applique
        # en se relançant lui-même toutes les 100ms.
        self._queue = queue.Queue()
        self.root.after(100, self._poll_queue)

    def _poll_queue(self):
        try:
            while True:
                func, args = self._queue.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_queue)

    def update_content(self, lines, explanation):
        """
        Thread-safe : peut être appelée depuis le thread d'analyse.
        Ne touche à AUCUN widget ici — dépose juste le travail dans la queue,
        qui sera traité par le thread principal via _poll_queue().

        lines : liste de dicts (comme renvoyés par engine_analysis, triés du
        meilleur au moins bon), ex: [{"move_san": "e4", "score": "+0.35",
        "pv_san": ["e4", "e5", "Cf3"]}, ...]
        """
        self._queue.put((self._update_content_impl, (lines, explanation)))

    def _update_content_impl(self, lines, explanation):
        for i, widgets in enumerate(self.line_labels):
            if i < len(lines):
                entry = lines[i]
                rank = f"{i + 1}. {entry['move_san']}"
                widgets["move"].config(text=rank)
                widgets["score"].config(text=entry["score"])
                widgets["pv"].config(text=" ".join(entry["pv_san"]))
            else:
                widgets["move"].config(text="—")
                widgets["score"].config(text="—")
                widgets["pv"].config(text="—")

        self.explanation_text.delete("1.0", tk.END)
        self.explanation_text.insert(tk.END, explanation)

        if self.board_overlay:
            self.board_overlay.update_moves(lines)

    def update_depth_line(self, depth, entry):
        """
        Thread-safe : met à jour UNIQUEMENT la ligne correspondant à ce
        palier de profondeur (10/15/20), sans toucher aux 2 autres lignes
        déjà affichées. Contrairement à update_content() (qui écrase les 3
        lignes d'un coup), c'est ce qu'il faut utiliser en mode navigateur
        progressif : chaque palier arrive séparément, à des moments
        différents, et doit rester visible pendant que les autres arrivent.

        entry : dict {"move_san", "score", "pv_san", ...} et, uniquement
        sur le palier le plus profond, une clé "explanation" en plus.
        """
        self._queue.put((self._update_depth_line_impl, (depth, entry)))

    def _update_depth_line_impl(self, depth, entry):
        try:
            idx = PROGRESSIVE_DEPTHS.index(depth)
        except ValueError:
            return  # profondeur inconnue (ex: --depth CLI custom), rien à faire ici
        if idx >= len(self.line_labels):
            return
        widgets = self.line_labels[idx]
        widgets["move"].config(text=f"d{depth} {entry['move_san']}")
        widgets["score"].config(text=entry["score"])
        widgets["pv"].config(text=" ".join(entry["pv_san"]))

        if "explanation" in entry:
            self.explanation_text.delete("1.0", tk.END)
            self.explanation_text.insert(tk.END, entry["explanation"])

    def update_profile_line(self, profile_id, entry):
        """
        Thread-safe : met à jour UNIQUEMENT la ligne correspondant à ce
        profil ("popular"/"creative"/"classical"), sans toucher aux
        3 autres lignes déjà affichées -- même logique que
        update_depth_line() mais pour le nouveau système de profils
        "humains" (voir human_profile.py).
        """
        self._queue.put((self._update_profile_line_impl, (profile_id, entry)))

    def _update_profile_line_impl(self, profile_id, entry):
        try:
            idx = human_profile.PROFILE_IDS.index(profile_id)
        except ValueError:
            return
        if idx >= len(self.line_labels):
            return
        widgets = self.line_labels[idx]
        widgets["move"].config(text=entry["move_san"])
        widgets["score"].config(text=entry["score"])
        widgets["pv"].config(text=" ".join(entry["pv_san"]))

        if "explanation" in entry:
            self.explanation_text.delete("1.0", tk.END)
            self.explanation_text.insert(tk.END, entry["explanation"])

    def update_explanation(self, text):
        """Thread-safe : met à jour uniquement le texte d'explication, sans toucher aux lignes de coups."""
        self._queue.put((self._update_explanation_impl, (text,)))

    def _update_explanation_impl(self, text):
        self.explanation_text.delete("1.0", tk.END)
        self.explanation_text.insert(tk.END, text)

    def append_warning(self, message):
        """Thread-safe : ajoute une ligne d'avertissement à la suite de l'explication."""
        self._queue.put((self._append_warning_impl, (message,)))

    def _append_warning_impl(self, message):
        self.explanation_text.insert("end", message)

    def show_error(self, message):
        """Thread-safe : peut être appelée depuis le thread d'analyse."""
        self._queue.put((self._show_error_impl, (message,)))

    def _show_error_impl(self, message):
        for widgets in self.line_labels:
            widgets["move"].config(text="—")
            widgets["score"].config(text="—")
            widgets["pv"].config(text="—")
        self.explanation_text.delete("1.0", tk.END)
        self.explanation_text.insert(tk.END, f"⚠ {message}")

        if self.board_overlay:
            self.board_overlay.clear()

    def run(self):
        self.root.mainloop()
