import re
from typing import List, Optional
from db.database import SessionLocal
from db.models import OrganizationGlossary
from utils.logging import logger
from document_processor.normalization import normalize_numerals, normalize_arabic_text

class EntityResolver:
    def __init__(self):
        pass

    def expand_query_synonyms(self, query: str, tenant_id: str = "default_tenant") -> str:
        """Find acronyms or short terms in the query and append their canonical Arabic terms for sparse search."""
        if not query:
            return ""
            
        db = SessionLocal()
        try:
            # Fetch all terms for this tenant
            glossary_items = db.query(OrganizationGlossary).filter(
                OrganizationGlossary.tenant_id == tenant_id
            ).all()
            
            expanded_terms = []
            normalized_query = normalize_numerals(normalize_arabic_text(query)).lower()
            
            for item in glossary_items:
                src = normalize_numerals(normalize_arabic_text(item.source_term)).strip()
                canonical = item.canonical_term.strip()
                
                # Check for word boundary match to avoid partial matches
                pattern = r'\b' + re.escape(src.lower()) + r'\b'
                if re.search(pattern, normalized_query):
                    expanded_terms.append(canonical)
                    
            if expanded_terms:
                expansion_suffix = " " + " ".join(expanded_terms)
                logger.info(f"[EntityResolver] Expanded query '{query}' with synonyms: '{expansion_suffix.strip()}'")
                return query + expansion_suffix
                
            return query
        except Exception as e:
            logger.error(f"[EntityResolver] Failed to expand synonyms: {e}")
            return query
        finally:
            db.close()
