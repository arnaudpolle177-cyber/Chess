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

import webview


class _JsApi:
    """Méthodes appelables depuis le JS via window.pywebview.api.xxx()."""

    def __init__(self, on_elo_change, on_toggle_side, on_refresh):
        self._on_elo_change = on_elo_change
        self._on_toggle_side = on_toggle_side
        self._on_refresh = on_refresh

    def set_elo(self, tier_id):
        if self._on_elo_change:
            self._on_elo_change(int(tier_id))

    def toggle_side(self):
        if self._on_toggle_side:
            self._on_toggle_side()

    def refresh(self):
        if self._on_refresh:
            self._on_refresh()


class CoachWebview:
    def __init__(self, on_refresh_click=None, on_toggle_side_click=None, on_elo_change=None):
        self._window = None
        self._api = _JsApi(on_elo_change, on_toggle_side_click, on_refresh_click)
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
    margin: 0; padding: 0; height: 100%;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Arial, sans-serif;
    font-size: 14px;
    -webkit-font-smoothing: antialiased;
    user-select: none;
  }
  #app { display: flex; flex-direction: column; height: 100%; padding: 16px; gap: 14px; }

  /* --- en-tête : niveau + camp --- */
  #header { display: flex; flex-direction: column; gap: 8px; }
  #elo-row { display: flex; align-items: center; justify-content: space-between; }
  #elo-label { font-size: 12px; font-weight: 600; color: var(--warn); letter-spacing: .02em; }
  #camp-btn {
    display: flex; align-items: center; gap: 6px;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px;
    padding: 5px 12px; cursor: pointer; font-size: 12px; color: var(--text-dim);
    transition: border-color .15s, color .15s;
  }
  #camp-btn:hover { border-color: var(--accent-tactical); color: var(--text); }
  #camp-btn svg { width: 13px; height: 13px; }

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
  #status-line { font-size: 12.5px; color: var(--text-faint); margin: 0; }
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
    <div id="detail-theme">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
      <span id="detail-theme-label">En attente</span>
    </div>
    <div id="detail-lines">
      <p id="status-line">En attente de ton site... vérifie que chess_coach_bridge.user.js est bien activé dans Tampermonkey.</p>
    </div>
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

    if (!entry || !entry.narration) {
      themeEl.querySelector("svg").innerHTML = THEME_ICON_PATHS["info"];
      themeLabel.textContent = "En attente";
      lines.innerHTML = '<p id="status-line">' +
        (entry ? "Analyse en cours..." : "En attente du prochain coup...") + '</p>';
      return;
    }

    const n = entry.narration;
    themeEl.querySelector("svg").innerHTML = THEME_ICON_PATHS[n.theme_icon] || THEME_ICON_PATHS["info"];
    themeLabel.textContent = n.theme_label || "";

    let html =
      '<p><span class="block-label">' + escapeHtml(n.label1 || "") + ' — </span>' + escapeHtml(n.text1 || "") + '</p>' +
      '<p class="secondary"><span class="block-label">' + escapeHtml(n.label2 || "") + ' — </span>' + escapeHtml(n.text2 || "") + '</p>';
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

  // --- Appelées depuis Python (voir webview_ui.py, _eval_js) ---
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
