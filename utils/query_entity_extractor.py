"""
Query Entity Extractor Utility
Extracts structured legal entities (article numbers, law numbers, enactment years, document types)
from normalized query strings to populate deterministic Qdrant payload filters.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from utils.query_normalizer import normalize_query_text, ORDINAL_MAP

@dataclass
class ExtractedEntities:
    articles: List[str] = field(default_factory=list)
    law_numbers: List[str] = field(default_factory=list)
    law_years: List[str] = field(default_factory=list)
    document_types: List[str] = field(default_factory=list)
    article_keys: List[str] = field(default_factory=list)
    has_citation: bool = False

ARTICLE_RE = re.compile(
    r'(?:المادة|الماده|مادة|ماده|article)\s*(?:رقم|num|no|\.)?\s*\(?\s*(\d+)\s*\)?'
    r'|(?:المادة|الماده|مادة|ماده)\s+(الأولى|الثانية|الثالثة|الرابعة|الخامسة|السادسة|السابعة|الثامنة|التاسعة|العاشرة|الاولي|الثانيه|الثالثه|الرابعه|الخامسه|السادسه|السابعه|الثامنه|التاسعه|العاشره|الحادية عشرة|الثانية عشرة)',
    re.IGNORECASE
)

LAW_NUM_RE = re.compile(
    r'(?:قانون|مرسوم|قرار|Law|Decree|Resolution)\s*(?:اتحادي|وزاري|مجلس الوزراء)?\s*(?:بقانون)?\s*(?:رقم|No\.?|No)?\s*[\(\)]*\s*(\d+)\s*[\(\)]*',
    re.IGNORECASE
)

LAW_YEAR_RE = re.compile(
    r'(?:لسنة|لسنه|سنة|سنه|year|of)\s*(\d{4})\b|(?:\b(19\d{2}|20\d{2})\b)'
)

DOC_TYPE_PATTERNS = [
    (re.compile(r'قرار مجلس الوزراء', re.IGNORECASE), "Cabinet Resolution"),
    (re.compile(r'قرار وزاري', re.IGNORECASE), "Ministerial Decision"),
    (re.compile(r'مرسوم بقانون', re.IGNORECASE), "Federal Decree Law"),
    (re.compile(r'قانون اتحادي|قانون', re.IGNORECASE), "Federal Law"),
]

def extract_query_entities(query_text: str) -> ExtractedEntities:
    """Extract canonical legal entities from a raw or normalized query string."""
    norm_text = normalize_query_text(query_text)
    entities = ExtractedEntities()

    # 1. Extract Articles
    for m in ARTICLE_RE.finditer(norm_text):
        for g in m.groups():
            if g is not None:
                art_val = ORDINAL_MAP.get(g, g)
                if art_val not in entities.articles:
                    entities.articles.append(art_val)

    # 2. Extract Law Numbers
    for m in LAW_NUM_RE.finditer(norm_text):
        g = m.group(1)
        if g and g not in entities.law_numbers:
            entities.law_numbers.append(g)

    # 3. Extract Law Years
    for m in LAW_YEAR_RE.finditer(norm_text):
        yr = m.group(1) or m.group(2)
        if yr and yr not in entities.law_years:
            entities.law_years.append(yr)

    # 4. Extract Document Types
    for pat, dt in DOC_TYPE_PATTERNS:
        if pat.search(norm_text):
            if dt not in entities.document_types:
                entities.document_types.append(dt)

    # 5. Build canonical article keys if article + law + year present
    if entities.articles:
        for art in entities.articles:
            if entities.law_numbers and entities.law_years:
                for lnum in entities.law_numbers:
                    for lyr in entities.law_years:
                        key = f"{lnum}_{lyr}_{art}"
                        if key not in entities.article_keys:
                            entities.article_keys.append(key)
            else:
                key = f"art_{art}"
                if key not in entities.article_keys:
                    entities.article_keys.append(key)

    entities.has_citation = bool(entities.articles or entities.law_numbers or entities.law_years)
    return entities
