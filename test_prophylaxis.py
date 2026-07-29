"""Prophylaxie : ce que le coup EMPECHE.

La moitie testee ici est la moitie PURE (prevented_by) -- aucun moteur. La
moitie moteur (opponent_threat) n'est testee que sur sa garde d'echec, le seul
point ou elle peut produire une position ILLEGALE : python-chess accepte
board.push(chess.Move.null()) en echec sans lever la moindre erreur et fabrique
silencieusement une position invalide (is_valid() -> False).
"""
import chess

import prophylaxis


def test_menace_annulee_par_capture_de_la_piece():
    # Cavalier noir en g4 pret a sauter en f2 (capture le pion) ; Bxg4
    # supprime la piece qui devait jouer la menace.
    # FEN corrigee : la FEN du brief ("... w kq ...") place un cavalier noir
    # en g4 alors que c'est aux BLANCS de jouer et son fou c4 ne peut pas
    # atteindre g4 -- ni "Nxf2" (coup noir) ni "Bxg4" n'y sont legaux. Position
    # minimale reconstruite avec la meme intention : Bh3 peut prendre Ng4, qui
    # menace Nxf2.
    board = chess.Board("4k3/8/8/8/6n1/7B/5P2/4K3 w - - 0 1")
    probe = board.copy()
    probe.push(chess.Move.null())
    threat = probe.parse_san("Nxf2")            # coup NOIR, la menace
    my_move = board.parse_san("Bxg4")            # je prends le cavalier
    out = prophylaxis.prevented_by(board, my_move, threat)
    assert out is not None
    assert out["reason"] == "captured"
    assert out["san"] == "Nxf2"


def test_menace_toujours_disponible_ne_reclame_rien():
    board = chess.Board("4k3/8/8/8/6n1/7B/5P2/4K3 w - - 0 1")
    probe = board.copy()
    probe.push(chess.Move.null())
    threat = probe.parse_san("Nxf2")
    my_move = board.parse_san("Kd1")             # ne touche pas au cavalier de g4
    assert prophylaxis.prevented_by(board, my_move, threat) is None


def test_menace_annulee_parce_que_la_case_est_occupee():
    """Le coup n'enleve pas la piece menacante, mais rend le coup illegal :
    reason doit valoir "blocked", pas "captured"."""
    # Dame noire en a7, menace Qxg1 le long de la diagonale a7-g1 (rien entre
    # les deux). Mon coup Ne3 interpose le cavalier c2 sur cette diagonale :
    # la dame est toujours vivante et son trajet vers g1 n'est plus legal.
    board = chess.Board("4k3/q7/8/8/8/8/2N5/4K1R1 w - - 0 1")
    probe = board.copy()
    probe.push(chess.Move.null())
    threat = probe.parse_san("Qxg1")
    my_move = board.parse_san("Ne3")
    out = prophylaxis.prevented_by(board, my_move, threat)
    assert out is not None and out["reason"] == "blocked"


def test_aucune_menace_aucun_plantage():
    board = chess.Board()
    assert prophylaxis.prevented_by(board, board.parse_san("e4"), None) is None


class _MoteurQuiNeDoitPasEtreAppele:
    """Sentinelle non-None avec un `.engine.analyse` qui s'auto-enregistre au
    lieu de lever : si `opponent_threat` capte une exception et rend None
    quand meme (`except Exception`), un test qui se contenterait de faire
    lever la sentinelle ne prouverait rien. Ici on verifie factuellement que
    `analyse` n'a JAMAIS ete appelee."""

    def __init__(self):
        self.engine = self
        self.called = False

    def analyse(self, *args, **kwargs):
        self.called = True
        return {}


