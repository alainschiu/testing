"""Proper-noun extraction + verification against artist/opportunity sources."""
from scout.agents.hallucination import extract_proper_nouns, verify_proper_nouns


def test_extracts_capitalised_multi_word_phrases() -> None:
    text = (
        "Drawing Room Project responds to DAAD Berliner Künstlerprogramm's"
        " stated interest in contemplative listening. The artist Alain Chiu"
        " collaborated with the Forensic Architecture team in Berlin."
    )
    nouns = extract_proper_nouns(text)
    assert "Drawing Room Project" in nouns
    assert "DAAD Berliner Künstlerprogramm" in nouns
    assert "Alain Chiu" in nouns
    assert "Forensic Architecture" in nouns


def test_does_not_treat_single_capitalised_words_as_phrases() -> None:
    nouns = extract_proper_nouns("Berlin is a city. Stuttgart too.")
    assert nouns == []


def test_verifies_against_sources() -> None:
    text = (
        "The Drawing Room Project will be presented at the DAAD Berliner"
        " Künstlerprogramm and the Made-Up Foundation."
    )
    sources = [
        "## The Drawing Room Project (load-bearing pitch)\nResearch into loneliness…",
        "Title: DAAD Berliner Künstlerprogramm\nFunder's stated priorities…",
    ]
    verified, unverified = verify_proper_nouns(text, sources)
    assert "Drawing Room Project" in verified
    assert "DAAD Berliner Künstlerprogramm" in verified
    assert "Made-Up Foundation" in unverified


def test_unicode_normalisation_so_accents_match() -> None:
    text = "Akademie Schloss Solitude"
    sources = ["Akademie Schloss Solitude is in Stuttgart."]
    verified, unverified = verify_proper_nouns(text, sources)
    assert verified == ["Akademie Schloss Solitude"]
    assert unverified == []


def test_stopwords_are_ignored() -> None:
    text = "Selected Works · Cover Letter · Artist Statement"
    nouns = extract_proper_nouns(text)
    assert all(n.lower() not in {"selected works", "cover letter", "artist statement"} for n in nouns)
