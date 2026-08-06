"""
Unit Test Suite: Query Rewriter Context Resolution (`tests/test_query_rewriter.py`)
Purpose: Validates QueryRewriterAgent (v1.1) multi-turn pronoun resolution and standalone query synthesis.
Functionality: Evaluates comparative entity anchoring, topic switching, and pronoun substitution against prior chat history turns.
Ensures follow-up queries like "How is this different?" resolve to explicit standalone statutory questions.
Usage: Run via `pytest tests/test_query_rewriter.py` or as part of automated CI regression suites.
"""

import pytest
import os
import sys

# Add the project root to the sys path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agents.query_rewriter import QueryRewriter

def test_comparative_anchoring():
    rewriter = QueryRewriter()
    history = '''User: How does Prompt Injection and Answer Generation work?
Assistant: The Prompt Injection and Answer Generation process works by appending retrieved chunks to the user query in the prompt.
'''
    question = "How is this different from Query Processing and Retrieval?"
    result = rewriter.rewrite(question, history)
    
    # Check that both concepts were correctly preserved and resolved
    assert "Prompt Injection and Answer Generation" in result["standalone_query"]
    assert "Query Processing and Retrieval" in result["standalone_query"]

def test_topic_switching():
    rewriter = QueryRewriter()
    history = '''User: Explain PostgreSQL.
Assistant: PostgreSQL is a powerful, open source object-relational database system.
'''
    question = "What is Kubernetes?"
    result = rewriter.rewrite(question, history)
    
    # EXACT match assertion
    assert result["standalone_query"] == "What is Kubernetes?", f"Got: {result}"
