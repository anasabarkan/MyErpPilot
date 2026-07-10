from app.services.language_service import detect_supported_language


def test_detect_supported_language_returns_english_for_english_question():
    assert detect_supported_language("Who are my top customers?") == "en"


def test_detect_supported_language_returns_french_for_french_question():
    assert detect_supported_language("Quels sont mes meilleurs clients ?") == "fr"


def test_detect_supported_language_returns_arabic_for_arabic_question():
    assert detect_supported_language("من هم أفضل العملاء؟") == "ar"


def test_detect_supported_language_defaults_to_english_for_ambiguous_text():
    assert detect_supported_language("top 5") == "en"
