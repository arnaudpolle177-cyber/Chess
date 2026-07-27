"""
webview_ui.py
Fenêtre du coach construite avec pywebview (une vraie fenêtre native, avec
du HTML/CSS/JS local dedans -- via WebView2 sur Windows) plutôt que
Tkinter. Remplace overlay_ui.py pour le mode navigateur (BrowserBridgeApp) ;
le mode capture d'écran (CoachApp, Tkinter) a été retiré du projet.

Tout le HTML/CSS/JS est embarqué directement dans ce fichier (pas de
fichier .html séparé à gérer/localiser une fois compilé en .exe), et
n'utilise AUCUNE ressource externe (pas de CDN, pas de police web) -- tout
doit continuer à fonctionner même sans connexion internet.

Le pont Python <-> JS :
- Python -> JS : self._eval_js(...) appelle des fonctions JS globales
  (window.updateProfile, window.showStatus, etc.) pour pousser les mises à
  jour, exactement comme les anciens update_profile_line()/show_error() de
  CoachOverlay.
- JS -> Python : window.pywebview.api.xxx() appelle les méthodes de _JsApi
  ci-dessous, qui délèguent directement aux callbacks fournis par
  BrowserBridgeApp (set_elo_tier, toggle_side, refresh_last_profiles) --
  aucune logique métier ici, juste un pont.
"""
import json
import threading

import chess
import chess.svg
import webview

# Labels (voir game_report.py, ALL_LABELS) pour lesquels on pré-génère un
# échiquier SVG cliquable (mode puzzle) -- se limite aux VRAIES erreurs,
# pas à tous les coups (Book/Best/Excellent/Good ne sont pas des choses à
# corriger).
_PUZZLE_LABELS = {"Inaccuracy", "Mistake", "Miss", "Blunder"}
# Taille fixe utilisée à la fois pour le rendu SVG et le mapping clic->case
# côté JS (voir window.PUZZLE_SIZE) -- coordinates=False (pas de marge de
# coordonnées autour du plateau) pour que ce mapping reste une simple
# division entière, sans cas particulier.
_PUZZLE_SVG_SIZE = 360


def _build_report_payload(report):
    """
    Convertit un rapport game_report.build_report(...) en JSON prêt pour
    window.renderReport -- ajoute un rendu chess.svg PRÉ-GÉNÉRÉ pour chaque
    coup cliquable (voir _PUZZLE_LABELS), pour ne pas avoir besoin d'un
    aller-retour JS -> Python supplémentaire à l'ouverture d'un puzzle.
    Orientation TOUJOURS du point de vue des Blancs (même pour les erreurs
    des Noirs) -- plus simple pour l'utilisateur (un seul sens de lecture)
    et pour le mapping clic->case côté JS (une seule formule, pas de cas
    "camp adverse" à gérer).
    """
    payload = {}
    for side in ("w", "b"):
        data = report[side]
        moves_payload = []
        for m in data["moves"]:
            item = {"ply": m.get("ply"), "san": m.get("san"), "label": m.get("label")}
            if (m.get("label") in _PUZZLE_LABELS
                    and m.get("fen_before") and m.get("best_move_uci")):
                try:
                    item["puzzle_svg"] = chess.svg.board(
                        chess.Board(m["fen_before"]), size=_PUZZLE_SVG_SIZE, coordinates=False)
                    item["best_move_uci"] = m["best_move_uci"]
                except Exception:
                    pass  # best-effort : le coup reste affiché, juste pas cliquable
            moves_payload.append(item)
        payload[side] = {"accuracy": data["accuracy"], "counts": data["counts"], "moves": moves_payload}
    return payload


class _JsApi:
    """Méthodes appelables depuis le JS via window.pywebview.api.xxx()."""

    def __init__(self, on_elo_change, on_toggle_side, on_refresh, on_show_report):
        self._on_elo_change = on_elo_change
        self._on_toggle_side = on_toggle_side
        self._on_refresh = on_refresh
        self._on_show_report = on_show_report

    def set_elo(self, tier_id):
        if self._on_elo_change:
            self._on_elo_change(int(tier_id))

    def toggle_side(self):
        if self._on_toggle_side:
            self._on_toggle_side()

    def refresh(self):
        if self._on_refresh:
            self._on_refresh()

    def request_report(self):
        if self._on_show_report:
            self._on_show_report()


