"""
capture_utils.py
Capture d'écran et calibration de la zone de l'échiquier.
"""
import json
import os
import numpy as np
import mss
import tkinter as tk

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "board_config.json")


def capture_region(region):
    """
    region: dict avec left, top, width, height
    Retourne une image numpy (BGR) de la zone capturée.
    """
    with mss.mss() as sct:
        shot = sct.grab(region)
        img = np.array(shot)  # BGRA
        return img[:, :, :3]  # on drop le canal alpha -> BGR


def save_board_config(left, top, width, height):
    config = {"left": left, "top": top, "width": width, "height": height}
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f)
    return config


def load_board_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH) as f:
        return json.load(f)


class CalibrationOverlay:
    """
    Fenêtre transparente plein écran : l'utilisateur clique-glisse
    pour dessiner un rectangle autour de l'échiquier.
    """

    def __init__(self):
        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.3)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.canvas = tk.Canvas(self.root, cursor="cross", bg="grey11")
        self.canvas.pack(fill="both", expand=True)

        self.start_x = None
        self.start_y = None
        self.rect = None
        self.result = None

        label = tk.Label(
            self.root,
            text="Clique-glisse pour entourer l'échiquier, puis relâche.\n"
            "Échap pour annuler.",
            fg="white",
            bg="black",
            font=("Arial", 16),
        )
        label.place(relx=0.5, rely=0.05, anchor="n")

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="red", width=3
        )

    def on_drag(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        x0, y0 = min(self.start_x, event.x), min(self.start_y, event.y)
        x1, y1 = max(self.start_x, event.x), max(self.start_y, event.y)
        self.result = (x0, y0, x1 - x0, y1 - y0)
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


def run_calibration():
    """Lance l'overlay de calibration et sauvegarde la config."""
    overlay = CalibrationOverlay()
    result = overlay.run()
    if result is None:
        raise RuntimeError("Calibration annulée.")
    left, top, width, height = result
    if width < 50 or height < 50:
        raise RuntimeError("Zone trop petite, réessaie.")
    return save_board_config(left, top, width, height)
