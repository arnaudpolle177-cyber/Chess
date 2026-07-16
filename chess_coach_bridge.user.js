// ==UserScript==
// @name         Coach d'échecs (pont local)
// @namespace    https://chess-coach.local
// @version      1.0
// @description  Lit le plateau (chessground) et affiche les 3 meilleurs coups directement sur la page, via le coach Python local.
// @match        https://lichess.com/*
// @match        https://lichess.org/*
// @match        https://*.lichess.com/*
// @match        https://*.chess.com/*
// @match        https://chess.com/*
// @match        https://www.chess.com/play/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==
/*
 * chess_coach_bridge_user.js
 * -----------------------------------------------------------------------
 * Installation :
 *   1. Installe l'extension Tampermonkey dans ton navigateur.
 *   2. Ouvre le tableau de bord Tampermonkey -> "Créer un script".
 *   3. Efface tout, colle le contenu de ce fichier, sauvegarde (Ctrl+S).
 *   4. Va sur la page de jeu de ton site -> le script s'active tout seul.
 *
 * IMPORTANT :
 *   - Lignes @match ci-dessus : le script ne tourne QUE sur testchess.com
 *     et coachchess.com, pas sur les autres onglets ouverts. Si tu ajoutes
 *     un 3e site plus tard, rajoute une ligne @match du même style.
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
 *
 * DÉTECTION DU TRAIT (qui doit jouer) -- par ordre de priorité, voir
 * getSideToMove() plus bas :
 *   1. window.chessCoachGetTurn() si tu l'exposes sur ta page (fiable 100%) :
 *          window.chessCoachGetTurn = function() {
 *            return monEtatDePartie.trait === "blanc" ? "w" : "b";
 *          };
 *   2. Le SURLIGNAGE du dernier coup joué (cases "last-move" sur lichess/
 *      chessground, ".highlight" sur chess.com) : la case d'arrivée porte une
 *      pièce -> sa couleur = qui vient de jouer -> trait = l'autre camp. Sans
 *      historique, donc IMMUNISÉ aux coups rapides / pre-moves (contrairement
 *      à l'ancienne déduction par diff, qui pouvait se tromper de camp si 2
 *      coups tombaient entre 2 lectures). C'est la source normale.
 *   3. Correction manuelle (bouton "Corriger le trait") -- one-shot.
 *   4. Déduction par diff (première case qui a changé entre 2 lectures) --
 *      repli si le surlignage n'est pas lisible.
 *
 * COULEUR AUTO (mon camp) : déduite de l'ORIENTATION du plateau (mes pièces
 * sont toujours en bas) et envoyée au serveur à chaque requête -- il cale
 * set_my_side() tout seul. Le bouton "Changer de camp" de la fenêtre Python
 * ne sert plus qu'en override manuel (le dernier réglage reçu gagne).
 * -----------------------------------------------------------------------
 */
