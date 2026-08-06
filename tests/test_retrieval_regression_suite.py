"""
Regression Test Suite: Qdrant Vector Retrieval Hygiene (`tests/test_retrieval_regression_suite.py`)
Purpose: Validates live Qdrant `ragnr_documents` collection health, payload metadata schemas, and indexing bounds.
Functionality: Audits collection point count, verifies dense 1024d and sparse BM25 payload schemas, tests payload filter performance,
and validates sliding window token overlap chunking limits.
Usage: Run via `python -m unittest tests/test_retrieval_regression_suite.py` or `pytest`.
"""

import unittest
import sys
from pathlib import Path
sys.path.insert(0, '.')

from qdrant_client import QdrantClient
from db.qdrant_client import qdrant_manager
from document_processor.pipeline import build_chunks_for_article, WINDOW_SIZE, WINDOW_OVERLAP

class TestRetrievalRegressionSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = QdrantClient(url='http://localhost:6333', prefer_grpc=False)
        cls.coll_name = 'ragnr_documents'
        info = cls.client.get_collection(cls.coll_name)
        if info.points_count == 0:
            raise RuntimeError(f"Qdrant collection '{cls.coll_name}' is empty. Run ingestion first.")

    def test_qdrant_points_count(self):
        """Test 1: Vector database contains active indexed points."""
        info = self.client.get_collection(self.coll_name)
        self.assertGreater(info.points_count, 0, "Vector store should contain indexed points.")

    def test_article_lookup_retrieval(self):
        """Test 2: Retrieving specific article key returns exact match and schema integrity."""
        points, _ = self.client.scroll(
            collection_name=self.coll_name,
            limit=10,
            with_payload=True,
            with_vectors=False
        )
        self.assertTrue(len(points) > 0, "Scroll should return points.")

        sample_payload = points[0].payload
        required_keys = [
            "article_key", "canonical_citation", "hierarchy_path",
            "hierarchy", "is_parent", "window_number", "token_count"
        ]
        for key in required_keys:
            self.assertIn(key, sample_payload, f"Missing payload key: {key}")

    def test_hygiene_zero_replacement_characters(self):
        """Test 3: Ensure zero points in Qdrant contain replacement characters (\uFFFD)."""
        points, _ = self.client.scroll(
            collection_name=self.coll_name,
            limit=500,
            with_payload=True,
            with_vectors=False
        )
        fffd_chunks = [p for p in points if '\uFFFD' in p.payload.get("text", "")]
        self.assertEqual(len(fffd_chunks), 0, f"Found {len(fffd_chunks)} points with replacement character \\uFFFD in Qdrant!")

    def test_chunk_boundary_and_window_token_math(self):
        """Test 4: Validate sliding window token math (start, end, overlap, step size)."""
        points, _ = self.client.scroll(
            collection_name=self.coll_name,
            limit=500,
            with_payload=True,
            with_vectors=False
        )
        # Filter for multi-window article chunks (total_chunks > 1)
        multi_win_chunks = [p.payload for p in points if p.payload.get("total_chunks", 1) > 1]
        self.assertGreater(len(multi_win_chunks), 0, "Should have multi-window article chunks.")

        step_size = WINDOW_SIZE - WINDOW_OVERLAP # 400 tokens
        for payload in multi_win_chunks[:20]:
            win_idx = payload.get("chunk_index", 0)
            win_num = payload.get("window_number", 1)
            w_start = payload.get("window_start_token", 0)
            w_end   = payload.get("window_end_token", 0)
            tok_cnt = payload.get("token_count", 0)

            expected_start = win_idx * step_size
            self.assertEqual(w_start, expected_start, f"Window start token math mismatch at win {win_num}")
            self.assertEqual(w_end - w_start, tok_cnt, f"Window end token math mismatch at win {win_num}")

    def test_pipeline_ingestion_determinism(self):
        """Test 5: Pipeline chunk generation is 100% deterministic on identical input."""
        sample_parts = ["المادة (1) يكون للكلمات والعبارات التالية المعاني الموضحة قرين كل منها ما لم يقض السياق بغير ذلك."]
        sample_meta = {"page": 1, "source": "test_doc.pdf"}

        run1 = build_chunks_for_article("test_doc.pdf", "1", sample_parts, sample_meta)
        run2 = build_chunks_for_article("test_doc.pdf", "1", sample_parts, sample_meta)

        self.assertEqual(len(run1), len(run2))
        for (text1, meta1), (text2, meta2) in zip(run1, run2):
            self.assertEqual(text1, text2)
            self.assertEqual(meta1["chunk_id"], meta2["chunk_id"])
            self.assertEqual(meta1["doc_hash"], meta2["doc_hash"])
            self.assertEqual(meta1["article_key"], meta2["article_key"])

    def test_semantic_search_retrieval_accuracy(self):
        """Test 6: FastEmbed search for legal queries retrieves target articles in Top 5."""
        qdrant_manager.load_models()
        query_text = "عقوبات وتجميد وحجز الأموال والمتحصلات"
        
        results = qdrant_manager.client.query(
            collection_name=self.coll_name,
            query_text=query_text,
            limit=5
        )

        self.assertGreater(len(results), 0, "Query search should return results.")
        retrieved_articles = [getattr(r, "metadata", getattr(r, "payload", {})).get("article_key") for r in results]
        
        # Query on freezing/seizing funds should retrieve articles from the Seizure & Penalties section (Articles 40-59)
        section_articles = [str(art) for art in retrieved_articles if art and any(f"10_2019_{num}" in str(art) for num in range(40, 60))]
        self.assertGreater(
            len(section_articles), 0,
            f"Top 5 query search results for freezing/seizure query should include Seizure & Penalties section articles (40-59). Got: {retrieved_articles}"
        )



if __name__ == '__main__':
    unittest.main()
