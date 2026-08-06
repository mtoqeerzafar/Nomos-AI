# Architecture Specification: Query Rewriter Agent (v1.1)

## 1. Overview
The **Query Rewriter Agent (v1.1)** (`agents/rewriter.py`) transforms conversational user queries containing pronouns, relative references, or follow-up shortcuts into standalone, self-contained retrieval queries.

---

## 2. Core Responsibilities

- **Pronoun Resolution**: Resolves ambiguous pronouns (e.g. "What are its penalties?" $\rightarrow$ "What are the penalties under Article 16 of Federal Law No. 20 of 2018?").
- **Multi-Turn Context Synthesis**: Integrates entity names, law numbers, and article numbers mentioned in previous conversation turns.
- **Query Expansion**: Generates multi-query variations to maximize recall during dense/sparse vector retrieval.

---

## 3. Algorithm & Code Snippet

```python
class QueryRewriterAgent:
    def rewrite(self, question: str, chat_history: List[Dict[str, str]], planner_decision: Dict[str, Any]) -> Tuple[str, List[str]]:
        if not chat_history or not planner_decision.get("needs_chat_history", False):
            return question, [question]
            
        system_prompt = """You are a legal query rewriter. Transform the user's follow-up question into a fully standalone query that includes all relevant law numbers, article numbers, and legal terms from the conversation history."""
        ...
```

---

## 4. Inputs & Outputs
- **Inputs**: `question: str`, `chat_history: List[Dict[str, str]]`, `planner_decision: Dict[str, Any]`
- **Outputs**:
  - `standalone_query: str` (Stored in `AgentState["current_query"]`)
  - `retrieval_queries: List[str]` (Stored in `AgentState["retrieval_queries"]`)