class CoachWebview:
    def __init__(self, on_refresh_click=None, on_toggle_side_click=None, on_elo_change=None,
                 on_show_report_click=None):
        self._window = None
        self._api = _JsApi(on_elo_change, on_toggle_side_click, on_refresh_click, on_show_report_click)
        self._ready = threading.Event()

    def _on_loaded(self):
        self._ready.set()

    def run(self):
        """
        Bloquant -- à appeler depuis le thread principal, comme
        overlay.run() avant (pywebview a la même contrainte que Tkinter :
        la boucle d'événements doit tourner sur le thread principal).
        """
        self._window = webview.create_window(
            "Coach d'échecs", html=_HTML, js_api=self._api,
            width=600, height=580, resizable=True, on_top=True,
        )
        self._window.events.loaded += self._on_loaded
        webview.start()

    def _eval_js(self, js):
        if self._window is None:
            return
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass  # fenêtre pas encore prête ou déjà fermée -- best-effort, pas bloquant

    def update_profile(self, profile_id, entry):
        """
        Pousse une mise à jour pour UN SEUL profil, sans toucher aux 2
        autres -- même principe que update_profile_line() avant. `entry`
        est un dict (move_san, score, pv_san, et éventuellement
        explanation) -- pour l'instant affiché tel quel en attendant la
        vraie couche de narration (thème détecté + gabarits par profil).
        """
        self._eval_js(f"window.updateProfile({json.dumps(profile_id)}, {json.dumps(entry)})")

    def show_status(self, message):
        """Message ponctuel : au tour de l'adversaire, partie terminée, erreur, en attente..."""
        self._eval_js(f"window.showStatus({json.dumps(message)})")

    def set_camp(self, camp):
        """camp: 'w' ou 'b'."""
        self._eval_js(f"window.setCamp({json.dumps(camp)})")

    def set_elo_tier(self, tier_id):
        self._eval_js(f"window.setEloTier({int(tier_id)})")

    def show_report(self, report):
        """
        Pousse le rapport de fin de partie (voir game_report.build_report)
        à l'UI, avec un rendu chess.svg pré-généré pour chaque coup
        cliquable (voir _build_report_payload) -- ouvre directement
        l'overlay rapport côté JS.
        """
        payload = _build_report_payload(report)
        self._eval_js(f"window.renderReport({json.dumps(payload)})")


