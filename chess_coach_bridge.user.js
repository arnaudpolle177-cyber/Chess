// ==UserScript==
// @name         Coach d'échecs (pont local)
// @namespace    https://chess-coach.local
// @version      1.0
// @description  Lit le plateau (chessground) et affiche les 3 meilleurs coups directement sur la page, via le coach Python local.
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==
/*
 * chess_coach_bridge.user.js
 * -----------------------------------------------------------------------
 * Installation :
 *   1. Installe l'extension Tampermonkey dans ton navigateur.
 *   2. Ouvre le tableau de bord Tampermonkey -> "Créer un script".
 *   3. Efface tout, colle le contenu de ce fichier, sauvegarde (Ctrl+S).
 *   4. Va sur la page de jeu de ton site -> le script s'active tout seul.
 *
 * IMPORTANT :
 *   - Ligne @match ci-dessus : par défaut ça marche sur N'IMPORTE QUEL
 *     site, pratique pour tester tout de suite. Une fois que
 *     tu connais l'URL exacte de ton site, remplace cette ligne par
 *     quelque chose de plus précis, ex :
 *         // @match        https://ton-site.exemple.com/*
 *     Ça évite que le script tourne inutilement sur tous tes autres
 *     onglets ouverts.
 *
 * Ce script :
 *   1. Lit la position du plateau directement dans le DOM (chessground),
 *      sans capture d'écran ni reconnaissance d'image -> fiable à 100%.
 *   2. L'envoie au petit serveur Python local (web_bridge.py), via
 *      GM_xmlhttpRequest (contourne les restrictions CSP/CORS du site,
 *      donc ça marche même sans toucher au code du site).
 *   3. Dessine les 3 meilleurs coups sous forme de flèches colorées
 *      directement sur ton échiquier, dans une couche SVG ajoutée par ce
 *      script (pas besoin de calibration, toujours parfaitement aligné).
 *
 * Le seul point qui peut avoir besoin d'ajustement : getSideToMove() plus
 * bas, si jamais la détection automatique du tour de jeu se désynchronise
 * (rare, voir le commentaire dans la fonction).
 * -----------------------------------------------------------------------
 */
