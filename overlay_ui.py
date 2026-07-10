"""
overlay_ui.py
Petite fenêtre toujours au premier plan qui affiche :
- les 3 meilleurs coups (multi-PV)
- l'évaluation de chacun
- une explication en langage clair pour le meilleur coup
"""

import tkinter as tk

MAX_LINES_DISPLAYED = 3


class CoachOverlay:
    def __init__(self, on_refresh_click=None, on_toggle_side_click=None):
        self.root = tk.Tk()
        self.root.title("Coach d'échecs")
        self.root.attributes("-topmost", True)
        self.root.geometry("380x380+40+40")
        self.root.configure(bg="#1e1e2e")

        title = tk.Label(
            self.root, text="♟ Coach d'échecs", fg="white", bg="#1e1e2e",
            font=("Arial", 14, "bold")
        )
        title.pack(pady=(10, 5))

        # --- Zone des meilleurs coups (multi-PV) ---
        self.lines_frame = tk.Frame(self.root, bg="#1e1e2e")
        self.lines_frame.pack(pady=(2, 6), padx=8, fill="x")

        self.line_labels = []
        line_colors = ["#a6e3a1", "#89dceb", "#cba6f7"]  # 1er, 2e, 3e coup
        for i in range(MAX_LINES_DISPLAYED):
            row = tk.Frame(self.lines_frame, bg="#313244")
            row.pack(fill="x", pady=2)

            move_lbl = tk.Label(
                row, text="—", fg=line_colors[i], bg="#313244",
                font=("Arial", 12, "bold"), width=10, anchor="w"
            )
            move_lbl.pack(side="left", padx=(6, 4), pady=4)

            score_lbl = tk.Label(
                row, text="—", fg="#f9e2af", bg="#313244",
                font=("Arial", 10, "bold"), width=8, anchor="w"
            )
            score_lbl.pack(side="left", pady=4)

            pv_lbl = tk.Label(
                row, text="—", fg="#cdd6f4", bg="#313244",
                font=("Arial", 9), anchor="w", justify="left", wraplength=200
            )
            pv_lbl.pack(side="left", padx=(4, 6), pady=4, fill="x", expand=True)

            self.line_labels.append({"move": move_lbl, "score": score_lbl, "pv": pv_lbl})

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

    def update_content(self, lines, explanation):
        """
        lines : liste de dicts (comme renvoyés par engine_analysis, triés du
        meilleur au moins bon), ex: [{"move_san": "e4", "score": "+0.35",
        "pv_san": ["e4", "e5", "Cf3"]}, ...]
        """
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

    def show_error(self, message):
        for widgets in self.line_labels:
            widgets["move"].config(text="—")
            widgets["score"].config(text="—")
            widgets["pv"].config(text="—")
        self.explanation_text.delete("1.0", tk.END)
        self.explanation_text.insert(tk.END, f"⚠ {message}")

    def run(self):
        self.root.mainloop()