# ---------------------------------------------------------------------
# Template HTML/CSS/JS -- aucune ressource externe (tout doit marcher
# hors-ligne). Icônes en SVG inline, pas de police d'icônes chargée par
# CDN. Palette reprise du thème existant (fond sombre, mêmes teintes).
# ---------------------------------------------------------------------
_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {
    --bg: #1e1e2e;
    --surface: #262637;
    --surface-2: #313244;
    --border: #3a3a4d;
    --text: #cdd6f4;
    --text-dim: #9399b2;
    --text-faint: #6c7086;
    --accent-popular: #89dceb;
    --accent-tactical: #f38ba8;
    --accent-classical: #cdd6f4;
    --warn: #f9e2af;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100%; position: relative;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
    user-select: none;
  }
  #app { display: flex; flex-direction: column; height: 100%; padding: 16px; gap: 14px; }

  /* --- en-tête : niveau + camp --- */
  #header { display: flex; flex-direction: column; gap: 8px; }
  #elo-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  #elo-label { font-size: 12px; font-weight: 600; color: var(--warn); letter-spacing: .02em; }
  #camp-btn, #report-btn {
    display: flex; align-items: center; gap: 6px;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px;
    padding: 5px 12px; cursor: pointer; font-size: 12px; color: var(--text-dim);
    transition: border-color .15s, color .15s;
  }
  #camp-btn:hover { border-color: var(--accent-tactical); color: var(--text); }
  #camp-btn svg { width: 13px; height: 13px; }
  #report-btn:hover { border-color: var(--warn); color: var(--text); }
  #report-btn svg { width: 13px; height: 13px; }

  #elo-slider {
    -webkit-appearance: none; width: 100%; height: 4px; border-radius: 2px;
    background: var(--surface-2); outline: none; cursor: pointer;
  }
  #elo-slider::-webkit-slider-thumb {
    -webkit-appearance: none; width: 16px; height: 16px; border-radius: 50%;
    background: var(--warn); cursor: pointer; border: none;
  }

  /* --- cartes de profil --- */
  #cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 12px; cursor: pointer; transition: border-color .15s, transform .15s;
  }
  .card .card-head { display: flex; align-items: center; gap: 7px; margin-bottom: 6px; }
  .card .card-head svg { width: 16px; height: 16px; flex-shrink: 0; }
  .card .card-name { font-weight: 600; font-size: 13px; }
  .card .card-tag { font-size: 11.5px; color: var(--text-dim); line-height: 1.4; margin: 0; }
  .card[data-active="true"] { transform: translateY(-2px); }
  .card[data-profile="popular"] .card-head svg { color: var(--accent-popular); }
  .card[data-profile="creative"] .card-head svg { color: var(--accent-tactical); }
  .card[data-profile="classical"] .card-head svg { color: var(--accent-classical); }
  .card[data-profile="popular"][data-active="true"] { border-color: var(--accent-popular); }
  .card[data-profile="creative"][data-active="true"] { border-color: var(--accent-tactical); }
  .card[data-profile="classical"][data-active="true"] { border-color: var(--accent-classical); }

  /* --- panneau de détail --- */
  #detail {
    flex: 1; background: var(--surface-2); border-radius: 12px; padding: 16px 18px;
    display: flex; flex-direction: column; gap: 10px; overflow: hidden;
  }
  #detail-theme { display: flex; align-items: center; gap: 7px; }
  #detail-theme svg { width: 15px; height: 15px; }
  #detail-theme-label { font-size: 12px; font-weight: 600; letter-spacing: .02em; }
  #detail-lines { display: flex; flex-direction: column; gap: 6px; }
  #detail-lines p { margin: 0; font-size: 14.5px; line-height: 1.55; }
  #detail-lines p.secondary { color: var(--text-dim); }
  #detail-lines .block-label { font-weight: 600; color: var(--text); }
  #detail-lines p.secondary .block-label { color: var(--text-dim); }
  #detail-lines p.suite { color: var(--text-dim); font-size: 12.5px; font-style: italic; }
  #detail-lines p.caution { color: var(--warn); font-size: 12.5px; margin-top: 4px; padding-top: 8px; border-top: 1px solid var(--border); }
  #detail-opening-tag { display: none; align-items: baseline; gap: 5px; font-size: 12px; color: var(--text-dim); margin-bottom: 2px; }
  #detail-opening-tag.visible { display: flex; }
  #detail-opening-tag .eco { font-weight: 600; color: var(--text); }
  #detail-opening-tag .variation { font-style: italic; }
  #status-line { font-size: 12.5px; color: var(--text-faint); margin: 0; }

  /* --- rapport de fin de partie + puzzle --- */
  #reportOverlay {
    position: absolute; inset: 0; background: var(--bg);
    display: flex; flex-direction: column; padding: 16px; gap: 12px;
  }
  #reportPanel { display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; }
  #reportHeader { display: flex; align-items: center; justify-content: space-between; }
  #reportTitle, #puzzleTitle { font-weight: 600; font-size: 14px; }
  #reportClose { cursor: pointer; font-size: 20px; color: var(--text-dim); line-height: 1; }
  #reportClose:hover { color: var(--text); }
  #puzzleBack { cursor: pointer; font-size: 12px; color: var(--accent-popular); }
  #reportHint { font-size: 11.5px; color: var(--text-faint); margin: 0; }

  #reportTable { flex: 1; overflow-y: auto; }
  #reportTable table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  #reportTable th, #reportTable td { padding: 5px 6px; text-align: center; }
  #reportTable th { color: var(--text-dim); font-weight: 600; font-size: 11px; }
  #reportTable td.label-cell { text-align: left; color: var(--text-dim); }
  #reportTable tr.accuracy-row td { font-weight: 700; font-size: 14px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  #reportTable tr.clickable td { cursor: pointer; }
  #reportTable tr.clickable:hover td { background: var(--surface-2); }
  #reportTable tr.count-zero td.count-cell { color: var(--text-faint); }

  #puzzlePanel { flex: 1; display: flex; flex-direction: column; gap: 10px; }
  #puzzleBoardWrap { position: relative; width: 100%; max-width: 360px; margin: 0 auto; }
  #puzzleBoard svg { width: 100%; height: auto; display: block; }
  #puzzleHighlight {
    position: absolute; border: 3px solid var(--warn); box-sizing: border-box;
    pointer-events: none;
  }
  #puzzleFeedback { text-align: center; font-size: 13px; font-weight: 600; min-height: 18px; margin: 0; }
  #puzzleFeedback.correct { color: #a6e3a1; }
  #puzzleFeedback.wrong { color: var(--accent-tactical); }
