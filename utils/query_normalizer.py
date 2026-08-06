"""
Query Normalizer Utility
canonical string hygiene for query strings prior to entity extraction or embedding generation.
Handles Tatweel removal, presentation-form Arabic normalization, Eastern digit mapping,
RTL inverted paren fixes, and Arabic ordinal conversion.
"""

import re
import unicodedata

ORDINAL_MAP = {
    'الأولى': '1', 'الثانية': '2', 'الثالثة': '3', 'الرابعة': '4',
    'الخامسة': '5', 'السادسة': '6', 'السابعة': '7', 'الثامنة': '8',
    'التاسعة': '9', 'العاشرة': '10',
    'الاولي': '1', 'الثانيه': '2', 'الثالثه': '3', 'الرابعه': '4',
    'الخامسه': '5', 'السادسه': '6', 'السابعه': '7', 'الثامنه': '8',
    'التاسعه': '9', 'العاشره': '10',
    'الحادية عشرة': '11', 'الثانية عشرة': '12',
}

EASTERN_DIGIT_MAP = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')

def normalize_query_text(text: str) -> str:
    """Normalize query text for deterministic entity extraction and dense encoding."""
    if not text:
        return ""

    # 1. Normalize Unicode NFKC (handles presentation-form glyphs U+FB50..U+FDFF)
    text = unicodedata.normalize('NFKC', text)

    # 2. Strip Tatweel / Kashida (\u0640)
    text = re.sub(r'[\u0640]+', '', text)

    # 3. Convert Eastern Arabic & Persian numerals to Western ASCII digits
    text = text.translate(EASTERN_DIGIT_MAP)

    # 4. Fix OCR / RTL inverted paren formatting & add clean spacing around article numbers
    text = re.sub(r'\)\s*(\d+)\s*\(', r'(\1)', text)
    text = re.sub(r'\(\s*(\d+)\s*\)', r'(\1)', text)
    text = re.sub(r'(?:المادة|الماده|مادة|ماده)\s*\)\s*(\d+)\s*\(', r'المادة \1 ', text)
    text = re.sub(r'\(?\s*(?:المادة|الماده|مادة|ماده)\s*(\d+)\s*\)?', r'المادة \1 ', text)

    # 5. Normalize common legal typos
    text = re.sub(r'\bالمر\s+سوم\b', 'المرسوم', text)
    text = re.sub(r'\bجر\s+يمه\b', 'جريمة', text)
    text = re.sub(r'[\u200B-\u200D\uFEFF\uFFFD]', '', text)

    # 6. Replace multi-spaces with single space
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()
