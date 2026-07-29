"""
Prophylaxie : ce que le coup recommandé EMPÊCHE.

C'est le seul « pourquoi » profond d'un coup calme -- un coup tranquille est
rarement bon pour ce qu'il fait, il est bon pour ce qu'il enlève à
l'adversaire.

Deux temps, dont un seul coûte un appel moteur :

1. opponent_threat() -- on analyse la position avec le TRAIT INVERSÉ (coup
   nul) pour savoir ce que l'adversaire jouerait s'il avait la main. Un appel,
   profondeur 10, mesuré à 87 ms sur le moteur de fond (1 thread, hash 128 Mo).

2. prevented_by() -- python-chess pur, aucun appel supplémentaire. On ne
   réclame la prophylaxie que dans le cas INDISCUTABLE : après mon coup, la
   menace n'est plus LÉGALE. « La menace est devenue moins bonne » demanderait
   une seconde analyse et resterait discutable ; « la menace est illégale » est
   un fait binaire, vérifiable, et suffisant.
"""
import chess
import chess.engine

# Profondeur de la sonde de menace adverse, alignée sur la profondeur qui
# dessine la flèche (ELO_TIERS tier 1 : 13-15) -- une menace vue moins loin
# que le coup recommandé produit des « ce coup empêche X » où X n'est plus la
# vraie menace.
#
# Coût MESURÉ (stockfish.exe local, threads=1, 4 positions ouverture/milieu/
# finale) : depth 10 -> 4-34 ms, 12 -> 8-23 ms, 14 -> 7-98 ms, 16 -> 21-120 ms.
# 14 est le compromis retenu : la sonde est SYNCHRONE dans le fil de la
# requête, juste avant l'affichage (voir web_bridge._opponent_threat), mais
# le cache par FEN la fait payer UNE fois par position pour les 3 profils.
# Au-delà de 16, repasser cette mesure avant de monter.
THREAT_DEPTH = 14


def opponent_threat(engine, board, depth=THREAT_DEPTH):
    """
    Coup que l'adversaire jouerait s'il avait le trait, ou None.

    GARDE NON NÉGOCIABLE sur board.is_check() : le coup nul est illégal quand
    on est en échec, et python-chess NE LÈVE AUCUNE ERREUR dans ce cas -- il
    fabrique silencieusement une position invalide (is_valid() -> False). Sans
    cette garde on enverrait au moteur une position illégale et on
    exploiterait ce qu'il en retourne. C'est le piège principal de ce module.

    Retourne None (jamais d'exception) si : échec, partie finie, moteur absent
    ou moteur en échec -- « on ne sait pas », jamais une valeur par défaut.
    """
    if engine is None or board.is_check() or board.is_game_over():
        return None
    probe = board.copy()
    probe.push(chess.Move.null())
    if not probe.is_valid():
        return None  # ceinture et bretelles : on ne parle pas à un moteur d'une position illégale
    try:
        info = engine.engine.analyse(probe, chess.engine.Limit(depth=depth))
    except Exception as e:
        print(f"⚠ Menace adverse indisponible : {e}")
        return None
    pv = info.get("pv") or []
    return pv[0] if pv else None


def prevented_by(board, my_move, threat):
    """
    Preuve que `threat` (coup ADVERSE, légal dans `board` trait inversé) n'est
    plus jouable après `my_move`, ou None.

    board  : position AVANT mon coup, à MOI de jouer.
    my_move: mon coup (chess.Move).
    threat : la menace rendue par opponent_threat, ou None.

    Retourne {"san": <SAN de la menace>, "reason": "captured"|"blocked"} :
      - "captured" : la pièce qui devait jouer la menace n'est plus là ;
      - "blocked"  : elle est là, mais le coup n'est plus légal (interposition,
                     clouage, case occupée).
    None dès que la menace reste légale -- aucune prophylaxie réclamée.
    """
    if threat is None or my_move is None:
        return None
    try:
        # SAN calculé sur la position À TRAIT INVERSÉ : c'est la seule où la
        # menace est légale, donc la seule où board.san() est correct.
        probe = board.copy()
        probe.push(chess.Move.null())
        if not probe.is_valid() or threat not in probe.legal_moves:
            return None
        san = probe.san(threat)

        # Après MON coup, c'est déjà le trait de l'adversaire (board.push
        # avance le trait) -- inutile et FAUX de repousser un coup nul ici :
        # ça ramènerait le trait à moi et viderait legal_moves des coups
        # adverses, faisant croire à tort que la menace a disparu.
        after = board.copy()
        after.push(my_move)
        if not after.is_valid():
            return None
        if threat in after.legal_moves:
            return None                 # la menace survit : rien à raconter

        # La pièce qui devait jouer la menace est "partie" si sa case de
        # départ n'est plus occupée par une pièce ADVERSE -- pas seulement si
        # la case est vide : si mon coup capture cette pièce, MA pièce occupe
        # maintenant cette case, ce qui n'est pas "vide" mais est bien une
        # capture.
        threat_color = not board.turn
        piece = after.piece_at(threat.from_square)
        piece_gone = piece is None or piece.color != threat_color
        return {"san": san, "reason": "captured" if piece_gone else "blocked"}
    except Exception as e:
        print(f"⚠ Prophylaxie indisponible : {e}")
        return None
