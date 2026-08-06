import re
from datetime import date
from typing import Optional
from hijri_converter import Hijri
from utils.logging import logger

EASTERN_TO_WESTERN = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')

def strip_harakat(text: str) -> str:
    """Remove Arabic diacritics (Tashkeel/Harakat)."""
    # Unicode block for Arabic diacritics
    arabic_diacritics = re.compile(r'[\u064B-\u0652\u0653\u0670]')
    return re.sub(arabic_diacritics, '', text)

def fix_arabic_spaces(text: str) -> str:
    """Fix spaced Arabic letters per line — preserves structural newlines."""
    if not text:
        return ""
    lines = text.split('\n')
    fixed_lines = [_fix_arabic_spaces_line(line) for line in lines]
    return '\n'.join(fixed_lines)

def _fix_arabic_spaces_line(text: str) -> str:
    """Fix spaced Arabic letters in a single line."""
    if not text:
        return ""
        
    words = text.split(' ')
    if not words:
        return text
        
    non_connecting = set("اأإآدذرءزو")
    non_merge = {"في", "من", "ما", "عن", "لا", "يا", "علي", "الي", "ام", "او", "بل", "قد", "هل", "ذو", "ذا", "ذي", "مع", "منذ"}
    
    cleaned = []
    i = 0
    while i < len(words):
        current = words[i]
        
        while i + 1 < len(words):
            next_word = words[i + 1]
            
            is_current_arabic = any(0x0600 <= ord(c) <= 0x06FF for c in current)
            is_next_arabic = any(0x0600 <= ord(c) <= 0x06FF for c in next_word)
            
            if not is_current_arabic or not is_next_arabic:
                break
                
            last_char = current[-1]
            if last_char not in non_connecting:
                break
                
            if next_word in non_merge:
                break
                
            # Merge if the next word is very short or the current word is very short (consecutive split fragments)
            if len(next_word) <= 2 or len(current) <= 3:
                current = current + next_word
                i += 1
            else:
                break
                
        cleaned.append(current)
        i += 1
        
    return " ".join(cleaned)