(function () {
  const COACH_ENDPOINT = "http://127.0.0.1:8765/fen";
  const ARROW_COLORS = ["#a6e3a1", "#89dceb", "#cba6f7"]; // 1er, 2e, 3e coup (mêmes couleurs que la fenêtre coach)
  const PIECE_LETTERS = { pawn: "p", knight: "n", bishop: "b", rook: "r", queen: "q", king: "k" };

  // Passe à true si tu as besoin de déboguer la lecture du plateau : affiche
  // le détail (FEN, orientation, nb de pièces) à CHAQUE poll. En usage
  // normal, laisse à false -- sinon ça spam la console en continu.
  const DEBUG = false;

  // Nombre de lectures IDENTIQUES consécutives requises avant d'envoyer une
  // position au serveur. Pendant l'animation d'un coup (drag, glissement),
  // la lecture du DOM peut être momentanément instable (une pièce apparaît
  // sur la mauvaise case pendant quelques ms) -- ça génère un FEN parasite
  // différent, qui déclenche un envoi au serveur pour rien, et donc un
  // effacement + redessin des flèches -> c'est CA le flicker. En exigeant
  // 2 lectures stables d'affilée, on filtre ces faux positifs.
  const STABLE_READS_REQUIRED = 2;

  let lastSentBoardPart = null;
  let localTurnToggle = "w"; // repli si getSideToMove() ne peut rien déterminer (voir plus bas)
  let lastStableGrid = null; // dernière position confirmée (grille 8x8), pour déduire qui vient de jouer

  let pendingBoardPart = null;
  let pendingStableCount = 0;

  let lastDrawnMovesKey = null; // pour éviter de redessiner les flèches si le résultat n'a pas changé

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
  // Trait (qui doit jouer).
  //
  // Priorité 1 : si ton site expose l'info (variable JS, objet chess.js,
  // etc.), remplace le contenu de cette fonction par ex. :
  //     return window.monJeu.turn();      // si tu utilises chess.js
  //     return maPartie.sideToMove;       // si tu as ta propre variable
  //
  // Priorité 2 (par défaut) : déduction automatique en comparant la
  // position stable précédente à la nouvelle -- la case qui a PERDU sa
  // pièce indique la couleur qui vient de jouer, donc c'est maintenant à
  // l'autre couleur de jouer. Contrairement à un simple compteur qui
  // alterne "w"/"b" à l'aveugle (et qui ne se resynchronise jamais s'il se
  // décale ne serait-ce qu'une fois), cette méthode se corrige toute seule
  // dès le premier vrai coup observé, quel que soit l'état de départ.
  // -----------------------------------------------------------------
  function inferMoverColor(oldGrid, newGrid) {
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const oldPiece = oldGrid[r][c];
        if (oldPiece !== "." && oldPiece !== newGrid[r][c]) {
          // Cette case avait une pièce avant, et n'a plus la même
          // maintenant (déplacée ou capturée depuis ici) -> sa couleur est
          // celle du camp qui vient de jouer.
          return oldPiece === oldPiece.toUpperCase() ? "white" : "black";
        }
      }
    }
    return null; // aucune case n'a "perdu" de pièce -> déduction impossible
  }

  function getSideToMove(newGrid) {
    if (window.chessCoachGetTurn) {
      try {
        return window.chessCoachGetTurn();
      } catch (e) {
        console.warn("chessCoachGetTurn() a levé une erreur, repli sur la déduction automatique :", e);
      }
    }
    if (lastStableGrid) {
      const moverColor = inferMoverColor(lastStableGrid, newGrid);
      if (moverColor) {
        localTurnToggle = moverColor === "white" ? "b" : "w";
        return localTurnToggle;
      }
    }
    // Repli : toute première position jamais lue (pas de comparaison
    // possible), ou déduction non concluante (très rare).
    return localTurnToggle;
  }

  let consecutiveSuspiciousReads = 0;

  function readBoardState() {
    const els = getBoardElements();
    if (!els) {
      console.warn("Coach d'échecs : éléments cg-wrap/cg-container/cg-board introuvables sur cette page.");
      return null;
    }
    const { grid, isWhiteOrientation, squareSize, size } = readGrid(els);
    const piecesFound = grid.flat().filter((c) => c !== ".").length;
    const boardPart = gridToFenBoardPart(grid);

    if (DEBUG) {
      console.log(
        `Coach d'échecs [debug] : ${piecesFound} pièce(s) détectée(s), ` +
        `orientation=${isWhiteOrientation ? "blanc" : "noir"}, ` +
        `taille plateau=${size}px, taille case=${squareSize}px\nplateau : ${boardPart}`
      );
    }

    // Un signal beaucoup plus fiable qu'un simple "peu de pièces" (qui est
    // NORMAL en fin de partie, ex: Roi+Dame vs Roi) : une position valide a
    // toujours exactement 1 roi blanc et 1 roi noir. S'il en manque un ou
    // qu'il y en a 2, la lecture est certainement fausse.
    const flat = grid.flat();
    const whiteKings = flat.filter((c) => c === "K").length;
    const blackKings = flat.filter((c) => c === "k").length;
    const suspicious = whiteKings !== 1 || blackKings !== 1;

    if (suspicious) {
      consecutiveSuspiciousReads++;
    } else {
      consecutiveSuspiciousReads = 0;
    }

    // On n'alerte que si le problème persiste sur plusieurs polls d'affilée
    // (~2s) : une lecture louche isolée est presque toujours un DOM en
    // cours de redessin (le site retire/rajoute les pièces entre 2 coups),
    // qui se corrige tout seul au poll suivant -- pas la peine d'alerter.
    if (consecutiveSuspiciousReads === 3) {
      console.warn(
        `Coach d'échecs : lecture du plateau suspecte depuis plusieurs secondes ` +
        `(rois détectés : blanc=${whiteKings}, noir=${blackKings}, ${piecesFound} pièce(s) au total). ` +
        "Vérifie que le site n'a pas changé de structure DOM. Grille actuelle :", grid
      );
    }

    return { grid, boardPart, whiteKings, blackKings };
  }

  // ---------------------------------------------------------------------
  // 2. Envoi au serveur Python + réception des coups recommandés
  // ---------------------------------------------------------------------

  async function sendFenToCoach(fen) {
    return new Promise((resolve) => {
      GM_xmlhttpRequest({
        method: "POST",
        url: COACH_ENDPOINT,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify({ fen }),
        timeout: 8000,
        onload: (response) => {
          try {
            const data = JSON.parse(response.responseText);
            if (data.error) {
              console.warn("Coach d'échecs :", data.error);
              clearArrows();
            } else if (data.game_over) {
              clearArrows();
            } else {
              drawArrows(data.lines);
            }
          } catch (e) {
            console.warn("Coach d'échecs : réponse invalide du serveur local.", e);
          }
          resolve();
        },
        onerror: () => {
          // Le serveur Python n'est probablement pas lancé -> pas grave, on
          // réessaiera au prochain coup détecté.
          console.warn(
            "Coach d'échecs : impossible de contacter le serveur local (port 8765). " +
            "Vérifie que le programme Python tourne bien (option 'Mode navigateur')."
          );
          resolve();
        },
        ontimeout: () => {
          console.warn("Coach d'échecs : le serveur local met trop de temps à répondre.");
          resolve();
        },
      });
    });
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
    lastDrawnMovesKey = null;
    const svg = document.getElementById("chess-coach-arrows");
    if (svg) {
      svg.querySelectorAll("line, circle.cc-label-bg, text.cc-label").forEach((n) => n.remove());
    }
  }

  function drawArrows(lines) {
    const movesKey = JSON.stringify((lines || []).map((l) => l.move_uci));
    if (movesKey === lastDrawnMovesKey) return; // deja affiche, rien a refaire

    const els = getBoardElements();
    if (!els) return;
    const { isWhiteOrientation, squareSize } = readGrid(els);
    const svg = getOrCreateSvgLayer(els);
    clearArrows();
    lastDrawnMovesKey = movesKey;

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
    if (countPieces() === 0) return; // état transitoire probable, on retente au prochain tick
    const state = readBoardState();
    if (!state) return;
    const { grid, boardPart, whiteKings, blackKings } = state;

    // IMPORTANT : on stabilise/compare uniquement la position des PIÈCES,
    // jamais le trait ("w"/"b"). Comparer le FEN complet (trait inclus)
    // provoquait une boucle infinie : le trait s'inversait à chaque envoi
    // confirmé, ce qui rendait la lecture suivante "différente" même sans
    // aucun coup réel joué, donc renvoyait encore, inversait encore, etc.
    // -> les flèches passaient sans arrêt du camp blanc au camp noir.

    // Filtre les lectures instables (ex: pendant l'animation d'un coup) :
    // on n'agit que si on lit exactement la même position de pièces 2 fois
    // de suite.
    if (boardPart !== pendingBoardPart) {
      pendingBoardPart = boardPart;
      pendingStableCount = 1;
      return;
    }
    pendingStableCount++;
    if (pendingStableCount < STABLE_READS_REQUIRED) return;

    if (boardPart === lastSentBoardPart) return; // le plateau n'a pas vraiment changé, rien à refaire

    // Garde-fou : même stable, une position sans exactement 1 roi de chaque
    // couleur est forcément une mauvaise lecture -> pas la peine d'embêter
    // le serveur avec, on retentera au prochain poll.
    if (whiteKings !== 1 || blackKings !== 1) return;

    // Le plateau a VRAIMENT changé (position de pièces différente et
    // stable) -> c'est le seul moment où on détermine/met à jour le trait,
    // par déduction (quelle case a perdu sa pièce) plutôt qu'en alternant
    // à l'aveugle -- voir le commentaire au-dessus de getSideToMove().
    const turn = getSideToMove(grid);
    const finalFen = `${boardPart} ${turn} KQkq - 0 1`;

    lastStableGrid = grid;
    lastSentBoardPart = boardPart;
    sendFenToCoach(finalFen);
  }

  function countPieces() {
    const els = getBoardElements();
    if (!els) return 0;
    return els.board.querySelectorAll("piece").length;
  }

  function startWatching() {
    const els = getBoardElements();
    if (!els) {
      // La page n'a pas encore fini de charger l'échiquier -> réessaie.
      setTimeout(startWatching, 500);
      return;
    }
    console.log("♟ Coach d'échecs connecté : lecture directe du plateau (aucune capture d'écran).");
    // Vérification périodique plutôt qu'un MutationObserver : plus simple
    // et insensible aux cas où le site remplace/redessine entièrement le
    // plateau entre deux coups (ce qui pouvait faire rater une mise à jour
    // avec l'ancienne approche basée sur les mutations DOM).
    setInterval(onBoardChanged, 700);
    onBoardChanged(); // première tentative immédiate
  }

  startWatching();
})();