</style>
</head>
<body>
<div id="app">

  <div id="header">
    <div id="elo-row">
      <span id="elo-label">Niveau : 2300-2700 Elo</span>
      <div id="camp-btn" onclick="onToggleSide()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 7h11l-3-3M17 17H6l3 3"/></svg>
        <span id="camp-label">Blancs</span>
      </div>
      <div id="report-btn" onclick="onRequestReport()" title="Rapport de la partie en cours">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 17V9M12 17V5M15 17v-4"/><rect x="3" y="3" width="18" height="18" rx="2"/></svg>
        <span>Rapport</span>
      </div>
    </div>
    <input id="elo-slider" type="range" min="1" max="3" step="1" value="2" oninput="onEloInput(this.value)">
  </div>

  <div id="cards">
    <div class="card" data-profile="popular" data-active="false" onclick="selectProfile('popular')">
      <div class="card-head">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/></svg>
        <span class="card-name">Pragmatique</span>
      </div>
      <p class="card-tag">Le résultat le plus sûr, sans détour.</p>
    </div>
    <div class="card" data-profile="creative" data-active="false" onclick="selectProfile('creative')">
      <div class="card-head">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg>
        <span class="card-name">Tactique</span>
      </div>
      <p class="card-tag">L'initiative, quitte à donner du matériel.</p>
    </div>
    <div class="card" data-profile="classical" data-active="false" onclick="selectProfile('classical')">
      <div class="card-head">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
        <span class="card-name">Textbook</span>
      </div>
      <p class="card-tag">Le principe classique du moment.</p>
    </div>
  </div>

  <div id="detail">
    <div id="detail-opening-tag"></div>
    <div id="detail-theme">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
      <span id="detail-theme-label">En attente</span>
    </div>
    <div id="detail-lines">
      <p id="status-line">En attente de ton site... vérifie que chess_coach_bridge.user.js est bien activé dans Tampermonkey.</p>
    </div>
  </div>

</div>

<div id="reportOverlay" style="display:none">
  <div id="reportPanel">
    <div id="reportHeader">
      <span id="reportTitle">Rapport de partie</span>
      <span id="reportClose" onclick="closeReport()" title="Fermer">&times;</span>
    </div>
    <div id="reportTable"></div>
    <p id="reportHint">Clique une Imprécision / Faute / Occasion manquée / Gaffe pour t'entraîner sur ce coup.</p>
  </div>
  <div id="puzzlePanel" style="display:none">
    <div id="reportHeader">
      <span id="puzzleTitle">Retrouve le meilleur coup</span>
      <span id="puzzleBack" onclick="closePuzzle()" title="Retour au rapport">&larr; Retour</span>
    </div>
    <div id="puzzleBoardWrap">
      <div id="puzzleBoard"></div>
      <div id="puzzleHighlight" style="display:none"></div>
    </div>
    <p id="puzzleFeedback"></p>
  </div>
</div>