def clean_decorative_artifacts(text: str) -> str:
    """Remove decorative lines, table borders, repeated page headers/footers, and duplicate punctuation."""
    if not text:
        return ""
    # 1. Remove decorative separators (e.g. =====, -----, *****, _____, ـــــ)
    text = re.sub(r'[=*\-_ـ]{4,}', '', text)
    # 2. Remove raw markdown table borders (e.g. |-------|)
    text = re.sub(r'\|[ \t:]*[-=]+[ \t:]*\|', '', text)
    # 3. Normalize repeated Arabic & English punctuation (e.g. ،،، -> ، or .... -> .)
    text = re.sub(r'،{2,}', '،', text)
    text = re.sub(r'\.{3,}', '.', text)
    # 4. Remove standalone page number footers (e.g. "\n 14 \n" or "صفحة 14 من 50")
    text = re.sub(r'\n\s*(?:صفحة\s*\d+\s*(?:من\s*\d+)?|\d+)\s*\n', '\n', text)
    # 5. Collapse excessive vertical blank lines (more than 2 consecutive newlines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def repair_ocr_words(text: str) -> str:
    """Repair common OCR Arabic word fragmentation and kerning splits."""
    if not text:
        return ""
    replacements = [
        (r'\bالمر\s+سوم\b', 'المرسوم'),
        (r'\bجر\s+يمه\b', 'جريمة'),
        (r'\bال\s+ارهاب\b', 'الارهاب'),
        (r'\bا\s+م\s+وال\b', 'اموال'),
        (r'\bال\s+اموال\b', 'الاموال'),
        (r'\bغ\s+ي\s+ر\b', 'غير'),
        (r'\bت\s+م\s+و\s+ي\s+ل\b', 'تمويل'),
        (r'\bالمش\s+روعه\b', 'المشروعة'),
        (r'\bالمت\s+علقه\b', 'المتعلقة'),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return text


def normalize_arabic_text(text: str) -> str:
    """Normalize Arabic text by stripping diacritics, OCR replacement chars, decorative artifacts, and unifying character variants."""
    import unicodedata
    if not text:
        return ""
    # Strip replacement character U+FFFD from font encoding artifacts
    text = text.replace('\uFFFD', '')
    # Strip zero-width non-joiner/space artifacts
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    # Normalize Unicode (fixes Arabic Presentation Forms from PDF extraction)
    text = unicodedata.normalize('NFKC', text)
    # Strip diacritics
    text = strip_harakat(text)
    # Clean decorative lines, borders, duplicate punctuation
    text = clean_decorative_artifacts(text)
    # Repair common split OCR words
    text = repair_ocr_words(text)
    # Fix spaced Arabic characters from PDF kerning artifact
    text = fix_arabic_spaces(text)
    # Unify Alef variants (أ/إ/آ -> ا)
    text = re.sub(r'[أإآ]', 'ا', text)
    # Unify Yeh/Alef Maksura (ى -> ي)
    text = re.sub(r'ى', 'ي', text)
    # Unify Teh Marbuta (ة -> ه)
    text = re.sub(r'ة', 'ه', text)
    return text.strip()



def normalize_numerals(text: str) -> str:
    """Convert Eastern Arabic numerals (٠-٩) to Western Arabic numerals (0-9)."""
    if not text:
        return ""
    return text.translate(EASTERN_TO_WESTERN)

def parse_date_to_gregorian(date_str: str) -> Optional[date]:
    """Parse a Hijri or Gregorian date string and return a Gregorian date object.
    
    If the date is determined to be Hijri, converts it to Gregorian.
    Supports DD/MM/YYYY, YYYY/MM/DD, and year-only formats (e.g. '1992م', '1413هـ').
    """
    if not date_str:
        return None
        
    # Standardize Eastern numerals to Western numerals
    date_str = normalize_numerals(date_str).strip()
    
    # 1. Try standard DD/MM/YYYY or YYYY/MM/DD patterns
    match = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', date_str)
    if match:
        d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        # Swap if in YYYY/MM/DD format
        if d > 1900 or (1300 <= d <= 1500):
            y, m, d = d, m, y
            
        if 1300 <= y <= 1500:
            try:
                return Hijri(y, m, d).to_gregorian()
            except Exception as e:
                logger.debug(f"Failed converting Hijri DD/MM/YYYY: {e}")
        elif 1900 <= y <= 2100:
            try:
                return date(y, m, d)
            except Exception as e:
                logger.debug(f"Failed parsing Gregorian DD/MM/YYYY: {e}")

    # 2. Try Year + Eras patterns (e.g. "1412هـ" or "1992م")
    # Assume month = 1, day = 1 as fallback for year-only dates
    match_hijri_yr = re.search(r'(\d{4})\s*هـ?', date_str)
    if match_hijri_yr:
        y = int(match_hijri_yr.group(1))
        try:
            return Hijri(y, 1, 1).to_gregorian()
        except Exception as e:
            logger.debug(f"Failed converting Hijri year {y}: {e}")
            
    match_greg_yr = re.search(r'(\d{4})\s*م', date_str)
    if match_greg_yr:
        y = int(match_greg_yr.group(1))
        try:
            return date(y, 1, 1)
        except Exception as e:
            logger.debug(f"Failed parsing Gregorian year {y}: {e}")
            
    # 3. Fallback check for any 4-digit number in the string
    years = [int(yr) for yr in re.findall(r'\b\d{4}\b', date_str)]
    for y in years:
        if 1300 <= y <= 1500:
            try:
                return Hijri(y, 1, 1).to_gregorian()
            except Exception:
                pass
        elif 1900 <= y <= 2100:
            try:
                return date(y, 1, 1)
            except Exception:
                pass
                
    return None
