"""
overlay_ui.py
Petite fenêtre toujours au premier plan qui affiche :
- le coup recommandé
- l'évaluation
- une explication en langage clair
"""
import tkinter as tk


class CoachOverlay:
    def __init__(self, on_refresh_click=None, on_toggle_side_click=None):
        self.root = tk.Tk()
        self.root.title("Coach d'échecs")
        self.root.attributes("-topmost", True)
        self.root.geometry("360x260+40+40")
        self.root.configure(bg="#1e1e2e")

        title = tk.Label(
            self.root, text="♟ Coach d'échecs", fg="white", bg="#1e1e2e",
            font=("Arial", 14, "bold")
        )
        title.pack(pady=(10, 5))

        self.move_label = tk.Label(
            self.root, text="Coup recommandé : —", fg="#a6e3a1", bg="#1e1e2e",
            font=("Arial", 13, "bold")
        )
        self.move_label.pack(pady=2)

        self.score_label = tk.Label(
            self.root, text="Évaluation : —", fg="#f9e2af", bg="#1e1e2e",
            font=("Arial", 11)
        )
        self.score_label.pack(pady=2)

        self.pv_label = tk.Label(
            self.root, text="Suite : —", fg="#cdd6f4", bg="#1e1e2e",
            font=("Arial", 10), wraplength=330, justify="left"
        )
        self.pv_label.pack(pady=(4, 4))

        self.explanation_text = tk.Text(
            self.root, height=6, width=42, wrap="word",
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

    def update_content(self, move_san, score_str, pv_san, explanation):
        self.move_label.config(text=f"Coup recommandé : {move_san}")
        self.score_label.config(text=f"Évaluation : {score_str}")
        self.pv_label.config(text="Suite : " + " ".join(pv_san))
        self.explanation_text.delete("1.0", tk.END)
        self.explanation_text.insert(tk.END, explanation)

    def show_error(self, message):
        self.move_label.config(text="Coup recommandé : —")
        self.score_label.config(text="Évaluation : —")
        self.pv_label.config(text="Suite : —")
        self.explanation_text.delete("1.0", tk.END)
        self.explanation_text.insert(tk.END, f"⚠ {message}")

    def run(self):
        self.root.mainloop()