(function () {
  const COACH_ENDPOINT = "http://127.0.0.1:8765/fen";
  // 3 profils de jeu "humains" (voir human_profile.py côté serveur) --
  // l'ordre ici doit rester cohérent avec human_profile.PROFILE_IDS.
  // Le niveau Elo (slider dans la fenêtre Python) ne change PAS ces
  // couleurs/profils : il change la fenêtre de tolérance utilisée par le
  // serveur pour choisir CHAQUE coup, en amont de ce script.
  const PROFILE_IDS = ["popular", "creative", "classical"];
  const PROFILE_STYLE = {
    // width décroissant + opacity croissant : quand plusieurs profils
    // tombent d'accord sur le même coup, les flèches se superposent en
    // formant une "cible" au lieu que l'une masque les autres.
    popular:   { color: "#89dceb", width: 9, opacity: 0.45 }, // bleu : coup pragmatique, bonnes chances de gain
    creative:  { color: "#f38ba8", width: 5, opacity: 0.75 }, // rose : coup tactique/sacrificiel
    classical: { color: "#f5f5f5", width: 2, opacity: 1.0 },  // blanc : coup textbook, sensible à la phase de partie
  };
  // Style dédié à la flèche "coup théorique" (livre polyglot local en
  // priorité, base ECO nommée en repli, Lichess en dernier recours --
  // DÉSACTIVÉ par défaut, voir web_bridge.py LICHESS_EXPLORER_ENABLED --
  // voir _get_theory_move) -- volontairement HORS PROFILE_STYLE : ce n'est
  // pas un profil de jeu, dessinée séparément dans redrawProfileArrows()
  // en pointillé pour ne jamais se confondre avec les flèches pleines des
  // profils. Orange/pêche plutôt que
  // turquoise : trop proche du bleu "popular" (#89dceb) pour être
  // distingué au premier coup d'œil. De toute façon, quand cette flèche
  // est affichée, les 3 flèches de profils sont masquées (voir
  // redrawProfileArrows) -- la distinction de couleur ne sert donc plus
  // qu'à ne pas confondre "en théorie" et "hors théorie" d'un coup d'œil
  // résiduel pendant la transition entre 2 positions.
  const THEORY_STYLE = { color: "#fab387", width: 6, opacity: 0.9 };
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
  //
  // Avec POLL_INTERVAL_MS=150, ça représente ~300ms de latence de
  // détection (au lieu de ~1.4s avant) -- réduit fortement (sans l'éliminer
  // complètement, voir le hook chessCoachGetTurn ci-dessus) le risque que 2
  // coups réels se jouent avant qu'on ait eu le temps de les distinguer.
  const STABLE_READS_REQUIRED = 2;
  const POLL_INTERVAL_MS = 100;

  // Clé (position des pièces + trait) de la dernière position VERROUILLÉE
  // après un envoi réussi -- format "boardPart:turn". Inclure le trait est ce
  // qui débloque le cas "skip puis trait corrigé" (voir onBoardChanged).
  let lastSentKey = null;
  // Position actuellement en cours d'envoi (requête pas encore résolue).
  // Sans ça, comme lastSentKey n'est plus verrouillé tant que la
  // requête n'a pas réussi, le poll suivant (avant que la 1re requête,
  // qui peut prendre jusqu'à 20s, ait répondu) renverrait la même position
  // en double.
  // Clé (pièces + trait, format "boardPart:turn") de la requête actuellement
  // en vol -- sert à la fois à éviter les envois en double ET à reconstruire
  // lastSentKey au verrouillage (voir handleCoachPayload). null hors requête.
  let inFlightKey = null;
  // Handles GM_xmlhttpRequest des requêtes profil actuellement en vol, pour
  // pouvoir les couper net si un nouveau coup est joué avant leur réponse
  // (voir abortInFlightRequests / sendOneProfile).
  const inFlightRequests = new Set();
  let localTurnToggle = "w"; // repli si getSideToMove() ne peut rien déterminer (voir plus bas)
  let lastStableGrid = null; // dernière position confirmée (grille 8x8), pour déduire qui vient de jouer

  let pendingBoardPart = null;
  let pendingStableCount = 0;

  let lastDrawnMovesKey = null; // pour éviter de redessiner les flèches si le résultat n'a pas changé
  // Dernier coup connu pour chaque palier de profondeur reçu pour LA
  // POSITION EN COURS (remis à zéro à chaque nouvel envoi). Permet
  // d'afficher/mettre à jour une flèche dès qu'un palier arrive, sans
  // attendre les autres.
  let currentProfileEntries = {};
  let currentTheoryEntry = null; // {move_uci, move_san, source} ou null

  // ---------------------------------------------------------------------
  // 1. Lecture du plateau (DOM chessground -> grille 8x8 -> FEN)
  // ---------------------------------------------------------------------

  function pickLargest(elements) {
    let best = null;
    let bestSize = 0;
    elements.forEach((el) => {
      const w = el.offsetWidth || el.getBoundingClientRect().width || 0;
      if (w > bestSize) {
        bestSize = w;
        best = el;
      }
    });
    return best;
  }

  function getBoardElements() {
    // chess.com / coachchess (custom element wc-chess-board, pièces en
    // classes "wp square-74" -- pas de pixels, tout est positionné en %).
    // querySelectorAll + le plus grand : sur une page d'accueil qui affiche
    // aussi des mini-plateaux (parties en cours d'autres joueurs, puzzle du
    // jour...), le PREMIER trouvé dans le DOM n'est pas forcément le
    // plateau de jeu principal -- le plus grand visuellement l'est presque
    // toujours.
    const wcBoards = document.querySelectorAll("wc-chess-board.board, chess-board.board");
    const wcBoard = pickLargest(wcBoards);
    if (wcBoard) return { type: "chesscom", board: wcBoard };

    // lichess / testchess (chessground, pièces positionnées via
    // translate() en pixels). Même logique : plusieurs .cg-wrap possibles
    // sur une page d'accueil, on prend le plus grand.
    const wraps = document.querySelectorAll(".cg-wrap");
    const wrap = pickLargest(wraps);
    if (!wrap) return null;
    const container = wrap.querySelector("cg-container");
    const board = wrap.querySelector("cg-board");
    if (container && board) return { type: "chessground", wrap, container, board };

    return null;
  }

  function readGridChessCom(els) {
    const isWhiteOrientation = !(
      els.board.classList.contains("flipped") || els.board.hasAttribute("flipped")
    );
    const grid = Array.from({ length: 8 }, () => Array(8).fill("."));

    els.board.querySelectorAll(".piece").forEach((el) => {
      const classes = el.className.split(/\s+/);
      const pieceClass = classes.find((c) => /^[wb][pnbrqk]$/.test(c));
      const squareClass = classes.find((c) => /^square-\d\d$/.test(c));
      if (!pieceClass || !squareClass) return;

      const color = pieceClass[0]; // w ou b
      let letter = pieceClass[1];
      if (color === "w") letter = letter.toUpperCase();

      const file = parseInt(squareClass[7], 10); // 1-8, a=1
      const rank = parseInt(squareClass[8], 10); // 1-8

      const row = 8 - rank; // grid[0] = rangée 8
      const col = file - 1; // col 0 = colonne a
      if (row >= 0 && row < 8 && col >= 0 && col < 8) {
        grid[row][col] = letter;
      }
    });

    // squareSize/size en PIXELS RÉELS (comme chessground) plutôt qu'en
    // pourcentage -- même si chess.com positionne ses pièces en %, on garde
    // un repère en pixels pour que les épaisseurs de flèches (pensées en
    // pixels dans PROFILE_STYLE) restent visuellement correctes.
    const size = els.board.offsetWidth || els.board.getBoundingClientRect().width || 600;
    const squareSize = size / 8;

    return { grid, isWhiteOrientation, squareSize, size };
  }

  function readGridChessground(els) {
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

  function readGrid(els) {
    return els.type === "chesscom" ? readGridChessCom(els) : readGridChessground(els);
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
  // Trait (qui doit jouer) -- SURLIGNAGE DU DERNIER COUP, source principale.
  //
  // Les deux sites marquent visuellement les 2 cases du dernier coup joué
  // (départ + arrivée). La case d'ARRIVÉE porte la pièce qui vient de bouger
  // -> sa couleur est celle du camp qui vient de jouer, donc c'est maintenant
  // à l'AUTRE de jouer (voir turnFromLastMoveSquares).
  //
  // Avantage décisif sur inferMoverColor (diff de 2 positions) : ne dépend PAS
  // d'une lecture précédente. Immunisé aux enchaînements rapides (ton coup +
  // celui de l'adversaire joués avant le prochain poll) qui, eux, faussaient
  // la déduction par diff -- cause du "il croit que c'est encore à
  // l'adversaire" et du blocage qui obligeait à cliquer "Corriger le trait".
  // (Si ton site expose le trait directement, le hook window.chessCoachGetTurn
  // reste prioritaire sur tout ceci -- voir getSideToMove.)
  // -----------------------------------------------------------------

  // Lit les cases du DERNIER coup (surlignage du site) et les renvoie sous
  // forme [{row, col}] (0 à 2 entrées). Lecture DOM isolée + try/catch : les
  // deux consommateurs (trait via turnFromLastMoveSquares, en passant via
  // enPassantFromGrid) partagent CE seul appel -- lu une fois par tick dans
  // onBoardChanged, pas deux (voir #2, lecture DOM unique).
  function readLastMoveSquares(els) {
    try {
      if (els.type === "chesscom") {
        // chess.com : cases surlignées = éléments ".highlight" avec un
        // data-square "54" (format "colRow", ex e4 -> "54").
        const highlights = els.board.querySelectorAll(".highlight[data-square], .highlight[style]");
        return [...highlights].map(squareFromChessComHighlight).filter(Boolean);
      }
      // chessground (lichess/testchess) : les 2 cases du dernier coup sont
      // des <square class="last-move"> positionnées en translate() pixels,
      // comme les pièces -- enfants directs de cg-board (els.board), même
      // conteneur que les pièces lues dans readGridChessground.
      const cgSquares = els.board.querySelectorAll("square.last-move");
      const { squareSize } = readGridChessground(els);
      const isWhiteOrientation = els.wrap.classList.contains("orientation-white");
      return [...cgSquares]
        .map((node) => squareFromChessgroundTranslate(node, squareSize, isWhiteOrientation))
        .filter(Boolean);
    } catch (e) {
      if (DEBUG) console.warn("Coach d'échecs [debug] : lecture du surlignage échouée, repli sur diff.", e);
      return [];
    }
  }

  // Trait déduit des cases du dernier coup : la case d'ARRIVÉE porte la pièce
  // qui vient de bouger -> sa couleur est celle du camp qui vient de jouer,
  // donc c'est à l'AUTRE de jouer. Si 0 case lisible ou 2 cases occupées
  // (rare : reprise sur la case d'arrivée -> ambigu), on ne tranche pas (null)
  // et l'appelant retombe sur la déduction par diff.
  function turnFromLastMoveSquares(squares, grid) {
    if (!squares || squares.length === 0) return null;
    const occupied = [];
    squares.forEach((rc) => {
      const piece = grid[rc.row] && grid[rc.row][rc.col];
      if (piece && piece !== ".") occupied.push(piece);
    });
    if (occupied.length !== 1) return null;
    const piece = occupied[0];
    const moverIsWhite = piece === piece.toUpperCase();
    return moverIsWhite ? "b" : "w";
  }

  function squareFromChessComHighlight(node) {
    // data-square peut valoir "54" (col 5, row 4, base 1) sur wc-chess-board.
    const sq = node.getAttribute("data-square");
    if (sq && /^\d\d$/.test(sq)) {
      const file = parseInt(sq[0], 10); // 1-8, a=1
      const rank = parseInt(sq[1], 10); // 1-8
      return { row: 8 - rank, col: file - 1 };
    }
    // Repli : certaines versions positionnent le highlight en style
    // translate %, comme les pièces -- non géré ici (renvoie null -> diff).
    return null;
  }

  function squareFromChessgroundTranslate(node, squareSize, isWhiteOrientation) {
    const style = node.getAttribute("style") || "";
    const m = style.match(/translate\(\s*([-\d.]+)px,\s*([-\d.]+)px\s*\)/);
    if (!m) return null;
    let col = Math.round(parseFloat(m[1]) / squareSize);
    let row = Math.round(parseFloat(m[2]) / squareSize);
    if (!isWhiteOrientation) {
      col = 7 - col;
      row = 7 - row;
    }
    if (row < 0 || row > 7 || col < 0 || col > 7) return null;
    return { row, col };
  }

  // Case cible EN PASSANT (champ FEN), déduite des cases du dernier coup.
  // Sans elle, le moteur ne verra JAMAIS une prise en passant possible et peut
  // rater le meilleur coup. On réutilise les cases déjà lues pour le trait
  // (readLastMoveSquares) -> aucune lecture DOM supplémentaire.
  //
  // Règle FEN : la case en passant n'est renseignée que si le DERNIER coup est
  // un pion qui a avancé de DEUX rangées. La case cible est la case SAUTÉE
  // (entre départ et arrivée). Retourne "e3" / "d6" / ... ou "-".
  function enPassantFromGrid(squares, grid) {
    if (!squares || squares.length !== 2) return "-";
    // La case d'ARRIVÉE porte le pion ; la case de DÉPART est vide.
    let to = null, from = null;
    squares.forEach((rc) => {
      const piece = grid[rc.row] && grid[rc.row][rc.col];
      if (piece && piece !== ".") to = { ...rc, piece };
      else from = rc;
    });
    if (!to || !from) return "-";
    if (to.piece !== "P" && to.piece !== "p") return "-"; // pas un pion
    if (to.col !== from.col) return "-";                  // pas une avance droite (capture)
    if (Math.abs(to.row - from.row) !== 2) return "-";    // pas un bond de 2

    // Règle FEN stricte : ne renseigner la case en passant QUE si un pion
    // adverse peut effectivement capturer (sinon "-"). Un pion ennemi doit se
    // trouver sur une case adjacente EN COLONNE à celle où le pion vient
    // d'atterrir (même rangée que l'arrivée). Sans ce filtre, on émettrait une
    // case en passant "fantôme" -- inoffensive avec python-chess (qui la
    // normalise), mais incorrecte au sens strict et trompeuse pour tout autre
    // consommateur du FEN.
    const enemyPawn = to.piece === "P" ? "p" : "P";
    const canCapture = [to.col - 1, to.col + 1].some((col) => {
      if (col < 0 || col > 7) return false;
      const adj = grid[to.row] && grid[to.row][col];
      return adj === enemyPawn;
    });
    if (!canCapture) return "-";

    // Case sautée = rangée intermédiaire, même colonne.
    const midRow = (to.row + from.row) / 2;
    const file = "abcdefgh"[to.col];
    const rank = 8 - midRow;
    return `${file}${rank}`;
  }

  // Droits de roque RÉELS (au lieu du "KQkq" figé qui faisait croire au moteur
  // qu'on peut toujours roquer -> suggestions de roque illégales). On ne peut
  // pas AJOUTER un droit depuis la seule position, mais on peut RETIRER ceux
  // qui sont clairement impossibles : roi absent de sa case d'origine, ou tour
  // absente de son coin. Prudent par construction : en cas de doute, on garde
  // le droit (le moteur filtrera de toute façon les coups illégaux).
  function castlingRightsFromGrid(grid) {
    const at = (row, col) => (grid[row] && grid[row][col]) || ".";
    let rights = "";
    // Blancs : roi en e1 (row 7, col 4).
    if (at(7, 4) === "K") {
      if (at(7, 7) === "R") rights += "K"; // tour h1
      if (at(7, 0) === "R") rights += "Q"; // tour a1
    }
    // Noirs : roi en e8 (row 0, col 4).
    if (at(0, 4) === "k") {
      if (at(0, 7) === "r") rights += "k"; // tour h8
      if (at(0, 0) === "r") rights += "q"; // tour a8
    }
    return rights || "-";
  }

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

  // Correction manuelle du trait : posée par le bouton flottant "⇄ Corriger
  // le trait" (voir plus bas). PERSISTANTE jusqu'au prochain coup réel (=
  // changement de la position des pièces), pas juste un tick : sinon, comme
  // le surlignage du dernier coup est désormais la source prioritaire et est
  // relu à CHAQUE poll (~150ms), une correction ne durant qu'un tick serait
  // écrasée aussitôt si le surlignage est mal lu de façon répétée -> le bouton
  // de secours deviendrait inutile pile quand on en a besoin. On mémorise donc
  // la valeur corrigée ET la position des pièces sur laquelle elle a été posée
  // (forcedTurnBoardPart) : tant que les pièces n'ont pas bougé, la correction
  // prime sur le surlignage ; dès qu'un vrai coup est joué, elle expire et la
  // déduction automatique reprend la main.
  let forcedTurnValue = null;       // "w" / "b" / null
  let forcedTurnBoardPart = null;   // position des pièces au moment de la correction

  function getSideToMove(newGrid, lastMoveSquares, boardPart) {
    if (window.chessCoachGetTurn) {
      try {
        return window.chessCoachGetTurn();
      } catch (e) {
        console.warn("chessCoachGetTurn() a levé une erreur, repli sur la déduction automatique :", e);
      }
    }
    // Correction manuelle explicite : gagne sur la déduction auto (mais pas
    // sur le hook du site ci-dessus, qui est une vérité, pas une déduction).
    // Persistante TANT QUE les pièces n'ont pas bougé (voir forcedTurnValue) :
    // dès qu'un vrai coup est joué, la position des pièces change, la
    // correction expire et la déduction automatique (surlignage/diff) reprend.
    if (forcedTurnValue !== null) {
      if (boardPart === forcedTurnBoardPart) {
        localTurnToggle = forcedTurnValue;
        return forcedTurnValue;
      }
      // Un coup a été joué depuis la correction -> elle n'a plus lieu d'être.
      forcedTurnValue = null;
      forcedTurnBoardPart = null;
    }
    // Source PRIORITAIRE (sans historique, robuste aux coups rapides) : le
    // surlignage du dernier coup joué (cases déjà lues une fois ce tick, voir
    // onBoardChanged / readLastMoveSquares).
    const fromHighlight = turnFromLastMoveSquares(lastMoveSquares, newGrid);
    if (fromHighlight) {
      localTurnToggle = fromHighlight;
      return fromHighlight;
    }
    // Repli 1 : déduction par diff avec la position stable précédente.
    if (lastStableGrid) {
      const moverColor = inferMoverColor(lastStableGrid, newGrid);
      if (moverColor) {
        localTurnToggle = moverColor === "white" ? "b" : "w";
        return localTurnToggle;
      }
    }
    // Repli 2 : toute première position jamais lue (pas de comparaison
    // possible), ou déduction non concluante (très rare).
    return localTurnToggle;
  }

  let consecutiveSuspiciousReads = 0;

  function readBoardState() {
    const els = getBoardElements();
    if (!els) {
      console.warn("Coach d'échecs : plateau introuvable sur cette page (ni chessground, ni chess.com).");
      return null;
    }

    // Le glissement en cours (voir plus bas) n'existe que sous forme de
    // classe "dragging" sur chessground -- chess.com n'a pas cet état dans
    // le DOM des pièces, donc rien à filtrer côté chess.com ici.

    // Une pièce est en train d'être glissée (clic gauche maintenu, pas
    // encore relâchée) : chessground fait suivre sa position au curseur en
    // pixels bruts, qui ne correspond ni à la case de départ ni à une case
    // d'arrivée réelle -- juste où se trouve le curseur À CET INSTANT. Si
    // on lisait le plateau maintenant et que l'utilisateur marque une
    // pause pendant le glisser (le temps de réfléchir), ça pouvait être
    // interprété comme un coup joué (2 lectures stables d'affilée) alors
    // que rien n'a été validé -- c'était la cause du "il pense que j'ai
    // joué alors que je fais juste glisser la pièce". On ignore
    // complètement ce poll tant qu'un glissement est en cours ; la lecture
    // reprend normalement dès que la pièce est lâchée (déposée ou
    // annulée/revenue à sa case).
    if (els.board.querySelector("piece.dragging")) {
      if (DEBUG) console.log("Coach d'échecs [debug] : glissement en cours détecté, lecture ignorée pour ce tick.");
      return null;
    }

    const { grid, isWhiteOrientation, squareSize, size } = readGrid(els);
    const flat = grid.flat();
    const piecesFound = flat.filter((c) => c !== ".").length;

    // Plateau vide = état transitoire (le site retire puis rajoute toutes les
    // pièces en redessinant entre 2 coups) -- jamais une vraie position. On
    // l'ignore comme le glissement en cours ci-dessus, plutôt que de le laisser
    // gonfler consecutiveSuspiciousReads (0 pièce = 0 roi = "suspect" à tort).
    // Rend aussi countPieces() inutile en tête de onBoardChanged : une seule
    // lecture DOM par tick au lieu de deux (voir readGrid / getBoardElements).
    if (piecesFound === 0) return null;

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

    return { grid, boardPart, whiteKings, blackKings, isWhiteOrientation, els };
  }

  // ---------------------------------------------------------------------
  // 2. Envoi au serveur Python + réception des coups recommandés
  // ---------------------------------------------------------------------

  async function sendFenToCoach(fen, boardPart, side, turn) {
    // Nouvelle position -> on repart d'un état de flèches vierge, elles
    // seront redessinées une par une au fil des profils reçus. Idem pour
    // currentTheoryEntry : sans ce reset, la flèche théorique de l'ANCIENNE
    // position resterait affichée jusqu'à la prochaine mise à jour valide
    // -- trompeur si la nouvelle position, elle, n'a aucune suggestion
    // théorique (ex: on vient de sortir de la théorie connue).
    currentProfileEntries = {};
    currentTheoryEntry = null;

    // Une requête HTTP INDÉPENDANTE par profil (au lieu d'un seul flux
    // streamé) : plus robuste, chaque profil arrive et s'affiche dès qu'IL
    // est prêt, sans dépendre du support d'onprogress() du navigateur/
    // gestionnaire d'extensions (voir le commentaire dans web_bridge.py,
    // handle_single_profile).
    //
    // Plus d'aperçu rapide préalable (ancien sendQuickTake, retiré) : depuis
    // que la vraie analyse démarre immédiatement, l'ancien "quick take"
    // faisait tourner le moteur une PREMIÈRE fois (à la même profondeur ou
    // presque) juste avant, en tenant le verrou moteur -- donc la vraie
    // analyse ne pouvait même pas démarrer tant qu'il n'avait pas fini. On
    // payait quasiment deux fois le même calcul en série. En le supprimant,
    // la première vraie flèche arrive PLUS TÔT qu'avant, pas plus tard.
    const results = await Promise.all(
      PROFILE_IDS.map((profileId) => sendOneProfile(fen, boardPart, profileId, side, turn))
    );

    // Le succès est confirmé si AU MOINS un profil a répondu correctement
    // -- si un seul échoue (timeout ponctuel), on ne bloque pas les autres,
    // mais on ne verrouille la position que si on a eu au moins une réponse
    // exploitable (voir handleCoachPayload, qui pose lastSentKey).
    if (results.every((ok) => !ok)) {
      console.warn("Coach d'échecs : aucun des 3 profils n'a répondu pour cette position.");
    }
    // Ne remet inFlightKey à null que si aucune requête plus récente n'a
    // pris le relais entre-temps (sinon on effacerait le verrou d'envoi de la
    // position suivante, laissant passer un doublon).
    if (inFlightKey === `${boardPart}:${turn}`) inFlightKey = null;
  }

  function sendOneProfile(fen, boardPart, profileId, side, turn) {
    return new Promise((resolve) => {
      const handle = GM_xmlhttpRequest({
        method: "POST",
        url: COACH_ENDPOINT,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify(side ? { fen, profile: profileId, side } : { fen, profile: profileId }),
        // Un profil = 1 analyse MultiPV (+ un avis Elo-bridé rapide) sur
        // une position déjà chargée : quelques secondes grand maximum.
        timeout: 15000,
        onload: (response) => {
          inFlightRequests.delete(handle);
          let payload;
          try {
            payload = JSON.parse(response.responseText || "{}");
          } catch (e) {
            console.warn(`Coach d'échecs : réponse invalide du serveur local pour le profil ${profileId}.`, e);
            resolve(false);
            return;
          }
          updateStatusIndicator({ serverOk: true }); // une réponse = serveur joignable
          handleCoachPayload(payload, boardPart, turn);
          resolve(true);
        },
        onerror: () => {
          inFlightRequests.delete(handle);
          updateStatusIndicator({ serverOk: false });
          console.warn(
            `Coach d'échecs : impossible de contacter le serveur local (port 8765) pour le profil ${profileId}. ` +
            "Vérifie que le programme Python tourne bien (option 'Mode navigateur')."
          );
          resolve(false);
        },
        ontimeout: () => {
          inFlightRequests.delete(handle);
          updateStatusIndicator({ serverOk: false });
          console.warn(`Coach d'échecs : le serveur local met trop de temps à répondre pour le profil ${profileId}.`);
          resolve(false);
        },
        onabort: () => {
          // Annulée volontairement (un nouveau coup a été joué avant que ce
          // profil ait répondu -- voir abortInFlightRequests). Rien à
          // afficher, la nouvelle position a déjà sa propre volée de requêtes.
          inFlightRequests.delete(handle);
          resolve(false);
        },
      });
      // Mémorise le handle pour pouvoir couper cette requête net si un
      // nouveau coup est joué avant sa réponse (voir abortInFlightRequests).
      // Certains gestionnaires (vieux Greasemonkey) ne retournent pas de
      // handle abortable -- on ne mémorise que si .abort existe, sinon on
      // retombe simplement sur l'ancien comportement (garde-fou JS + stale
      // serveur) pour cette requête.
      if (handle && typeof handle.abort === "function") {
        inFlightRequests.add(handle);
      }
    });
  }

  // Coupe net toutes les requêtes profil encore en vol. Appelé dès qu'un
  // NOUVEAU coup est détecté (voir onBoardChanged) : sans ça, les 2-3
  // requêtes de l'ancienne position que le serveur n'a pas encore eu le
  // temps de marquer "stale" (fenêtre entre la libération du verrou moteur
  // et l'arrivée du poll de la nouvelle position) continuaient de tenir le
  // verrou et de calculer un coup déjà obsolète -- ce qui retardait d'autant
  // l'analyse de la VRAIE nouvelle position. Les couper libère le verrou
  // moteur tout de suite pour la position réellement à l'écran.
  function abortInFlightRequests() {
    if (inFlightRequests.size === 0) return;
    for (const handle of inFlightRequests) {
      try { handle.abort(); } catch (e) { /* déjà terminée : sans effet */ }
    }
    inFlightRequests.clear();
  }


  function handleCoachPayload(data, boardPart, turn) {
    // Verrou = clé (position DES PIÈCES + TRAIT) de CETTE requête (voir
    // onBoardChanged, lastSentKey). On la reçoit en paramètre plutôt que de la
    // reconstruire depuis un état global : si une requête plus récente est
    // partie entre-temps, une réponse tardive ne doit pas verrouiller la
    // mauvaise clé. Un "skip" sur un trait donné ne bloque plus le renvoi si
    // le trait se corrige ensuite (la clé change).
    const lockKey = `${boardPart}:${turn}`;

    // Garde-fou anti-traînard : si un coup a été joué pendant que CETTE
    // requête (partie pour l'ANCIENNE position) était encore en vol, le
    // serveur peut très bien répondre AVANT d'avoir été informé du nouveau
    // coup (aucune notification push -- il ne le saura qu'au prochain poll
    // JS) et renvoyer un résultat parfaitement valide, mais pour une
    // position qui n'existe déjà plus à l'écran. `data.stale` ne couvre que
    // le cas où le serveur s'en est rendu compte LUI-MÊME ; ici on vérifie
    // côté navigateur que cette réponse correspond encore à la position
    // actuellement suivie (inFlightKey pendant un envoi en cours,
    // lastSentKey une fois verrouillée) avant d'en faire quoi que ce soit --
    // sinon on l'ignore silencieusement, une réponse plus fraîche est déjà
    // en route.
    if (lockKey !== inFlightKey && lockKey !== lastSentKey) {
      return;
    }

    if (data.stale) {
      return; // position déjà dépassée entre-temps côté serveur, rien à afficher/verrouiller
    }
    if (data.error) {
      console.warn("Coach d'échecs :", data.error);
      clearArrows();
      lastSentKey = lockKey; // erreur applicative : pas la peine de retenter, elle échouera pareil
    } else if (data.game_over) {
      clearArrows();
      lastSentKey = lockKey;
    } else if (data.skip) {
      // Pas le tour du camp choisi : pas de flèches à afficher pour le coup
      // de l'adversaire. On verrouille SUR CE TRAIT précis -- si le trait est
      // corrigé au poll suivant (surlignage enfin lisible), la clé change et
      // le renvoi se fait automatiquement (plus besoin de "Corriger le trait").
      clearArrows();
      lastSentKey = lockKey;
    } else if (data.profile) {
      // Le résultat d'un profil vient d'arriver -> on met à jour
      // uniquement la flèche correspondante, les autres restent affichées
      // telles quelles en attendant leur propre mise à jour.
      currentProfileEntries[data.profile] = data;
      // Idem côté serveur (voir handle_single_profile, entry["theory_move"]) :
      // chaque réponse de profil est une nouvelle chance de récupérer la
      // suggestion théorique si le cache Lichess s'est rempli entre-temps
      //.
      if (data.theory_move && data.theory_move.move_uci) {
        currentTheoryEntry = data.theory_move;
      }
      redrawProfileArrows();
      lastSentKey = lockKey;
    }
  }

  // ---------------------------------------------------------------------
  // 3. Dessin des flèches directement sur la page
  // ---------------------------------------------------------------------

  function getOrCreateSvgLayer(els) {
    let svg = document.getElementById("chess-coach-arrows");
    // Ancre le calque : sur chess.com directement sur wc-chess-board (déjà
    // en 0-100%, comme son propre SVG de coordonnées) ; sur chessground sur
    // cg-container (en pixels, comme avant).
    const anchor = els.type === "chesscom" ? els.board : els.container;
    const size = els.type === "chesscom"
      ? (els.board.offsetWidth || els.board.getBoundingClientRect().width || 600)
      : (els.container.offsetWidth || 688);

    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = "chess-coach-arrows";
      svg.style.position = "absolute";
      svg.style.top = "0";
      svg.style.left = "0";
      svg.style.width = "100%";
      svg.style.height = "100%";
      svg.style.pointerEvents = "none";
      svg.style.zIndex = "9999";

      const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
      Object.entries(PROFILE_STYLE).forEach(([profileId, style]) => {
        const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
        marker.setAttribute("id", `cc-arrowhead-${profileId}`);
        marker.setAttribute("markerWidth", "6");
        marker.setAttribute("markerHeight", "6");
        marker.setAttribute("refX", "3");
        marker.setAttribute("refY", "3");
        marker.setAttribute("orient", "auto");
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", "M0,0 L6,3 L0,6 Z");
        path.setAttribute("fill", style.color);
        marker.appendChild(path);
        defs.appendChild(marker);
      });
      svg.appendChild(defs);

      // S'assure que le conteneur peut recevoir un enfant positionné en absolu.
      if (getComputedStyle(anchor).position === "static") {
        anchor.style.position = "relative";
      }
      anchor.appendChild(svg);
    }
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
    // chess.com : repère 0-100 (pourcentage du plateau), squareSize vaut
    // déjà 12.5 (100/8) -- même formule, juste une unité différente.
    return { x: col * squareSize + squareSize / 2, y: row * squareSize + squareSize / 2 };
  }

  function clearArrows() {
    currentProfileEntries = {};
    currentTheoryEntry = null;
    lastDrawnMovesKey = null;
    const svg = document.getElementById("chess-coach-arrows");
    if (svg) {
      svg.querySelectorAll("line, circle.cc-label-bg, text.cc-label").forEach((n) => n.remove());
    }
  }

  function redrawProfileArrows() {
    // Redessine TOUTES les flèches actuellement connues pour la position en
    // cours (jusqu'à 3 : bleu/rose/blanc = popular/creative/classical), à
    // partir de currentProfileEntries. Appelé à chaque nouveau profil reçu --
    // pas cher (3 lignes max).
    //
    // EXCEPTION : si un coup théorique est reconnu (currentTheoryEntry), on
    // n'affiche QUE la flèche théorique -- les 3 flèches de profils sont
    // masquées tant qu'on reste en théorie connue. Avant, les 4 flèches se
    // superposaient (le moteur propose souvent un coup DIFFÉRENT du coup de
    // référence en tout début de partie), ce qui rendait la suggestion de
    // livre illisible et contredisait l'idée même de "reste en théorie".
    const inTheory = !!(currentTheoryEntry && currentTheoryEntry.move_uci);
    const profileIds = inTheory
      ? []
      : Object.keys(currentProfileEntries).sort(
          (a, b) => PROFILE_IDS.indexOf(a) - PROFILE_IDS.indexOf(b)
        );
    const movesKey = JSON.stringify([
      ...profileIds.map((p) => `${p}:${currentProfileEntries[p].move_uci}`),
      // Sans cette entrée, l'arrivée/le changement de la seule flèche
      // théorique (les 3 profils restant sur les mêmes coups) serait
      // silencieusement ignoré par le court-circuit ci-dessous.
      currentTheoryEntry ? `theory:${currentTheoryEntry.move_uci}` : "theory:none",
    ]);
    if (movesKey === lastDrawnMovesKey) return; // déjà affiché tel quel, rien à refaire

    const els = getBoardElements();
    if (!els) return;
    const { isWhiteOrientation, squareSize } = readGrid(els);
    const svg = getOrCreateSvgLayer(els);

    // Efface uniquement les lignes existantes (pas currentProfileEntries,
    // qu'on est justement en train d'utiliser pour redessiner).
    svg.querySelectorAll("line").forEach((n) => n.remove());
    lastDrawnMovesKey = movesKey;

    profileIds.forEach((profileId) => {
      const entry = currentProfileEntries[profileId];
      const uci = entry.move_uci;
      if (!uci) return;
      const style = PROFILE_STYLE[profileId] || { color: "#cccccc", width: 5, opacity: 0.6 };
      const fromSq = uci.slice(0, 2);
      const toSq = uci.slice(2, 4);
      const from = squareToXY(fromSq, squareSize, isWhiteOrientation);
      const to = squareToXY(toSq, squareSize, isWhiteOrientation);

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
      line.setAttribute("stroke", style.color);
      line.setAttribute("stroke-width", style.width);
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("opacity", style.opacity);
      line.setAttribute("marker-end", `url(#cc-arrowhead-${profileId})`);
      svg.appendChild(line);
    });

    // Flèche "coup théorique" (livre polyglot / base ECO / Lichess -- voir
    // THEORY_STYLE plus haut pour le détail des sources) -- dessinée à
    // part, en pointillé (voir THEORY_STYLE), jamais confondue avec les
    // flèches pleines des profils (masquées de toute façon tant qu'on est
    // en théorie, voir plus haut). Pas de marker-end dédié : la couleur +
    // le pointillé suffisent à la distinguer sans toucher au <defs> existant.
    if (currentTheoryEntry && currentTheoryEntry.move_uci) {
      const uci = currentTheoryEntry.move_uci;
      const fromSq = uci.slice(0, 2);
      const toSq = uci.slice(2, 4);
      const from = squareToXY(fromSq, squareSize, isWhiteOrientation);
      const to = squareToXY(toSq, squareSize, isWhiteOrientation);
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const len = Math.hypot(dx, dy) || 1;
      const shorten = squareSize * 0.35;

      const theoryLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
      theoryLine.setAttribute("x1", from.x);
      theoryLine.setAttribute("y1", from.y);
      theoryLine.setAttribute("x2", to.x - (dx / len) * shorten);
      theoryLine.setAttribute("y2", to.y - (dy / len) * shorten);
      theoryLine.setAttribute("stroke", THEORY_STYLE.color);
      theoryLine.setAttribute("stroke-width", THEORY_STYLE.width);
      theoryLine.setAttribute("stroke-linecap", "round");
      theoryLine.setAttribute("opacity", THEORY_STYLE.opacity);
      theoryLine.setAttribute("stroke-dasharray", "10,6");
      svg.appendChild(theoryLine);
    }
  }


  // ---------------------------------------------------------------------
  // 4. Surveillance du plateau (détecte chaque coup joué)
  // ---------------------------------------------------------------------

  // Position de départ standard : sert à détecter qu'une NOUVELLE partie
  // vient de commencer (plutôt qu'un simple coup dans la partie en cours),
  // pour réinitialiser tout l'état interne du script (sinon des restes de
  // l'ancienne partie -- dernier plateau connu, camp actif, flèches --
  // pouvaient fausser la lecture des tout premiers coups de la partie
  // suivante).
  const START_BOARD_PART = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR";

  function resetTrackingState(reason) {
    console.log(`♟ Coach d'échecs : réinitialisation de l'état (${reason}).`);
    lastSentKey = null;
    inFlightKey = null;
    localTurnToggle = "w";
    lastStableGrid = null;
    pendingBoardPart = null;
    pendingStableCount = 0;
    forcedTurnValue = null;      // une correction manuelle ne survit pas à une nouvelle partie
    forcedTurnBoardPart = null;
    clearArrows();
  }

  function onBoardChanged() {
    // readBoardState() gère lui-même les états transitoires (0 pièce,
    // glissement en cours) en renvoyant null -> une seule lecture DOM par tick
    // (voir #2), au lieu de l'ancien countPieces() qui re-scannait le document.
    const state = readBoardState();
    if (!state) return;
    const { grid, boardPart, whiteKings, blackKings, isWhiteOrientation, els } = state;

    // Nouvelle partie détectée (retour à la position de départ alors qu'on
    // avait déjà une position différente en mémoire) -> on oublie tout ce
    // qui concerne l'ancienne partie avant de continuer, plutôt que de
    // comparer la position de départ à la dernière position de la partie
    // précédente (ce qui donnerait un diff n'importe quoi et un trait
    // déduit au hasard pour les premiers coups).
    if (boardPart === START_BOARD_PART && lastStableGrid !== null) {
      const wasDifferent = gridToFenBoardPart(lastStableGrid) !== START_BOARD_PART;
      if (wasDifferent) {
        resetTrackingState("nouvelle partie détectée (retour à la position de départ)");
      }
    }

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

    // Garde-fou : même stable, une position sans exactement 1 roi de chaque
    // couleur est forcément une mauvaise lecture -> pas la peine d'embêter
    // le serveur avec, on retentera au prochain poll.
    if (whiteKings !== 1 || blackKings !== 1) return;

    // Le plateau est stable et valide -> on détermine le trait MAINTENANT
    // (avant la déduplication), car la clé anti-renvoi inclut désormais le
    // trait, pas seulement la position des pièces. Voir ci-dessous.
    // Couleur auto : l'orientation du plateau donne mon camp (mes pièces sont
    // toujours en bas). On l'envoie au serveur pour qu'il cale set_my_side
    // tout seul -- le bouton manuel de la fenêtre Python devient un simple
    // override optionnel. Best-effort : jamais bloquant pour le dessin.
    const detectedSide = isWhiteOrientation ? "w" : "b";
    // Cases du dernier coup lues UNE SEULE fois ici (voir readLastMoveSquares) :
    // partagées entre la déduction du trait ET le calcul de la case en passant,
    // pour ne pas scanner le DOM deux fois.
    const lastMoveSquares = readLastMoveSquares(els);
    const turn = getSideToMove(grid, lastMoveSquares, boardPart);

    // Indicateur d'état (voir updateStatusIndicator) : reflète trait + camp
    // détectés à CHAQUE tick stable, même quand on ne renvoie pas (dédup) --
    // l'utilisateur voit en direct ce que le coach "pense", sans cliquer.
    updateStatusIndicator({ turn, side: detectedSide });

    // Déduplication par (position DES PIÈCES + TRAIT). Historiquement, la clé
    // ne portait QUE les pièces : après un "skip" (trait mal déduit -> serveur
    // répond "pas ton tour"), la position se verrouillait et le trait corrigé
    // au poll suivant n'était JAMAIS re-testé -> il fallait cliquer "Corriger
    // le trait" pour débloquer. En incluant le trait dans la clé, une position
    // identique dont le trait a changé (ex: surlignage du dernier coup enfin
    // lisible) est automatiquement renvoyée.
    //
    // Ceci ne réintroduit PAS la boucle infinie que craignait l'ancien
    // commentaire : cette boucle venait d'un trait qui S'INVERSAIT à chaque
    // envoi (ancien compteur alterné). Le trait actuel est DÉTERMINISTE pour
    // une position + un état DOM donnés (lu du surlignage, ou déduit par diff
    // qui retombe sur une valeur stable une fois lastStableGrid figé) -> sur
    // un plateau statique, la clé reste identique, donc aucun renvoi en boucle.
    const sentKey = `${boardPart}:${turn}`;
    if (sentKey === lastSentKey) return; // même position ET même trait déjà envoyés -> rien à refaire
    // Garde in-flight sur la CLÉ COMPLÈTE (pièces + trait), pas les pièces
    // seules : sinon une requête en vol avec un trait erroné (ex: "skip")
    // bloquerait le renvoi de la MÊME position dont le trait vient d'être
    // corrigé -- ce qui annulerait le déblocage automatique. Avec la clé
    // complète, un trait corrigé pendant une requête lente déclenche bien un
    // nouvel envoi (au pire une requête en double, rare et sans conséquence).
    if (sentKey === inFlightKey) return;

    // FEN COMPLET (voir #1) : droits de roque et case en passant RÉELS, au
    // lieu du "KQkq - " figé qui faisait manquer les prises en passant et
    // suggérer des roques illégaux. Les compteurs (demi-coups / n° de coup)
    // restent à "0 1" faute d'info fiable côté DOM -- sans effet sur l'analyse
    // à court terme (juste la règle des 50 coups, hors sujet ici).
    const castling = castlingRightsFromGrid(grid);
    const enPassant = enPassantFromGrid(lastMoveSquares, grid);
    const finalFen = `${boardPart} ${turn} ${castling} ${enPassant} 0 1`;

    // Un nouveau coup est confirmé (on a passé les deux guards ci-dessus) :
    // toute requête profil encore en vol porte forcément l'ANCIENNE position.
    // On les coupe net pour libérer le verrou moteur côté serveur tout de
    // suite, au lieu de le laisser finir un calcul déjà obsolète pendant que
    // la vraie nouvelle position attend derrière (voir abortInFlightRequests).
    abortInFlightRequests();

    inFlightKey = sentKey;
    lastStableGrid = grid;
    // On NE verrouille plus lastSentKey ici : si l'envoi échoue (timeout,
    // serveur down...), cette position doit rester "à retenter" au prochain
    // poll. Le verrouillage se fait uniquement en cas de succès confirmé,
    // dans handleCoachPayload().
    sendFenToCoach(finalFen, boardPart, detectedSide, turn);
  }

  // ---------------------------------------------------------------------
  // 5. Correction manuelle (boutons flottants injectés sur la page)
  // ---------------------------------------------------------------------
  // Comme on ne peut pas toucher au code du site, ces boutons sont ajoutés
  // directement par le script -- aucune coopération du site nécessaire.

  function forceRefresh() {
    // Force un nouvel envoi immédiat, même si le plateau "semble" identique
    // au dernier envoi confirmé, et sans attendre les lectures de stabilité
    // habituelles (l'utilisateur a explicitement demandé un recalcul, donc
    // pas la peine de re-filtrer).
    lastSentKey = null;
    const state = readBoardState();
    if (!state) return;
    pendingBoardPart = state.boardPart;
    pendingStableCount = STABLE_READS_REQUIRED;
    onBoardChanged();
  }

  function forceTurnFlipAndRefresh() {
    // "Le coach pense que c'est à l'adversaire, mais c'est en fait mon
    // tour (ou l'inverse)" -- corrige le trait déduit puis relance
    // immédiatement une analyse avec la valeur corrigée. La correction est
    // ANCRÉE sur la position actuelle des pièces (forcedTurnBoardPart) et
    // tient jusqu'au prochain vrai coup -- voir getSideToMove.
    const flipped = localTurnToggle === "w" ? "b" : "w";
    forcedTurnValue = flipped;
    const state = readBoardState();
    forcedTurnBoardPart = state ? state.boardPart : null;
    forceRefresh();
  }

  // Indicateur d'état (voir #4) : évite de cliquer "Recalculer"/"Corriger le
  // trait" à l'aveugle. Reflète 3 infos que le script connaît déjà -- serveur
  // joignable, trait détecté, ton camp -- sans aucune lecture DOM/réseau en
  // plus (juste l'affichage d'un état déjà calculé). null = pas encore connu.
  let statusServerOk = null;   // true / false / null
  let statusTurn = null;       // "w" / "b" / null (à qui de jouer)
  let statusSide = null;       // "w" / "b" / null (mon camp)
  let statusLastRender = null; // dernier rendu appliqué au DOM (dédup, voir plus bas)

  // Point d'entrée unique : on ne passe que les champs qui changent (patch
  // partiel), les autres gardent leur dernière valeur connue. Puis on redessine.
  function updateStatusIndicator({ serverOk, turn, side } = {}) {
    if (serverOk !== undefined) statusServerOk = serverOk;
    if (turn !== undefined) statusTurn = turn;
    if (side !== undefined) statusSide = side;
    renderStatus();
  }

  // Calcule le texte + la couleur du voyant à partir de l'état courant. Séparé
  // de l'écriture DOM pour pouvoir dédupliquer (appelé à chaque tick stable via
  // onBoardChanged -- inutile de réécrire le DOM si rien n'a changé, cf #2).
  function computeStatusRender() {
    if (statusServerOk === false) {
      return { text: "● Serveur injoignable", color: "#f38ba8" };
    }
    const sideTxt = statusSide === "w" ? "Blancs" : statusSide === "b" ? "Noirs" : "?";
    // statusTurn comparé au camp : à toi de jouer, ou au tour de l'adversaire.
    let turnTxt;
    if (statusTurn === null || statusSide === null) {
      turnTxt = "en attente";
    } else if (statusTurn === statusSide) {
      turnTxt = "à toi de jouer";
    } else {
      turnTxt = "tour adverse";
    }
    return {
      text: `● ${turnTxt} (tu joues ${sideTxt})`,
      color: statusTurn === statusSide ? "#a6e3a1" : "#f9e2af",
    };
  }

  function renderStatus() {
    const el = document.getElementById("chess-coach-status");
    if (!el) return;
    const { text, color } = computeStatusRender();
    const signature = `${text}|${color}`;
    if (signature === statusLastRender) return; // rien n'a changé -> pas de toucher DOM
    statusLastRender = signature;
    el.textContent = text;
    el.style.color = color;
  }

  function injectControls() {
    if (document.getElementById("chess-coach-controls")) return;
    const box = document.createElement("div");
    box.id = "chess-coach-controls";
    box.style.position = "fixed";
    box.style.bottom = "16px";
    box.style.right = "16px";
    box.style.zIndex = "10000";
    box.style.display = "flex";
    box.style.flexDirection = "column";
    box.style.gap = "6px";
    box.style.fontFamily = "Arial, sans-serif";

    const status = document.createElement("div");
    status.id = "chess-coach-status";
    status.style.padding = "6px 10px";
    status.style.borderRadius = "8px";
    status.style.background = "rgba(30,30,46,0.85)";
    status.style.color = "#cdd6f4";
    status.style.fontSize = "12px";
    status.style.fontWeight = "bold";
    status.style.textAlign = "center";
    status.textContent = "● démarrage...";
    box.appendChild(status);

    const makeButton = (label, title, onClick) => {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.title = title;
      btn.style.padding = "8px 12px";
      btn.style.borderRadius = "8px";
      btn.style.border = "none";
      btn.style.cursor = "pointer";
      btn.style.fontSize = "13px";
      btn.style.fontWeight = "bold";
      btn.style.color = "#1e1e2e";
      btn.style.background = "#89b4fa";
      btn.style.boxShadow = "0 2px 6px rgba(0,0,0,0.3)";
      btn.addEventListener("click", onClick);
      return btn;
    };

    const refreshBtn = makeButton(
      "🔁 Recalculer",
      "Le coach semble bloqué sur un ancien coup : force un nouveau calcul immédiat.",
      forceRefresh
    );

    const flipBtn = makeButton(
      "⇄ Corriger le trait",
      "Le coach pense que c'est à l'adversaire de jouer, mais c'est en fait ton tour (ou l'inverse) : corrige et recalcule.",
      forceTurnFlipAndRefresh
    );
    flipBtn.style.background = "#f38ba8";

    box.appendChild(refreshBtn);
    box.appendChild(flipBtn);
    document.body.appendChild(box);
  }

  function startWatching() {
    const els = getBoardElements();
    if (!els) {
      // La page n'a pas encore fini de charger l'échiquier -> réessaie.
      setTimeout(startWatching, 500);
      return;
    }
    console.log("♟ Coach d'échecs connecté : lecture directe du plateau (aucune capture d'écran).");
    injectControls();
    // Vérification périodique plutôt qu'un MutationObserver : plus simple
    // et insensible aux cas où le site remplace/redessine entièrement le
    // plateau entre deux coups (ce qui pouvait faire rater une mise à jour
    // avec l'ancienne approche basée sur les mutations DOM).
    setInterval(onBoardChanged, POLL_INTERVAL_MS);
    onBoardChanged(); // première tentative immédiate

    // Les navigateurs ralentissent fortement setInterval() sur un onglet en
    // arrière-plan (throttling, pour économiser la batterie) -- c'est une
    // limitation du navigateur, pas de ce script, et il n'y a pas de vrai
    // contournement pour "changer de fenêtre sans jamais rien perdre".
    // Ce qu'on PEUT faire : dès que l'onglet redevient actif, vérifier tout
    // de suite l'état du plateau au lieu d'attendre le prochain tick throttlé
    // -> tu vois la bonne analyse dès que tu reviens, sans délai de rattrapage.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        onBoardChanged();
      }
    });
  }

  startWatching();
})();
