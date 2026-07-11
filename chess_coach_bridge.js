/*
 * chess_coach_bridge.js
 * -----------------------------------------------------------------------
 * À coller dans le code de ton site (juste avant </body>, ou en tant que
 * fichier <script src="chess_coach_bridge.js"> chargé sur la page de jeu).
 *
 * Ce script :
 *   1. Lit la position du plateau directement dans le DOM (chessground),
 *      sans capture d'écran ni reconnaissance d'image -> fiable à 100%.
 *   2. L'envoie au petit serveur Python local (web_bridge.py).
 *   3. Dessine les 3 meilleurs coups sous forme de flèches colorées
 *      directement sur ton échiquier, dans une couche SVG ajoutée par ce
 *      script (pas besoin de calibration, toujours parfaitement aligné).
 *
 * IMPORTANT - à adapter à TON site :
 *   - COACH_ENDPOINT : change le port si tu as changé --bridge-port.
 *   - getSideToMove() : voir le commentaire dans la fonction, c'est le
 *     seul point qui a besoin d'être vérifié/adapté selon comment ton
 *     site suit le tour de jeu.
 * -----------------------------------------------------------------------
 */
(function () {
  const COACH_ENDPOINT = "http://127.0.0.1:8765/fen";
  const ARROW_COLORS = ["#a6e3a1", "#89dceb", "#cba6f7"]; // 1er, 2e, 3e coup (mêmes couleurs que la fenêtre coach)
  const PIECE_LETTERS = { pawn: "p", knight: "n", bishop: "b", rook: "r", queen: "q", king: "k" };

  let lastSentFen = null;
  let localTurnToggle = "w"; // repli si getSideToMove() ne peut rien déterminer (voir plus bas)

  // ---------------------------------------------------------------------
  // 1. Lecture du plateau (DOM chessground -> grille 8x8 -> FEN)
  // ---------------------------------------------------------------------

  function getBoardElements() {
    const wrap = document.querySelector(".cg-wrap");
    const container = document.querySelector("cg-container");
    const board = document.querySelector("cg-board");
    if (!wrap || !container || !board) return null;
    return { wrap, container, board };
  }

  function readGrid(els) {
    const isWhiteOrientation = els.wrap.classList.contains("orientation-white");
    const size = els.container.offsetWidth || parseInt(els.container.style.width, 10) || 688;
    const squareSize = size / 8;

    const grid = Array.from({ length: 8 }, () => Array(8).fill("."));
    const pieces = els.board.querySelectorAll("piece");

    pieces.forEach((el) => {
      const classes = el.className.split(/\s+/);
      const isWhite = classes.includes("white");
      const typeClass = classes.find((c) => PIECE_LETTERS[c]);
      if (!typeClass) return;

      let letter = PIECE_LETTERS[typeClass];
      if (isWhite) letter = letter.toUpperCase();

      const style = el.getAttribute("style") || "";
      const m = style.match(/translate\(\s*([-\d.]+)px,\s*([-\d.]+)px\s*\)/);
      if (!m) return;
      const px = parseFloat(m[1]);
      const py = parseFloat(m[2]);

      let col = Math.round(px / squareSize);
      let row = Math.round(py / squareSize);

      // Le DOM place toujours (0,0) en haut-à-gauche TEL QU'AFFICHÉ. On
      // convertit vers une grille "absolue" (grid[0] = rangée 8, col 0 =
      // colonne a) quelle que soit l'orientation d'affichage.
      if (!isWhiteOrientation) {
        col = 7 - col;
        row = 7 - row;
      }
      if (row >= 0 && row < 8 && col >= 0 && col < 8) {
        grid[row][col] = letter;
      }
    });

    return { grid, isWhiteOrientation, squareSize, size };
  }

  function gridToFenBoardPart(grid) {
    return grid
      .map((row) => {
        let out = "";
        let empty = 0;
        row.forEach((c) => {
          if (c === ".") {
            empty++;
          } else {
            if (empty > 0) {
              out += empty;
              empty = 0;
            }
            out += c;
          }
        });
        if (empty > 0) out += empty;
        return out;
      })
      .join("/");
  }

  // -----------------------------------------------------------------
  // Trait (qui doit jouer). C'EST LE SEUL POINT SPÉCIFIQUE À TON SITE.
  //
  // Par défaut, ce script alterne "w"/"b" localement à chaque coup DÉTECTÉ
  // (ça marche bien tant que le MutationObserver capte bien chaque coup,
  // le sien ET celui de l'adversaire, une seule fois chacun).
  //
  // Si ton site sait déjà qui doit jouer (variable JS, objet chess.js,
  // etc.), remplace le contenu de cette fonction par ex. :
  //     return window.monJeu.turn();      // si tu utilises chess.js
  //     return maPartie.sideToMove;       // si tu as ta propre variable
  // -----------------------------------------------------------------
  function getSideToMove() {
    if (window.chessCoachGetTurn) {
      try {
        return window.chessCoachGetTurn();
      } catch (e) {
        console.warn("chessCoachGetTurn() a levé une erreur, repli sur le mode local :", e);
      }
    }
    return localTurnToggle;
  }

  function buildFen() {
    const els = getBoardElements();
    if (!els) return null;
    const { grid } = readGrid(els);
    const boardPart = gridToFenBoardPart(grid);
    const turn = getSideToMove();
    // Roques/en-passant non trackés ici -> valeurs par défaut (léger impact
    // sur l'analyse dans de rares situations de fin de partie/roque).
    return `${boardPart} ${turn} KQkq - 0 1`;
  }

  // ---------------------------------------------------------------------
  // 2. Envoi au serveur Python + réception des coups recommandés
  // ---------------------------------------------------------------------

  async function sendFenToCoach(fen) {
    try {
      const res = await fetch(COACH_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fen }),
      });
      const data = await res.json();
      if (data.error) {
        console.warn("Coach d'échecs :", data.error);
        clearArrows();
        return;
      }
      if (data.game_over) {
        clearArrows();
        return;
      }
      drawArrows(data.lines);
    } catch (e) {
      // Le serveur Python n'est probablement pas lancé -> pas grave, on
      // réessaiera au prochain coup détecté.
      console.warn("Coach d'échecs : impossible de contacter le serveur local (", e.message, "). Vérifie que le programme Python tourne bien (option 'Mode navigateur').");
    }
  }

  // ---------------------------------------------------------------------
  // 3. Dessin des flèches directement sur la page
  // ---------------------------------------------------------------------

  function getOrCreateSvgLayer(els) {
    let svg = document.getElementById("chess-coach-arrows");
    const size = els.container.offsetWidth || 688;
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = "chess-coach-arrows";
      svg.style.position = "absolute";
      svg.style.top = "0";
      svg.style.left = "0";
      svg.style.pointerEvents = "none";
      svg.style.zIndex = "9999";

      const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
      ARROW_COLORS.forEach((color, i) => {
        const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
        marker.setAttribute("id", `cc-arrowhead-${i}`);
        marker.setAttribute("markerWidth", "6");
        marker.setAttribute("markerHeight", "6");
        marker.setAttribute("refX", "3");
        marker.setAttribute("refY", "3");
        marker.setAttribute("orient", "auto");
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", "M0,0 L6,3 L0,6 Z");
        path.setAttribute("fill", color);
        marker.appendChild(path);
        defs.appendChild(marker);
      });
      svg.appendChild(defs);

      // S'assure que le conteneur peut recevoir un enfant positionné en absolu.
      if (getComputedStyle(els.container).position === "static") {
        els.container.style.position = "relative";
      }
      els.container.appendChild(svg);
    }
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    return svg;
  }

  function squareToXY(square, squareSize, isWhiteOrientation) {
    const file = square.charCodeAt(0) - "a".charCodeAt(0); // 0-7
    const rank = parseInt(square[1], 10); // 1-8
    let col, row;
    if (isWhiteOrientation) {
      col = file;
      row = 8 - rank;
    } else {
      col = 7 - file;
      row = rank - 1;
    }
    return { x: col * squareSize + squareSize / 2, y: row * squareSize + squareSize / 2 };
  }

  function clearArrows() {
    const svg = document.getElementById("chess-coach-arrows");
    if (svg) {
      svg.querySelectorAll("line, circle.cc-label-bg, text.cc-label").forEach((n) => n.remove());
    }
  }

  function drawArrows(lines) {
    const els = getBoardElements();
    if (!els) return;
    const { isWhiteOrientation, squareSize } = readGrid(els);
    const svg = getOrCreateSvgLayer(els);
    clearArrows();

    lines.forEach((entry, i) => {
      const uci = entry.move_uci;
      if (!uci) return;
      const fromSq = uci.slice(0, 2);
      const toSq = uci.slice(2, 4);
      const from = squareToXY(fromSq, squareSize, isWhiteOrientation);
      const to = squareToXY(toSq, squareSize, isWhiteOrientation);
      const color = ARROW_COLORS[i] || "#cccccc";

      // Raccourcit légèrement la ligne pour laisser de la place à la pointe.
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const len = Math.hypot(dx, dy) || 1;
      const shorten = squareSize * 0.35;
      const endX = to.x - (dx / len) * shorten;
      const endY = to.y - (dy / len) * shorten;

      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", from.x);
      line.setAttribute("y1", from.y);
      line.setAttribute("x2", endX);
      line.setAttribute("y2", endY);
      line.setAttribute("stroke", color);
      line.setAttribute("stroke-width", i === 0 ? 8 : 5);
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("opacity", i === 0 ? 0.85 : 0.6);
      line.setAttribute("marker-end", `url(#cc-arrowhead-${i})`);
      svg.appendChild(line);
    });
  }

  // ---------------------------------------------------------------------
  // 4. Surveillance du plateau (détecte chaque coup joué)
  // ---------------------------------------------------------------------

  function onBoardChanged() {
    const fen = buildFen();
    if (!fen || fen === lastSentFen) return;
    lastSentFen = fen;
    localTurnToggle = localTurnToggle === "w" ? "b" : "w"; // pour le prochain coup, si pas de hook custom
    sendFenToCoach(fen);
  }

  function startWatching() {
    const els = getBoardElements();
    if (!els) {
      // La page n'a pas encore fini de charger l'échiquier -> réessaie.
      setTimeout(startWatching, 500);
      return;
    }
    const observer = new MutationObserver(() => {
      clearTimeout(startWatching._debounce);
      startWatching._debounce = setTimeout(onBoardChanged, 150);
    });
    observer.observe(els.board, { attributes: true, subtree: true, attributeFilter: ["style"] });

    console.log("♟ Coach d'échecs connecté : lecture directe du plateau (aucune capture d'écran).");
    onBoardChanged(); // première analyse immédiate au chargement
  }

  startWatching();
})();
