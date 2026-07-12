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
 *
 * ⚠ RECOMMANDÉ SI TU PRE-MOVES (préshot des coups rapidement) :
 *   Par défaut, le script DEVINE qui vient de jouer en comparant 2 lectures
 *   du plateau et en repérant la première case qui a changé. Si 2 coups (le
 *   tien + celui de l'adversaire) se jouent tous les deux avant la lecture
 *   suivante, cette déduction peut se tromper de camp -- pas juste être en
 *   retard, vraiment se tromper -- et rester désynchronisée jusqu'au
 *   prochain coup. Puisque c'est TON site, la solution fiable à 100% est de
 *   lui faire exposer directement le trait actuel, en ajoutant dans le JS
 *   de ta page (PAS dans ce fichier) :
 *
 *       window.chessCoachGetTurn = function() {
 *         return monEtatDePartie.trait === "blanc" ? "w" : "b";
 *         // adapte à la variable/fonction qui donne déjà le trait sur ton site
 *       };
 *
 *   Dès que cette fonction existe, ce script l'utilise automatiquement en
 *   priorité (voir getSideToMove() plus bas) et la déduction par diff n'est
 *   plus qu'un repli pour les autres sites.
 * -----------------------------------------------------------------------
 */
(function () {
  const COACH_ENDPOINT = "http://127.0.0.1:8765/fen";
  // 4 profils de jeu "humains" (voir human_profile.py côté serveur) --
  // l'ordre ici doit rester cohérent avec human_profile.PROFILE_IDS.
  // Le niveau Elo (slider dans la fenêtre Python) ne change PAS ces
  // couleurs/profils : il change la fenêtre de tolérance utilisée par le
  // serveur pour choisir CHAQUE coup, en amont de ce script.
  const PROFILE_IDS = ["solid", "popular", "creative", "classical"];
  const PROFILE_STYLE = {
    // width décroissant + opacity croissant : quand plusieurs profils
    // tombent d'accord sur le même coup, les flèches se superposent en
    // formant une "cible" au lieu que l'une masque les autres.
    solid:     { color: "#a6e3a1", width: 10, opacity: 0.35 }, // vert : coup solide/simple
    popular:   { color: "#89dceb", width: 7,  opacity: 0.55 }, // bleu : coup fréquent à ce niveau
    creative:  { color: "#f38ba8", width: 4,  opacity: 0.8 },  // rose : coup plus créatif/intuitif
    classical: { color: "#f5f5f5", width: 2,  opacity: 1.0 },  // blanc : coup classique/naturel
  };
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
  const POLL_INTERVAL_MS = 150;

  let lastSentBoardPart = null;
  // Position actuellement en cours d'envoi (requête pas encore résolue).
  // Sans ça, comme lastSentBoardPart n'est plus verrouillé tant que la
  // requête n'a pas réussi, le poll suivant (avant que la 1re requête,
  // qui peut prendre jusqu'à 20s, ait répondu) renverrait la même position
  // en double.
  let inFlightBoardPart = null;
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

  // Correction manuelle ponctuelle : posée par le bouton flottant "⇄
  // Corriger le trait" (voir plus bas). S'applique UNE SEULE fois (au tout
  // prochain calcul du trait), puis se remet à null -- après ça, la
  // déduction automatique reprend normalement à partir de cette valeur
  // corrigée.
  let forcedTurnForNextSend = null;

  function getSideToMove(newGrid) {
    if (window.chessCoachGetTurn) {
      try {
        return window.chessCoachGetTurn();
      } catch (e) {
        console.warn("chessCoachGetTurn() a levé une erreur, repli sur la déduction automatique :", e);
      }
    }
    if (forcedTurnForNextSend !== null) {
      const t = forcedTurnForNextSend;
      forcedTurnForNextSend = null;
      localTurnToggle = t;
      return t;
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

  async function sendFenToCoach(fen, boardPart) {
    // Nouvelle position -> on repart d'un état de flèches vierge, elles
    // seront redessinées une par une au fil des profils reçus.
    currentProfileEntries = {};

    // 1. Aperçu rapide (depth 12, quasi instantané) : les 4 profils d'un
    // coup, affichés immédiatement. On l'ATTEND avant de lancer les vraies
    // requêtes -- sinon rien ne garantit qu'elle arrive au serveur (et
    // donc au verrou moteur) avant les 4 requêtes plus lourdes, ce qui la
    // rendrait aussi lente que le reste. Un petit délai fixe (~100-300ms
    // typiquement), largement rentabilisé par l'affichage immédiat.
    await sendQuickTake(fen, boardPart);

    // 2. Une requête HTTP INDÉPENDANTE par profil (au lieu d'un seul flux
    // streamé) : plus robuste, chaque profil arrive et s'affiche dès qu'IL
    // est prêt, sans dépendre du support d'onprogress() du navigateur/
    // gestionnaire d'extensions (voir le commentaire dans web_bridge.py,
    // handle_single_profile). Remplace automatiquement l'aperçu rapide dès
    // qu'elle arrive (voir handleCoachPayload).
    const results = await Promise.all(
      PROFILE_IDS.map((profileId) => sendOneProfile(fen, boardPart, profileId))
    );

    // Le succès est confirmé si AU MOINS un profil a répondu correctement
    // -- si un seul échoue (timeout ponctuel), on ne bloque pas les autres,
    // mais on ne verrouille la position que si on a eu au moins une réponse
    // exploitable (voir sendOneProfile, qui pose lastSentBoardPart
    // lui-même par profil).
    if (results.every((ok) => !ok)) {
      console.warn("Coach d'échecs : aucun des 4 profils n'a répondu pour cette position.");
    }
    inFlightBoardPart = null;
  }

  function sendQuickTake(fen, boardPart) {
    if (DEBUG) console.log("Coach d'échecs [debug] : envoi aperçu rapide (quick take)...");
    return new Promise((resolve) => {
      GM_xmlhttpRequest({
        method: "POST",
        url: COACH_ENDPOINT,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify({ fen, quick: true }),
        // depth 12 doit être quasi instantané -- pas la peine d'attendre
        // longtemps ; si ça traîne, on laisse juste les vraies requêtes
        // prendre le relais sans aperçu préalable pour cette position.
        timeout: 2000,
        onload: (response) => {
          if (DEBUG) console.log("Coach d'échecs [debug] : aperçu rapide reçu, statut", response.status, "corps brut:", response.responseText);
          let payload;
          try {
            payload = JSON.parse(response.responseText || "{}");
          } catch (e) {
            if (DEBUG) console.warn("Coach d'échecs [debug] : aperçu rapide -- JSON invalide.", e);
            resolve();
            return;
          }
          handleQuickTakePayload(payload, boardPart);
          resolve();
        },
        onerror: (response) => {
          if (DEBUG) console.warn("Coach d'échecs [debug] : aperçu rapide -- erreur réseau.", response);
          resolve();
        },
        ontimeout: () => {
          if (DEBUG) console.warn("Coach d'échecs [debug] : aperçu rapide -- timeout (>2s), abandon pour cette position.");
          resolve();
        },
      });
    });
  }

  function handleQuickTakePayload(data, boardPart) {
    if (data.error || data.game_over || data.skip || data.stale || !data.profiles) {
      if (DEBUG) console.log("Coach d'échecs [debug] : aperçu rapide ignoré, raison :", data);
      return; // rien à afficher pour l'instant, la vraie requête suivra de toute façon
    }
    let updated = false;
    Object.entries(data.profiles).forEach(([profileId, entry]) => {
      // Ne remplace jamais un résultat déjà présent pour ce profil (garde-fou
      // si jamais une vraie réponse arrivait avant, cas rare mais possible) :
      // l'aperçu rapide ne doit jamais écraser un résultat plus fiable.
      if (!currentProfileEntries[profileId]) {
        currentProfileEntries[profileId] = { ...entry, profile: profileId };
        updated = true;
      } else if (DEBUG) {
        console.log(`Coach d'échecs [debug] : aperçu rapide pour ${profileId} ignoré (déjà une entrée présente).`);
      }
    });
    if (DEBUG) console.log("Coach d'échecs [debug] : aperçu rapide -- profils reçus:", Object.keys(data.profiles), "mise à jour appliquée:", updated);
    if (updated) redrawProfileArrows();
  }

  function sendOneProfile(fen, boardPart, profileId) {
    return new Promise((resolve) => {
      GM_xmlhttpRequest({
        method: "POST",
        url: COACH_ENDPOINT,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify({ fen, profile: profileId }),
        // Un profil = 1 analyse MultiPV (+ un avis Elo-bridé rapide) sur
        // une position déjà chargée : quelques secondes grand maximum.
        timeout: 15000,
        onload: (response) => {
          let payload;
          try {
            payload = JSON.parse(response.responseText || "{}");
          } catch (e) {
            console.warn(`Coach d'échecs : réponse invalide du serveur local pour le profil ${profileId}.`, e);
            resolve(false);
            return;
          }
          handleCoachPayload(payload, boardPart);
          resolve(true);
        },
        onerror: () => {
          console.warn(
            `Coach d'échecs : impossible de contacter le serveur local (port 8765) pour le profil ${profileId}. ` +
            "Vérifie que le programme Python tourne bien (option 'Mode navigateur')."
          );
          resolve(false);
        },
        ontimeout: () => {
          console.warn(`Coach d'échecs : le serveur local met trop de temps à répondre pour le profil ${profileId}.`);
          resolve(false);
        },
      });
    });
  }

  function handleCoachPayload(data, boardPart) {
    if (data.stale) {
      return; // position déjà dépassée entre-temps côté serveur, rien à afficher/verrouiller
    }
    if (data.error) {
      console.warn("Coach d'échecs :", data.error);
      clearArrows();
      lastSentBoardPart = boardPart; // erreur applicative : pas la peine de retenter, elle échouera pareil
    } else if (data.game_over) {
      clearArrows();
      lastSentBoardPart = boardPart;
    } else if (data.skip) {
      // Pas le tour du camp choisi (bouton "Changer de camp") : pas de
      // flèches à afficher pour le coup de l'adversaire.
      clearArrows();
      lastSentBoardPart = boardPart;
    } else if (data.profile) {
      // Le résultat d'un profil vient d'arriver -> on met à jour
      // uniquement la flèche correspondante, les autres restent affichées
      // telles quelles en attendant leur propre mise à jour.
      currentProfileEntries[data.profile] = data;
      redrawProfileArrows();
      lastSentBoardPart = boardPart;
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
    currentProfileEntries = {};
    lastDrawnMovesKey = null;
    const svg = document.getElementById("chess-coach-arrows");
    if (svg) {
      svg.querySelectorAll("line, circle.cc-label-bg, text.cc-label").forEach((n) => n.remove());
    }
  }

  function redrawProfileArrows() {
    // Redessine TOUTES les flèches actuellement connues pour la position en
    // cours (jusqu'à 4 : vert/bleu/rose/blanc), à partir de
    // currentProfileEntries. Appelé à chaque nouveau profil reçu -- pas
    // cher (4 lignes max).
    const profileIds = Object.keys(currentProfileEntries).sort(
      (a, b) => PROFILE_IDS.indexOf(a) - PROFILE_IDS.indexOf(b)
    );
    const movesKey = JSON.stringify(profileIds.map((p) => `${p}:${currentProfileEntries[p].move_uci}`));
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
    lastSentBoardPart = null;
    inFlightBoardPart = null;
    localTurnToggle = "w";
    lastStableGrid = null;
    pendingBoardPart = null;
    pendingStableCount = 0;
    clearArrows();
  }

  function onBoardChanged() {
    if (countPieces() === 0) return; // état transitoire probable, on retente au prochain tick
    const state = readBoardState();
    if (!state) return;
    const { grid, boardPart, whiteKings, blackKings } = state;

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

    if (boardPart === lastSentBoardPart) return; // le plateau n'a pas vraiment changé, rien à refaire
    if (boardPart === inFlightBoardPart) return; // une requête pour cette même position est déjà en cours

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
    inFlightBoardPart = boardPart;
    // On NE verrouille plus lastSentBoardPart ici : si l'envoi échoue
    // (timeout, serveur down...), cette position doit rester "à retenter"
    // au prochain poll. Le verrouillage se fait uniquement en cas de succès
    // confirmé, dans sendFenToCoach() ci-dessous.
    sendFenToCoach(finalFen, boardPart);
  }

  function countPieces() {
    const els = getBoardElements();
    if (!els) return 0;
    return els.board.querySelectorAll("piece").length;
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
    lastSentBoardPart = null;
    const state = readBoardState();
    if (!state) return;
    pendingBoardPart = state.boardPart;
    pendingStableCount = STABLE_READS_REQUIRED;
    onBoardChanged();
  }

  function forceTurnFlipAndRefresh() {
    // "Le coach pense que c'est à l'adversaire, mais c'est en fait mon
    // tour (ou l'inverse)" -- corrige le trait déduit puis relance
    // immédiatement une analyse avec la valeur corrigée.
    forcedTurnForNextSend = localTurnToggle === "w" ? "b" : "w";
    forceRefresh();
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
