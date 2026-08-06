import os
import sys
import json
from pathlib import Path

def generate_report(trace_data, output_dir, index):
    tc = trace_data.get("test_case", {})
    trace = trace_data.get("trace", {})
    
    query = tc.get("query", "Unknown query")
    expected_answer = tc.get("expected_answer", "N/A")
    
    # Extract trace components safely
    planner = trace.get("planner", {}).get("decision", {})
    
    # Retrieval
    retrieval_stats = trace.get("retrieval_statistics", {})
    raw_candidates = retrieval_stats.get("raw_candidates", 0)
    after_rerank = retrieval_stats.get("after_rerank", 0)
    
    # Relevance
    relevance_res = trace.get("relevance", {})
    
    # Final Outcome
    failure_stage = trace.get("failure_stage", "UNKNOWN")
    first_failure_stage = trace.get("first_failure_stage", "UNKNOWN")
    failure_code = trace.get("failure_code", "UNKNOWN")
    failure_reasoning = trace.get("failure_reasoning", "None provided")
    
    # Calculate retention rates
    retention_rate = f"{(after_rerank / raw_candidates * 100):.0f}%" if raw_candidates > 0 else "0%"
    
    gold_chunks = tc.get("gold_chunks", [])
    lifecycle = trace.get("candidate_lifecycle", [])
    
    def is_gold(chunk):
        if not gold_chunks: return False
        for g in gold_chunks:
            match_source = g.get("source")
            match_article = g.get("article_number")
            is_match = True
            if match_source and match_source != chunk.get("source"):
                is_match = False
            if match_article and match_article != chunk.get("article_number"):
                is_match = False
            if is_match:
                return True
        return False

    table_lines = [
        "| Rank | Document | Article | Origin | BM25 | Dense | RRF | Score | Decision | Reason | Gold |",
        "|---|---|---|---|---|---|---|---|---|---|---|"
    ]
    
    gold_retrieved = False
    gold_reranked = False
    gold_used = False
    
    for i, chunk in enumerate(lifecycle):
        gold = is_gold(chunk)
        if gold:
            gold_retrieved = True
            if chunk.get("selected"): gold_reranked = True
            if chunk.get("sent_to_generator"): gold_used = True
            
        doc_src = chunk.get("source") or "Unknown"
        doc_short = doc_src if len(doc_src) <= 30 else doc_src[:27] + "..."
        art = chunk.get("article_number") or ""
        origin = chunk.get("retrieval_origin") or ""
        bm25 = str(chunk.get("bm25_rank") or "-")
        dense = str(chunk.get("dense_rank") or "-")
        rrf = str(chunk.get("rrf_rank") or "-")
        score = f"{chunk.get('rerank_score'):.3f}" if chunk.get('rerank_score') is not None else "-"
        decision = chunk.get("decision") or "-"
        reason = chunk.get("drop_reason") or "-"
        g_mark = "✅" if gold else "❌"
        
        table_lines.append(f"| {i+1} | {doc_short} | {art} | {origin} | {bm25} | {dense} | {rrf} | {score} | {decision} | {reason} | {g_mark} |")
        
    candidate_table = "\\n".join(table_lines)
    
    if gold_chunks:
        rc = "## Root Cause Analysis\\n\\n"
        if gold_used:
            rc += "✓ Gold chunk retrieved\\n✓ Gold chunk reranked\\n✓ Gold chunk passed relevance\\n\\n**Recommended action:** Investigate generation or verification stage."
        elif gold_reranked:
            rc += "✓ Gold chunk retrieved\\n✓ Gold chunk reranked\\n✗ Gold chunk rejected by relevance checker\\n\\n**Recommended action:** Investigate relevance prompt or check if retrieved context is missing something."
        elif gold_retrieved:
            rc += "✓ Gold chunk retrieved\\n✗ Gold chunk removed by threshold\\n\\n**Recommended action:** Investigate FlashRank threshold or reranker performance."
        else:
            rc += "✗ Gold chunk never retrieved in Top 30\\n\\n**Recommended action:** Investigate retrieval components (BM25, Dense, Hybrid Alpha)."
        root_cause = rc
    else:
        root_cause = "## Root Cause Analysis\\n\\nNo gold chunks defined for this query."
        
    report_content = f"""# Diagnostic Report: Failed Query {index}

## Overview
**Query:** `{query}`
**Expected Answer:**
> {expected_answer}

## Trace Analysis

### 1. Planner
- **Query Type:** {planner.get('query_type', 'N/A')}
- **Intent Type:** {planner.get('intent_type', 'N/A')}

### 2. Retrieval
- **Raw Candidates Retrieved:** {raw_candidates}
- **Retrieved Chunks Passed to Reranker:** {raw_candidates}

### 3. Reranker
- **Reranked Chunks Kept:** {after_rerank}
- **Retrieval Retention Rate:** {retention_rate}

### 4. Relevance
- **Sufficient:** {relevance_res.get('sufficient', 'N/A')}
- **Evidence Type:** {relevance_res.get('evidence_type', 'N/A')}

### 5. Failure Diagnosis
- **First Failure Stage:** `{first_failure_stage}`
- **Final Failure Stage:** `{failure_stage}`
- **Failure Code:** `{failure_code}`
- **Reasoning:** 
> {failure_reasoning}

## Candidate Lifecycle Top-30 Report

{candidate_table}

{root_cause}
"""



    report_path = output_dir / f"query_{index:02d}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    return report_path

def main():
    if len(sys.argv) < 3:
        print("Usage: python diagnostic.py <traces_json_path> <output_dir>")
        sys.exit(1)
        
    traces_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    
    if not traces_path.exists():
        print(f"File not found: {traces_path}")
        sys.exit(1)
        
    output_dir.mkdir(exist_ok=True, parents=True)
    
    with open(traces_path, "r", encoding="utf-8") as f:
        traces = json.load(f)
        
    print(f"Generating reports for {len(traces)} failed queries...")
    for idx, trace_data in enumerate(traces):
        generate_report(trace_data, output_dir, idx + 1)
        
    print(f"Done. Reports saved to {output_dir}")

if __name__ == "__main__":
    main()
