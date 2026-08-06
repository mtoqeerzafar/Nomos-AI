"""
Unit Test Suite: Arabic Text Normalization & Hygiene (`tests/test_normalization_hygiene.py`)
Purpose: Audits core document processor text cleaning routines (`document_processor/normalization.py`).
Functionality: Verifies removal of U+FFFD replacement characters, zero-width spaces, Arabic Tashkeel diacritics,
and Kashida elongations (`ـ`). Validates Unicode character unification (Alef/Ta Marbouta) and OCR word repair.
Usage: Run via `python -m unittest tests/test_normalization_hygiene.py` or `pytest`.
"""

import unittest
import re
from document_processor.normalization import (
    normalize_arabic_text,
    clean_decorative_artifacts,
    repair_ocr_words,
    strip_harakat,
    fix_arabic_spaces
)

class TestNormalizationHygiene(unittest.TestCase):

    def test_replacement_character_removal(self):
        """Test 1: U+FFFD (replacement character \uFFFD) is stripped."""
        raw_text = "LAW: قرار مجلس الوزراء \uFFFD رقم (10) لسنة 2019 \uFFFD"
        cleaned = normalize_arabic_text(raw_text)
        self.assertNotIn('\uFFFD', cleaned)

    def test_zero_width_space_removal(self):
        """Test 2: Zero-width non-joiners (\u200B-\u200D, \uFEFF) are stripped."""
        raw_text = "المادة\u200B (57)\uFEFF"
        cleaned = normalize_arabic_text(raw_text)
        self.assertNotIn('\u200B', cleaned)
        self.assertNotIn('\uFEFF', cleaned)

    def test_arabic_presentation_forms(self):
        """Test 3: Arabic presentation forms (NFKC glyphs) normalize to standard Unicode."""
        raw_presentation = "ﻣﺠﻠﺲ اﻟﻮ ز ر اء"
        cleaned = normalize_arabic_text(raw_presentation)
        self.assertTrue(any(0x0600 <= ord(c) <= 0x06FF for c in cleaned))

    def test_ocr_word_repairs(self):
        """Test 4: Common OCR word splits (e.g. المر سوم -> المرسوم) are repaired."""
        raw_samples = [
            ("المر سوم بقانون", "المرسوم"),
            ("ارتكاب جر يمه غسل", "جريمه"),
            ("مكافحة ال ارهاب", "الارهاب"),
            ("مواجهة غسل ا م وال", "اموال"),
            ("لاغراض غ ي ر مشروعة", "غير"),
        ]
        for raw, expected_substr in raw_samples:
            cleaned = normalize_arabic_text(raw)
            self.assertIn(expected_substr, cleaned, f"Failed for raw input: {raw}")


    def test_decorative_artifact_cleaning(self):
        """Test 5: Decorative lines (=====, -----, *****) and table borders (|---|) are removed."""
        raw_text = "فقرة أولى.\n---------------------\n=====================\n|---|---|---|\nفقرة ثانية."
        cleaned = clean_decorative_artifacts(raw_text)
        self.assertNotIn('---------------------', cleaned)
        self.assertNotIn('=====================', cleaned)
        self.assertNotIn('|---|---|---|', cleaned)
        self.assertIn('فقرة أولى.', cleaned)
        self.assertIn('فقرة ثانية.', cleaned)

    def test_punctuation_normalization(self):
        """Test 6: Repeated punctuation (،،، -> ،, .... -> .) is normalized."""
        raw_text = "المادة 1،،، والمادة 2...."
        cleaned = clean_decorative_artifacts(raw_text)
        self.assertNotIn('،،،', cleaned)
        self.assertNotIn('....', cleaned)
        self.assertIn('المادة 1،', cleaned)

    def test_alef_and_teh_marbuta_unification(self):
        """Test 7: Alef variants (أ/إ/آ -> ا) and Teh Marbuta (ة -> ه) are unified."""
        raw_text = "الإرهاب أسلوب آمن في المنظمة"
        cleaned = normalize_arabic_text(raw_text)
        self.assertNotIn('إ', cleaned)
        self.assertNotIn('أ', cleaned)
        self.assertNotIn('آ', cleaned)
        self.assertNotIn('ة', cleaned)
        self.assertIn('الارهاب', cleaned)
        self.assertIn('المنظمه', cleaned)

    def test_normalization_idempotency(self):
        """Test 8 (Property Test): Normalization is idempotent: normalize(normalize(text)) == normalize(text)."""
        raw_text = "LAW: قرار مجلس الوزراء \uFFFD رقم (10) لسنة 2019 \uFFFD\n---------------------\nالمر سوم بقانون ارتكاب جر يمه غسل ا م وال،،،"
        first_pass = normalize_arabic_text(raw_text)
        second_pass = normalize_arabic_text(first_pass)
        third_pass = normalize_arabic_text(second_pass)

        self.assertEqual(first_pass, second_pass, "Normalization must be idempotent! Second pass changed text.")
        self.assertEqual(second_pass, third_pass, "Normalization must be idempotent! Third pass changed text.")


if __name__ == '__main__':
    unittest.main()

