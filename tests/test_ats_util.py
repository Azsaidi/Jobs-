from jobbot import ats
from jobbot.util import html_to_text, parse_date


def test_html_to_text_handles_escaped_html():
    raw = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "Hello & welcome" in text
    assert "- SQL" in text and "- Python" in text
    assert "<" not in text


def test_parse_date_variants():
    assert parse_date("2026-10-15T04:40:52-04:00").hour == 8
    assert parse_date(1785824225174).year == 2026
    assert parse_date("Wed, 28 Oct 2026 10:00:00 +0000").day == 28
    assert parse_date("nonsense") is None


def test_extract_keywords_finds_vocab_and_profile_terms():
    text = "We need SQL, Python and Power BI. SQL is key. Experience with our in-house FooTool is a plus."
    kws = ats.extract_keywords(text, extra_terms=["FooTool"], title="Data Analyst (SQL)")
    assert kws[0] == "SQL"  # most frequent + in title
    assert {"Python", "Power BI", "FooTool"} <= set(kws)


def test_short_terms_are_case_sensitive():
    assert ats.count_term("R", "Experience with R and Python") == 1
    assert ats.count_term("R", "our team, r&d and more") == 0
    assert ats.count_term("Go", "Let's go to the office") == 0


def test_repeated_unknown_acronyms_are_picked_up():
    kws = ats.extract_keywords("Knowledge of SCADA systems. SCADA upgrades. UAE based. UAE.")
    assert "SCADA" in kws and "UAE" not in kws


def test_coverage():
    score, matched, missing = ats.coverage("I use SQL and Python daily", ["SQL", "Python", "Tableau"])
    assert matched == ["SQL", "Python"] and missing == ["Tableau"]
    assert 60 < score < 75
    assert ats.coverage("anything", []) == (0.0, [], [])
