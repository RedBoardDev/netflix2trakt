from netflix2trakt.text import normalize, strip_accents, title_score


def test_strip_accents():
    assert strip_accents("L'Éternaute") == "L'Eternaute"


def test_normalize_drops_accents_punctuation_and_nbsp():
    assert normalize("L'Éternaute : Crédo!") == "l eternaute credo"
    assert normalize("Chapitre\xa010") == "chapitre 10"  # non-breaking space


def test_title_score_exact_is_one_ignoring_case_and_accents():
    assert title_score("Pilote", "Pilote") == 1.0
    assert title_score("Pilote", "pilôte") == 1.0


def test_title_score_containment():
    # Netflix "65 : La Terre d'avant" should strongly match the TMDB movie "65".
    assert title_score("65 : La Terre d'avant", "65") >= 0.9


def test_title_score_divergent_translations_are_low():
    # Different French translations of the same episode score low -- the reason
    # order-inference exists rather than relying on fuzzy title matching alone.
    assert title_score("Sous emprise", "Hypnotisé") < 0.6