def test_en_echec_pas_de_coup_nul():
    # Roi blanc en echec (mais PAS mat : le roi a des cases de fuite) sur une
    # position minimale -- la FEN "Qh4" du brief est en realite un MAT
    # (is_game_over() est deja True), ce qui masquerait la garde is_check()
    # exactement comme engine=None la masquait : is_game_over() court-circuite
    # avant is_check() d'etre determinant. Ici seule is_check() peut arreter
    # l'appel.
    check_board = chess.Board("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1")
    assert check_board.is_check()
    assert not check_board.is_game_over()

    sentinel = _MoteurQuiNeDoitPasEtreAppele()
    # `probe.is_valid()` est un second filet ("ceinture et bretelles") qui,
    # a lui seul, rattrape TOUJOURS une position en echec apres coup nul --
    # donc meme sans la garde is_check(), ce filet empecherait l'appel
    # moteur et le test paraitrait probant sans l'etre. On le neutralise
    # temporairement pour isoler ce que la garde is_check() fait vraiment.
    original_is_valid = chess.Board.is_valid
    chess.Board.is_valid = lambda self: True
    try:
        result = prophylaxis.opponent_threat(sentinel, check_board)
    finally:
        chess.Board.is_valid = original_is_valid

    assert result is None
    assert sentinel.called is False


def test_menace_reelle_mat_ou_materiel_seulement():
    """threat_is_real tranche entre « une menace » et « le coup qu'il jouerait
    de toute facon ». Sans ce filtre, l'avertissement sortirait sur 97% des
    positions (mesure sur 80 positions reelles) et l'oeil apprendrait a le
    sauter -- y compris le jour ou il annonce un mat.

    Les quatre cas sont verifies AFFICHES avant d'etre crus : mes premieres
    positions etaient fausses (la dame ne pouvait pas atteindre la tour, et
    le "mat" n'en etait pas un).
    """
    # PRISE NON DEFENDUE : Dxd1 ramasse une tour que rien ne reprend.
    b = chess.Board("3qk3/8/8/8/8/8/6PP/3R2K1 w - - 0 1")
    m = prophylaxis.threat_is_real(b, chess.Move.from_uci("d8d1"))
    assert m and m["kind"] == "material" and m["gain"] == 5, m
    assert m["san"] == "Qxd1+", m["san"]   # SAN calcule trait inverse

    # COUP CALME : meme position, la dame va en d5. Rien a signaler.
    assert prophylaxis.threat_is_real(b, chess.Move.from_uci("d8d5")) is None

    # ECHANGE EGAL : Txd2 est repris par Td1 -> solde 0, sous le seuil.
    egal = chess.Board("3rk3/8/8/8/8/8/3R4/3R2K1 w - - 0 1")
    assert prophylaxis.threat_is_real(egal, chess.Move.from_uci("d8d2")) is None

    # MAT du couloir : aucune prise, mais c'est LA menace a montrer.
    mat = chess.Board("r3k3/8/8/8/8/8/5PPP/6K1 w - - 0 1")
    m = prophylaxis.threat_is_real(mat, chess.Move.from_uci("a8a1"))
    assert m and m["kind"] == "mate" and m["san"] == "Ra1#", m


def test_menace_en_echec_ne_sonde_rien():
    """Meme garde non negociable que opponent_threat : en echec, le coup nul
    est illegal et python-chess fabrique une position invalide SANS lever."""
    b = chess.Board("4k3/8/8/8/8/8/8/r3K3 w - - 0 1")
    assert b.is_check()
    assert prophylaxis.threat_is_real(b, chess.Move.from_uci("a1a8")) is None


def test_texte_de_lalerte_est_en_francais():
    """Le SAN de l'alerte passe par la conversion d'affichage : « Ra1# » se
    lirait ROI a1 en francais alors que c'est une TOUR."""
    import fragment_library
    txt = fragment_library.threat_caution({"san": "Ra1#", "kind": "mate", "gain": 0})
    assert "Ta1#" in txt and "Ra1#" not in txt, txt
    txt = fragment_library.threat_caution({"san": "Qxd1+", "kind": "material", "gain": 5})
    assert "Dxd1+" in txt and "tour" in txt, txt   # 5 points = l'equivalent d'une tour
    assert fragment_library.threat_caution(None) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
