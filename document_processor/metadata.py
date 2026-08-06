import re
from datetime import date
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from hijri_converter import Hijri
from utils.logging import logger
from utils.llm_factory import get_llm
from document_processor.normalization import parse_date_to_gregorian, normalize_numerals, normalize_arabic_text

# Pydantic Schemas for LLM Structured Output
class ExtractedRelation(BaseModel):
    target_law_ref: str = Field(description="The reference string of the related law/regulation (e.g. 'قانون رقم (43) لسنة 1992').")
    relation_type: str = Field(description="The type of relationship. Must be one of: implements, amends, supersedes, references, corrects.")
    confidence: float = Field(description="Confidence score for this relationship from 0.0 to 1.0.")

class ExtractedMetadataSchema(BaseModel):
    title: str = Field(description="The official title of the legal/policy document in Arabic.")
    domain: str = Field(description="The domain of the document (e.g. 'HR', 'Finance', 'Legal').")
    version: str = Field(description="The version string of this document. Default to '1.0' if not specified.")
    allowed_roles: List[str] = Field(default_factory=list, description="A list of allowed roles for access control, e.g. ['HR_Manager']. Return empty list if unrestricted/public.")
    applicability: Optional[Dict[str, str]] = Field(default=None, description="Key-value mapping of applicability metadata, e.g. {'country': 'UAE'}.")
    original_calendar: str = Field(description="The calendar used for the effective date. Must be either 'Hijri' or 'Gregorian'.")
    original_effective_date: str = Field(description="The effective date string exactly as written in the document.")
    effective_date_gregorian: Optional[str] = Field(default=None, description="The parsed Gregorian date in YYYY-MM-DD format (e.g. '1992-12-15').")
    expiry_date_gregorian: Optional[str] = Field(default=None, description="The parsed Gregorian expiry date in YYYY-MM-DD format (if any).")
    relationships: List[ExtractedRelation] = Field(default_factory=list, description="Any cross-document relationships extracted from the document.")


# Regex patterns for deterministic extraction
LAW_NAME_PATTERN = re.compile(
    r'(قانون اتحادي|مرسوم بقانون اتحادي|قرار مجلس الوزراء|قانون|مرسوم)\s+(?:بقانون\s+)?(?:اتحادي\s+)?رقم\s*\(?(\d+)\)?\s+(?:لسنة|لسنه)\s*\(?(\d{4})\)?',
    re.IGNORECASE
)

DATE_PATTERN = re.compile(
    r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})'
)

def extract_metadata_regex(text: str, filename: str) -> Dict[str, Any]:
    """Fallback deterministic regex-based metadata extractor."""
    normalized_text = normalize_numerals(text)
    
    # 1. Parse Title
    title = filename
    law_match = LAW_NAME_PATTERN.search(normalized_text)
    if law_match:
        title = law_match.group(0).strip()
    else:
        # Fallback to the first non-empty line of the text
        for line in text.split('\n'):
            line = line.strip()
            if line:
                title = line[:100]
                break
                
    # 2. Parse Date
    original_date = ""
    effective_date_greg = None
    original_calendar = "Gregorian"
    
    # Search for date pattern
    date_match = DATE_PATTERN.search(normalized_text)
    if date_match:
        original_date = date_match.group(0)
        # Determine calendar
        context = normalized_text[max(0, date_match.start() - 30):min(len(normalized_text), date_match.end() + 30)]
        if 'هـ' in context or 'هجري' in context:
            original_calendar = "Hijri"
        effective_date_greg = parse_date_to_gregorian(original_date)
    else:
        # Try year-only search
        yr_match = re.search(r'(\d{4})\s*(هـ|م)?', normalized_text)
        if yr_match:
            original_date = yr_match.group(0)
            if yr_match.group(2) == 'هـ':
                original_calendar = "Hijri"
            effective_date_greg = parse_date_to_gregorian(original_date)
            
    # 3. Detect relationships (e.g. references to other laws)
    relationships = []
    for match in LAW_NAME_PATTERN.finditer(normalized_text):
        ref = match.group(0).strip()
        # Avoid linking to itself
        if ref not in title:
            relationships.append({
                "target_law_ref": ref,
                "relation_type": "references",
                "confidence": 0.99
            })
            
    return {
        "title": title,
        "domain": "HR" if "hr" in filename.lower() or "موارد" in text else "Legal",
        "version": "1.0",
        "allowed_roles": [],
        "applicability": {"country": "UAE"},
        "original_calendar": original_calendar,
        "original_effective_date": original_date,
        "effective_date_gregorian": effective_date_greg,
        "expiry_date_gregorian": None,
        "relationships": relationships,
        "warnings": []
    }