<script>
  const PROFILE_DATA = {}; // profile_id -> dernière entry reçue
  let activeProfile = "popular";

  function onEloInput(value) {
    document.getElementById("elo-label").textContent = "Niveau : " + eloLabelFor(value) + " Elo";
    if (window.pywebview) window.pywebview.api.set_elo(value);
  }
  function eloLabelFor(v) {
    return { "1": "1800-2200", "2": "2300-2700", "3": "2800-3200" }[String(v)] || "?";
  }
  function onToggleSide() {
    if (window.pywebview) window.pywebview.api.toggle_side();
  }
  function onRequestReport() {
    if (window.pywebview) window.pywebview.api.request_report();
  }

  function selectProfile(id) {
    activeProfile = id;
    document.querySelectorAll(".card").forEach(c => {
      c.dataset.active = (c.dataset.profile === id) ? "true" : "false";
    });
    renderDetail();
  }

  function renderDetail() {
    const entry = PROFILE_DATA[activeProfile];
    const themeEl = document.getElementById("detail-theme");
    const themeLabel = document.getElementById("detail-theme-label");
    const lines = document.getElementById("detail-lines");
    const openingTagEl = document.getElementById("detail-opening-tag");

    if (!entry || !entry.narration) {
      themeEl.querySelector("svg").innerHTML = THEME_ICON_PATHS["info"];
      themeLabel.textContent = "En attente";
      lines.innerHTML = '<p id="status-line">' +
        (entry ? "Analyse en cours..." : "En attente du prochain coup...") + '</p>';
      openingTagEl.classList.remove("visible");
      openingTagEl.innerHTML = "";
      return;
    }

    const n = entry.narration;
    themeEl.querySelector("svg").innerHTML = THEME_ICON_PATHS[n.theme_icon] || THEME_ICON_PATHS["info"];
    themeLabel.textContent = n.theme_label || "";

    // Bannière ouverture/variation (voir narration.py, _opening_tag) --
    // affichée uniquement quand le thème PRINCIPAL n'est PAS déjà OPENING
    // (auquel cas le nom est déjà le contenu principal ci-dessous, pas la
    // peine de le répéter dans la bannière).
    if (n.opening_tag) {
      const t = n.opening_tag;
      let tagHtml = '<span class="eco">' + escapeHtml(t.family || "") +
        (t.eco ? ' (' + escapeHtml(t.eco) + ')' : '') + '</span>';
      if (t.variation) {
        tagHtml += '<span class="variation">' + escapeHtml(t.variation) + '</span>';
      }
      openingTagEl.innerHTML = tagHtml;
      openingTagEl.classList.add("visible");
    } else {
      openingTagEl.classList.remove("visible");
      openingTagEl.innerHTML = "";
    }

    // Narration v2 (voir narration_v2.py) : si un paragraphe tissé est fourni,
    // on l'affiche comme UNE seule pensée (2-4 phrases). Sinon, repli sur
    // l'affichage historique à 2 blocs (label1/text1 + label2/text2) -- les
    // deux chemins coexistent pendant la transition.
    let html;
    if (n.paragraph) {
      html = '<p>' + escapeHtml(n.paragraph) + '</p>';
    } else {
      html =
        '<p><span class="block-label">' + escapeHtml(n.label1 || "") + ' — </span>' + escapeHtml(n.text1 || "") + '</p>' +
        '<p class="secondary"><span class="block-label">' + escapeHtml(n.label2 || "") + ' — </span>' + escapeHtml(n.text2 || "") + '</p>';
    }
    if (n.suite) {
      html += '<p class="suite"><span class="block-label">Suite envisagée — </span>' + escapeHtml(n.suite) + '</p>';
    }
    if (n.caution) {
      html += '<p class="caution">' + escapeHtml(n.caution) + '</p>';
    }
    lines.innerHTML = html;
  }

  const THEME_ICON_PATHS = {
    alert: '<path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>',
    bolt: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/>',
    sword: '<path d="M12 2c1 3-3 4-3 8a3 3 0 0 0 6 0c0-1-1-2-1-3 2 1 3 3 3 5a5 5 0 0 1-10 0c0-5 4-6 5-10z"/>',
    shield: '<path d="M12 2 4 5v6c0 5 3.5 9 8 11 4.5-2 8-6 8-11V5z"/>',
    rewind: '<path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-2"/>',
    flag: '<path d="M4 22V4M4 4h14l-3 4 3 4H4"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    trend: '<path d="M3 17 9 11l4 4 8-8M21 7h-6M21 7v6"/>',
    scale: '<path d="M12 3v18M3 7l4-2 4 2M3 7l-1 5a3 3 0 0 0 6 0zM15 7l4-2 4 2M15 7l-1 5a3 3 0 0 0 6 0zM7 21h10"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    move: '<path d="M12 2v20M2 12h20M5 5l3 3M19 5l-3 3M5 19l3-3M19 19l-3-3"/>',
    pulse: '<path d="M2 12h4l2-7 4 14 3-10 2 3h5"/>',
    info: '<path d="M12 16v-4M12 8h.01"/><circle cx="12" cy="12" r="9"/>',
  };

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // --- Rapport de fin de partie + mode puzzle (voir game_report.py) ---
  const REPORT_LABEL_ORDER = [
    "Brilliant", "Great", "Book", "Best", "Excellent", "Good",
    "Inaccuracy", "Mistake", "Miss", "Blunder",
  ];
  const REPORT_LABEL_FR = {
    Brilliant: "Brillant", Great: "Génial", Book: "Théorie", Best: "Meilleur",
    Excellent: "Excellent", Good: "Bon", Inaccuracy: "Imprécision",
    Mistake: "Faute", Miss: "Occasion manquée", Blunder: "Gaffe",
  };
  const PUZZLE_CLICKABLE_LABELS = ["Inaccuracy", "Mistake", "Miss", "Blunder"];
  const PUZZLE_SIZE = 360;       // doit rester cohérent avec _PUZZLE_SVG_SIZE (webview_ui.py)
  const PUZZLE_SQUARE = PUZZLE_SIZE / 8;
  let lastReport = null;         // dernier payload reçu, pour retrouver le coup cliqué
  let puzzleFromSquare = null;

  function renderReportTable(report) {
    const el = document.getElementById("reportTable");
    let html = '<table>';
    html += '<tr class="accuracy-row"><td class="label-cell">Accuracy</td>' +
      '<td>' + (report.w.accuracy ?? "—") + '</td>' +
      '<td>' + (report.b.accuracy ?? "—") + '</td></tr>';
    html += '<tr><th></th><th>Blancs</th><th>Noirs</th></tr>';
    REPORT_LABEL_ORDER.forEach(label => {
      const wCount = report.w.counts[label] || 0;
      const bCount = report.b.counts[label] || 0;
      const clickable = PUZZLE_CLICKABLE_LABELS.includes(label) && (wCount > 0 || bCount > 0);
      const rowClass = (clickable ? "clickable " : "") + ((wCount + bCount) === 0 ? "count-zero" : "");
      html += `<tr class="${rowClass}" data-label="${label}">` +
        `<td class="label-cell">${REPORT_LABEL_FR[label] || label}</td>` +
        `<td class="count-cell">${wCount}</td><td class="count-cell">${bCount}</td></tr>`;
    });
    html += '</table>';
    el.innerHTML = html;

    el.querySelectorAll("tr.clickable").forEach(row => {
      row.onclick = () => openFirstPuzzleForLabel(row.dataset.label);
    });
  }

  function openFirstPuzzleForLabel(label) {
    // Ouvre le PREMIER coup de ce label trouvé (Blancs d'abord) -- un
    // sélecteur "quel coup précisément" serait plus riche mais pas
    // demandé ici ; l'utilisateur peut relancer sur le même label pour
    // s'entraîner sur cette catégorie, un coup à la fois.
    for (const side of ["w", "b"]) {
      const moves = (lastReport[side].moves || []).filter(m => m.label === label && m.puzzle_svg);
      if (moves.length > 0) {
        openPuzzle(moves[Math.floor(Math.random() * moves.length)]);
        return;
      }
    }
  }

  function openPuzzle(moveData) {
    document.getElementById("reportPanel").style.display = "none";
    document.getElementById("puzzlePanel").style.display = "flex";
    document.getElementById("puzzleTitle").textContent =
      "Retrouve le meilleur coup (au lieu de " + (moveData.san || "?") + ")";
    document.getElementById("puzzleBoard").innerHTML = moveData.puzzle_svg;
    document.getElementById("puzzleFeedback").textContent = "";
    document.getElementById("puzzleFeedback").className = "";
    document.getElementById("puzzleHighlight").style.display = "none";
    puzzleFromSquare = null;

    const boardEl = document.getElementById("puzzleBoard");
    boardEl.onclick = (ev) => onPuzzleBoardClick(ev, moveData);
  }

  function squareFromClick(ev, boardEl) {
    // coordinates=False côté chess.svg.board (voir _PUZZLE_SVG_SIZE) : le
    // SVG est une grille 8x8 pleine, sans marge -- mapping direct, aucun
    // cas particulier à gérer. Orientation TOUJOURS Blancs (voir
    // _build_report_payload) : file 0 = colonne a, rang du haut = 8.
    const rect = boardEl.getBoundingClientRect();
    const scale = PUZZLE_SIZE / rect.width;
    const x = (ev.clientX - rect.left) * scale;
    const y = (ev.clientY - rect.top) * scale;
    const file = Math.min(7, Math.max(0, Math.floor(x / PUZZLE_SQUARE)));
    const rankFromTop = Math.min(7, Math.max(0, Math.floor(y / PUZZLE_SQUARE)));
    const square = "abcdefgh"[file] + (8 - rankFromTop);
    return { square, file, rankFromTop, rectWidth: rect.width };
  }

  function onPuzzleBoardClick(ev, moveData) {
    const boardEl = document.getElementById("puzzleBoard");
    const { square, file, rankFromTop, rectWidth } = squareFromClick(ev, boardEl);
    const highlight = document.getElementById("puzzleHighlight");
    const px = rectWidth / 8;
    highlight.style.display = "block";
    highlight.style.width = px + "px";
    highlight.style.height = px + "px";
    highlight.style.left = (file * px) + "px";
    highlight.style.top = (rankFromTop * px) + "px";

    if (puzzleFromSquare === null) {
      puzzleFromSquare = square;
      return;
    }
    const guess = puzzleFromSquare + square;
    puzzleFromSquare = null;
    highlight.style.display = "none";
    // Promotion : auto-dame par défaut (voir game_report.py -- ponytail,
    // pas de sélecteur de promotion pour du contenu d'entraînement).
    const isCorrect = guess === moveData.best_move_uci || (guess + "q") === moveData.best_move_uci;
    const feedback = document.getElementById("puzzleFeedback");
    feedback.className = isCorrect ? "correct" : "wrong";
    feedback.textContent = isCorrect
      ? "Bien vu !"
      : "Pas encore -- réessaie (clique la case de départ).";
  }

  function closePuzzle() {
    document.getElementById("puzzlePanel").style.display = "none";
    document.getElementById("reportPanel").style.display = "flex";
  }

  function closeReport() {
    document.getElementById("reportOverlay").style.display = "none";
  }

  // --- Appelées depuis Python (voir webview_ui.py, _eval_js) ---
  window.renderReport = function(report) {
    lastReport = report;
    document.getElementById("reportOverlay").style.display = "flex";
    document.getElementById("reportPanel").style.display = "flex";
    document.getElementById("puzzlePanel").style.display = "none";
    renderReportTable(report);
  };
  window.updateProfile = function(profileId, entry) {
    PROFILE_DATA[profileId] = entry;
    if (profileId === activeProfile) renderDetail();
  };
  window.showStatus = function(message) {
    document.getElementById("detail-theme").querySelector("svg").innerHTML = THEME_ICON_PATHS["info"];
    document.getElementById("detail-theme-label").textContent = "En attente";
    document.getElementById("detail-lines").innerHTML =
      '<p id="status-line">' + escapeHtml(message) + '</p>';
  };
  window.setCamp = function(camp) {
    document.getElementById("camp-label").textContent = (camp === "w") ? "Blancs" : "Noirs";
  };
  window.setEloTier = function(tierId) {
    document.getElementById("elo-slider").value = tierId;
    document.getElementById("elo-label").textContent = "Niveau : " + eloLabelFor(tierId) + " Elo";
  };

  selectProfile("popular");
</script>
</body>
</html>
"""