def extract_document_metadata(text: str, filename: str) -> Dict[str, Any]:
    """Extract metadata using LLM structured output with a robust regex fallback."""
    # First, run regex to get a baseline
    base_meta = extract_metadata_regex(text, filename)
    
    # Try LLM-based extraction
    try:
        llm = get_llm(temperature=0.0)
        structured_llm = llm.with_structured_output(ExtractedMetadataSchema, method="function_calling")
        
        # Take the first 10,000 characters of text for metadata extraction to save context tokens
        sample_text = text[:10000]
        
        prompt = (
            f"You are a legal metadata extraction assistant. Extract the metadata from the following UAE document.\n"
            f"Filename: {filename}\n"
            f"Document Content (Sample):\n{sample_text}\n"
        )
        
        result = structured_llm.invoke(prompt)
        
        # Defensive conversion of result to a dictionary
        if isinstance(result, list) and len(result) > 0:
            result = result[0]
            
        res_dict = {}
        if isinstance(result, BaseModel):
            res_dict = result.model_dump()
        elif isinstance(result, dict):
            res_dict = result
        elif result is not None:
            res_dict = {
                "title": getattr(result, "title", None),
                "domain": getattr(result, "domain", None),
                "version": getattr(result, "version", None),
                "allowed_roles": getattr(result, "allowed_roles", None),
                "applicability": getattr(result, "applicability", None),
                "original_calendar": getattr(result, "original_calendar", None),
                "original_effective_date": getattr(result, "original_effective_date", None),
                "effective_date_gregorian": getattr(result, "effective_date_gregorian", None),
                "expiry_date_gregorian": getattr(result, "expiry_date_gregorian", None),
                "relationships": getattr(result, "relationships", None)
            }
            
        # Parse Gregorian dates to Date objects if returned as strings
        eff_date = None
        eff_date_str = res_dict.get("effective_date_gregorian")
        if eff_date_str:
            try:
                eff_date = date.fromisoformat(str(eff_date_str))
            except Exception:
                eff_date = parse_date_to_gregorian(str(eff_date_str))
                
        exp_date = None
        exp_date_str = res_dict.get("expiry_date_gregorian")
        if exp_date_str:
            try:
                exp_date = date.fromisoformat(str(exp_date_str))
            except Exception:
                exp_date = parse_date_to_gregorian(str(exp_date_str))
                
        # Handle warnings
        warnings = []
        if not eff_date:
            warnings.append("Missing or unparseable effective date")
            
        # Compile relationships
        relationships = []
        raw_rels = res_dict.get("relationships") or []
        for rel in raw_rels:
            if isinstance(rel, dict):
                target = rel.get("target_law_ref")
                rtype = rel.get("relation_type")
                conf = rel.get("confidence", 0.80)
            else:
                target = getattr(rel, "target_law_ref", None)
                rtype = getattr(rel, "relation_type", None)
                conf = getattr(rel, "confidence", 0.80)
                
            if target and rtype:
                relationships.append({
                    "target_law_ref": target,
                    "relation_type": rtype,
                    "confidence": conf
                })
            
        return {
            "title": res_dict.get("title") or base_meta["title"],
            "domain": res_dict.get("domain") or base_meta["domain"],
            "version": res_dict.get("version") or base_meta["version"],
            "allowed_roles": res_dict.get("allowed_roles") or base_meta["allowed_roles"],
            "applicability": res_dict.get("applicability") or base_meta["applicability"],
            "original_calendar": res_dict.get("original_calendar") or base_meta["original_calendar"],
            "original_effective_date": res_dict.get("original_effective_date") or base_meta["original_effective_date"],
            "effective_date_gregorian": eff_date or base_meta["effective_date_gregorian"],
            "expiry_date_gregorian": exp_date,
            "relationships": relationships or base_meta["relationships"],
            "warnings": warnings
        }
    except Exception as e:
        logger.warning(f"LLM metadata extraction failed, falling back to regex: {e}")
        base_meta["warnings"].append("LLM extraction failed, using regex fallback")
        if not base_meta["effective_date_gregorian"]:
            base_meta["warnings"].append("Missing or unparseable effective date")
        return base_meta

def resolve_law_reference(db, ref_str: str) -> Optional[str]:
    """Resolve a reference string to an existing document ID in the database."""
    from db.models import Document, DocumentFamily
    norm_ref = normalize_numerals(normalize_arabic_text(ref_str)).strip()
    
    # Extract law number and year
    match = LAW_NAME_PATTERN.search(norm_ref)
    if match:
        no, yr = match.group(2), match.group(3)
        results = db.query(Document).join(DocumentFamily).filter(
            DocumentFamily.title.ilike(f"%{no}%"),
            DocumentFamily.title.ilike(f"%{yr}%")
        ).all()
        if results:
            return results[0].id
            
    # Direct match fallback
    result = db.query(Document).join(DocumentFamily).filter(
        DocumentFamily.title.ilike(f"%{ref_str}%")
    ).first()
    if result:
        return result.id
        
    return None
