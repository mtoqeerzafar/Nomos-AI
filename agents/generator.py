"""
Phase 6 — Generation Engine Contracts & Implementations v1.1 (Production Frozen)
Defines frozen Pydantic contracts and 5 Cohesive Sub-Engines for GeneratorAgent.
"""

import os, re, json, time, hashlib
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from langchain_core.documents import Document
from langchain_openai import AzureChatOpenAI


# ==============================================================================
# ENUMS & TAXONOMIES
# ==============================================================================

GENERATOR_CONTRACT_VERSION = "1.1"
PROMPT_TEMPLATE_VERSION = "ARABIC_LEGAL_v1.1"

GeneratorFailureMode = Literal[
    "EMPTY_GRAPH",
    "TOKEN_BUDGET_FAILURE",
    "PROMPT_BUILD_FAILURE",
    "LLM_TIMEOUT",
    "EMPTY_OUTPUT",
    "INVALID_JSON",
    "CLAIM_BIND_FAILURE",
    "NONE"
]

GapType = Literal[
    "NO_RETRIEVAL",
    "INSUFFICIENT_EVIDENCE",
    "CONFLICTING_EVIDENCE",
    "OUTSIDE_SCOPE",
    "LOW_CONFIDENCE",
    "TOKEN_LIMIT",
    "MISSING_ROLE"
]

CompressionProfile = Literal["FULL", "LIGHT", "AGGRESSIVE", "EMERGENCY"]


# ==============================================================================
# DATA CONTRACTS (v1.1 Frozen)
# ==============================================================================

class GenerationProvenance(BaseModel):
    """Detailed provenance tracking for reproducible generation decisions."""
    prompt_version: str = Field(default=PROMPT_TEMPLATE_VERSION)
    graph_schema_version: str = Field(default="1.1")
    layout_template_version: str = Field(default="LAYOUT_v1.0")
    compression_profile: CompressionProfile = Field(default="FULL")
    cache_hit: bool = Field(default=False)
    cache_key: Optional[str] = Field(default=None)
    cache_build_ms: float = Field(default=0.0)


class DraftMetadata(BaseModel):
    """Production execution telemetry and structural metadata for the generated draft."""
    total_claims: int = Field(default=0, description="Total number of factual legal claims in the draft.")
    claims_bound: int = Field(default=0, description="Total claims successfully bound to evidence nodes.")
    citations_bound_count: int = Field(default=0, description="Total number of canonical legal citations bound.")
    legal_documents_used: List[str] = Field(default_factory=list, description="List of unique law identifiers used.")
    generation_time_ms: float = Field(default=0.0, description="Execution time of Generation Engine in milliseconds.")
    prompt_tokens: int = Field(default=0, description="Estimated total tokens consumed in system + user prompt.")
    completion_tokens: int = Field(default=0, description="Estimated tokens consumed in LLM output.")
    graph_nodes_count: int = Field(default=0, description="Total nodes in evidence reasoning graph.")
    graph_edges_count: int = Field(default=0, description="Total relational edges in evidence reasoning graph.")
    compression_ratio: float = Field(default=1.0, description="Context compression ratio (retained_tokens / total_tokens).")
    answer_language: str = Field(default="ar", description="Language of generated text ('ar' or 'en').")
    layout_used: str = Field(default="DEFAULT", description="Layout template used (e.g. 'LEGAL_EVOLUTION').")
    strategy_used: str = Field(default="COMPLETE", description="Inherited generation strategy from RelevanceDecision.")
    provenance: GenerationProvenance = Field(default_factory=GenerationProvenance, description="Detailed generation provenance.")


class ClaimBinding(BaseModel):
    """Single factual claim anchored to source evidence graph nodes with evidence density."""
    claim_id: str = Field(description="Deterministic relational claim ID, e.g. CLAIM_LAW20_ART16_PRIMARY_001")
    statement: str = Field(description="The specific factual legal assertion generated in Arabic.")
    source_article_key: str = Field(description="Source statutory article key, e.g. '16'")
    source_law_number: str = Field(description="Source law number, e.g. '20'")
    evidence_role: str = Field(default="PRIMARY_OBLIGATION", description="Role of supporting evidence.")
    supporting_chunk_ids: List[str] = Field(default_factory=list, description="Parent chunk IDs or chunk keys.")
    canonical_citations: List[str] = Field(default_factory=list, description="Bound legal citations injected by Engine 5.")
    generated_text_span: Optional[str] = Field(default=None, description="Exact text snippet in draft corresponding to this claim.")
    evidence_density: int = Field(default=1, description="Count of supporting chunks/nodes grounding this claim.")
    evidence_confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(default="HIGH", description="Confidence based on evidence density.")
    contradictory_sources: List[str] = Field(default_factory=list, description="List of node IDs with conflicting provisions.")
    superseded_sources: List[str] = Field(default_factory=list, description="List of node IDs superseded by amendments.")
    effective_source: Optional[str] = Field(default=None, description="The governing active source node ID.")


class UnresolvedGap(BaseModel):
    """Structured gap descriptor for unresolved information or missing roles."""
    gap_type: GapType = Field(description="Taxonomy classification of the gap.")
    description: str = Field(description="Arabic description of missing information.")
    target_article_or_role: Optional[str] = Field(default=None, description="Target missing article key or evidence role.")


class ConversationContext(BaseModel):
    """Resolved conversational context from Memory/Planner for follow-up grounding."""
    active_document: Optional[str] = Field(default=None, description="Active primary law or document family.")
    active_law_number: Optional[str] = Field(default=None, description="Active law number from prior turns.")
    active_articles: List[str] = Field(default_factory=list, description="Articles referenced in current conversation scope.")
    resolved_pronouns: Dict[str, str] = Field(default_factory=dict, description="Pronoun resolution map, e.g. {'عقوباتها': 'المادة 16'}.")
    previous_summary: Optional[str] = Field(default=None, description="Summary of preceding conversational turn.")
    user_goal: Optional[str] = Field(default=None, description="Overarching intent of user's multi-turn session.")
    conversation_language: str = Field(default="ar", description="Language of user conversation.")


class EvidenceGraphNode(BaseModel):
    """Single node in the relational Evidence Reasoning Graph."""
    node_id: str = Field(description="Unique node key, e.g. LAW_20_2018_ART_16")
    law_number: str = Field(description="Law number")
    law_year: Optional[str] = Field(default=None, description="Law year")
    article_key: str = Field(description="Article key or number")
    evidence_role: str = Field(description="PRIMARY_OBLIGATION, SANCTION_PENALTY, PROCEDURAL_RULE, etc.")
    clean_text: str = Field(description="Cleaned, structured statutory text snippet")
    cross_references: List[str] = Field(default_factory=list, description="Referenced article keys or law numbers")
    parent_chunk_id: Optional[str] = Field(default=None)


class EvidenceReasoningGraph(BaseModel):
    """Relational in-memory reasoning graph constructed by Engine 1."""
    graph_schema_version: str = Field(default="1.1", description="Evidence graph schema version.")
    nodes: Dict[str, EvidenceGraphNode] = Field(default_factory=dict, description="Node ID -> EvidenceGraphNode")
    relational_edges: List[Dict[str, str]] = Field(default_factory=list, description="Edges: [{'source': A, 'target': B, 'rel': 'references'}]")
    laws_covered: List[str] = Field(default_factory=list)
    articles_covered: List[str] = Field(default_factory=list)
    roles_present: List[str] = Field(default_factory=list)


class GeneratorOutput(BaseModel):
    """
    Frozen public contract returned by GeneratorAgent (v1.1).
    Purely generative output — truth validation is delegated to VerificationAgent.
    """
    generator_schema_version: str = Field(default=GENERATOR_CONTRACT_VERSION, description="Immutable Generator contract version.")
    generation_strategy_used: str = Field(description="Strategy inherited from RelevanceDecision.")
    structured_answer: str = Field(description="Full formatted Arabic legal response text.")
    claims: List[ClaimBinding] = Field(default_factory=list, description="Relational claim map anchored to evidence graph.")
    citations_bound: List[str] = Field(default_factory=list, description="Exact legal citations bound by Engine 5.")
    unresolved_gaps: List[UnresolvedGap] = Field(default_factory=list, description="Structured evidence gaps flagged by RelevanceChecker.")
    warnings_and_disclaimers: List[str] = Field(default_factory=list, description="Active disclaimers (e.g. partial scope warning).")
    metadata: DraftMetadata = Field(default_factory=DraftMetadata, description="Draft execution metrics and telemetry.")
    failure_mode: GeneratorFailureMode = Field(default="NONE", description="Deterministic failure mode enum.")


class VerificationInput(BaseModel):
    """
    Complete payload handed off to Phase 7 (Verification Agent).
    Verification evaluates factual correctness, support status, and contradictions.
    """
    verification_schema_version: str = Field(default="1.1", description="Contract version for Verification handoff.")
    generator_output: GeneratorOutput = Field(description="Output produced by GeneratorAgent.")
    evidence_reasoning_graph: EvidenceReasoningGraph = Field(description="Relational evidence graph built by Engine 1.")
    raw_query: str = Field(description="User's original question.")
    planner_decision: Dict[str, Any] = Field(default_factory=dict, description="Planner decision dictionary.")
    relevance_decision: Dict[str, Any] = Field(default_factory=dict, description="RelevanceDecision dict.")


# ==============================================================================
# ENGINE 1: Evidence Reasoning Graph Builder (Deterministic — 0ms)
# ==============================================================================

class EvidenceReasoningGraphBuilder:
    """Builds a relational in-memory reasoning graph from retrieved documents."""

    @classmethod
    def build_graph(cls, documents: List[Document]) -> EvidenceReasoningGraph:
        nodes = {}
        edges = []
        laws_set = set()
        articles_set = set()
        roles_set = set()

        for idx, doc in enumerate(documents):
            law_num = str(doc.metadata.get("law_number") or "20")
            law_year = str(doc.metadata.get("law_year")) if doc.metadata.get("law_year") else None
            art_key = str(doc.metadata.get("article_key") or doc.metadata.get("article_number") or idx + 1)
            role = str(doc.metadata.get("evidence_role") or "PRIMARY_OBLIGATION")
            chunk_id = doc.metadata.get("parent_chunk_id") or f"chunk_{art_key}_{law_num}"

            node_id = f"LAW_{law_num}_{law_year or 'UNDATED'}_ART_{art_key}"
            clean_text = doc.page_content.strip().replace("\r", "").replace("\n\n", "\n")

            refs = re.findall(r"المادة\s*\(?(\d+)\)?", clean_text)
            cross_refs = list(set(refs) - {art_key})

            node = EvidenceGraphNode(
                node_id=node_id,
                law_number=law_num,
                law_year=law_year,
                article_key=art_key,
                evidence_role=role,
                clean_text=clean_text,
                cross_references=cross_refs,
                parent_chunk_id=chunk_id,
            )

            nodes[node_id] = node
            laws_set.add(law_num)
            articles_set.add(art_key)
            roles_set.add(role)

            for target_art in cross_refs:
                edges.append({
                    "source": node_id,
                    "target_article": target_art,
                    "relation": "cross_references"
                })

        return EvidenceReasoningGraph(
            graph_schema_version="1.1",
            nodes=nodes,
            relational_edges=edges,
            laws_covered=sorted(list(laws_set)),
            articles_covered=sorted(list(articles_set)),
            roles_present=sorted(list(roles_set)),
        )


# ==============================================================================
# ENGINE 2: Prompt Context Optimizer & Token Budget Manager (Deterministic — 0ms)
# ==============================================================================

class PromptContextOptimizer:
    """Prunes redundant text, applies priority-based token budgeting, and builds structured prompt context."""

    ROLE_PRIORITIES = {
        "PRIMARY_OBLIGATION": 1,
        "SANCTION_PENALTY": 2,
        "EXCEPTION_CLAUSE": 3,
        "PROCEDURAL_RULE": 4,
        "DEFINITION": 5,
        "SUPPORTING_CONTEXT": 6,
    }

    @classmethod
    def optimize_context(
        cls,
        graph: EvidenceReasoningGraph,
        max_context_tokens: int = 3500,
        conversation_context: Optional[ConversationContext] = None
    ) -> tuple[str, float, CompressionProfile]:
        sorted_nodes = sorted(
            graph.nodes.values(),
            key=lambda n: (cls.ROLE_PRIORITIES.get(n.evidence_role, 99), n.law_number, n.article_key)
        )

        total_available_tokens = sum(len(n.clean_text.split()) * 1.3 for n in sorted_nodes) + 1.0

        context_blocks = []
        estimated_tokens = 0

        if conversation_context and conversation_context.previous_summary:
            conv_block = f"[سياق المحادثة السابقة: {conversation_context.previous_summary}]"
            context_blocks.append(conv_block)
            estimated_tokens += len(conv_block.split()) * 1.3

        for node in sorted_nodes:
            citation_str = f"المادة {node.article_key} من القانون {node.law_number}" + (f" لسنة {node.law_year}" if node.law_year else "")
            block = f"--- [معرف: {node.node_id} | الدور: {node.evidence_role} | {citation_str}] ---\n{node.clean_text}"
            node_tokens = len(block.split()) * 1.3

            if estimated_tokens + node_tokens > max_context_tokens:
                break

            context_blocks.append(block)
            estimated_tokens += node_tokens

        compression_ratio = min(1.0, round(estimated_tokens / total_available_tokens, 3))
        
        if compression_ratio >= 0.95:
            profile: CompressionProfile = "FULL"
        elif compression_ratio >= 0.70:
            profile = "LIGHT"
        elif compression_ratio >= 0.40:
            profile = "AGGRESSIVE"
        else:
            profile = "EMERGENCY"

        return "\n\n".join(context_blocks), compression_ratio, profile


# ==============================================================================
# ENGINE 3: Strategy & Layout Router (Deterministic — 0ms)
# ==============================================================================

class StrategyLayoutRouter:
    """Maps RelevanceDecision.generation_strategy to specialized Arabic legal layout instructions."""

    LAYOUT_TEMPLATES = {
        "LEGAL_EVOLUTION": """
استخدم الهيكل التشريعي التالي المكون من 4 أجزاء لتحليل التطور القانوني والتعديلات:
1. **الإطار القانوني الأصلي**: النص والسياق التشريعي في القانون الأصلي.
2. **التعديلات والقرارات التنفيذية اللاحقة**: التعديلات المنظمة والقرارات التنفيذية المتعاقبة.
3. **الحكم القانوني النافذ حالياً**: القاعدة القانونية النهائية السارية والمعتمدة في الوقت الحالي.
4. **الأثر والتطبيق العملي**: كيفية التزام المنشآت والأشخاص بالحكم النهائي.
""",
        "COMPARISON": """
استخدم هيكل المقارنة التشريعية التالي:
1. **نطاق المقارنة والصكوك التشريعية**: تحديد التشريعات والقوانين محل المقارنة.
2. **التحليل المقارن للأحكام والالتزامات**: المقارنة التفصيلية بين النصوص والمواد.
3. **الفروق الجوهرية وتنسيق التطبيق**: تحديد أوجه الاختلاف والتكامل بين النصوص.
""",
        "PROCEDURAL": """
استخدم هيكل الإجراءات الإدارية والقانونية:
1. **الشروط والإجراءات الواجب اتباعها**: الخطوات المتسلسلة للامتثال.
2. **الجهات المختصة وآليات الإخطار**: السلطات المعنية وكيفية الإفصاح/الإبلاغ.
3. **المهل الزمانية وتوقيت الامتثال**: المواعيد المحددة قانوناً.
""",
        "PARTIAL_WITH_WARNING": """
قم بإنشاء الرد مع إدراج تنبيه صريح في البداية بشأن النقص في الأدلة المتاحة:
- **تنبيه**: الأدلة المسترجعة تغطي الالتزامات الأساسية ولكنها تفتقر لتغطية كاملة لبعض التفاصيل الاستثنائية أو العقوبات.
""",
        "DEFAULT": """
استخدم صياغة قانونية سليمة ومباشرة مقسمة إلى فقرات واضحة تدعم التحليل القانوني الدقيق.
"""
    }

    @classmethod
    def get_layout_instructions(cls, strategy: str) -> str:
        return cls.LAYOUT_TEMPLATES.get(strategy, cls.LAYOUT_TEMPLATES["DEFAULT"])


# ==============================================================================
# ENGINE 5: Claim & Citation Binder (Deterministic — 10ms)
# ==============================================================================

class ClaimCitationBinder:
    """Scans draft text, binds claim IDs to canonical citations, calculates evidence density, and formats final answer."""

    @classmethod
    def bind_claims_and_citations(
        cls,
        draft_text: str,
        graph: EvidenceReasoningGraph
    ) -> tuple[str, List[ClaimBinding], List[str]]:
        claims = []
        citations_bound_set = set()

        pattern = r"\[CLAIM:([^\]]+)\]"
        matches = list(re.finditer(pattern, draft_text))

        clean_answer = draft_text
        claim_counter = 1

        for match in matches:
            node_id = match.group(1).strip()
            node = graph.nodes.get(node_id)

            if node:
                citation_str = f"المادة {node.article_key} من القانون {node.law_number}" + (f" لسنة {node.law_year}" if node.law_year else "")
                citations_bound_set.add(citation_str)

                claim_id = f"CLAIM_{node.law_number}_ART{node.article_key}_{node.evidence_role}_{claim_counter:03d}"
                claim_counter += 1

                start_idx = max(0, match.start() - 120)
                end_idx = min(len(draft_text), match.end() + 120)
                statement_span = draft_text[start_idx:end_idx].strip()

                matching_nodes = [n for n in graph.nodes.values() if n.article_key == node.article_key and n.law_number == node.law_number]
                density = len(matching_nodes)
                confidence: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH" if density >= 2 else ("MEDIUM" if density == 1 else "LOW")

                claim = ClaimBinding(
                    claim_id=claim_id,
                    statement=statement_span,
                    source_article_key=node.article_key,
                    source_law_number=node.law_number,
                    evidence_role=node.evidence_role,
                    supporting_chunk_ids=[n.parent_chunk_id for n in matching_nodes if n.parent_chunk_id],
                    canonical_citations=[citation_str],
                    generated_text_span=statement_span,
                    evidence_density=density,
                    evidence_confidence=confidence,
                    effective_source=node.node_id
                )
                claims.append(claim)

                clean_answer = clean_answer.replace(match.group(0), f" [{citation_str}]")

        return clean_answer.strip(), claims, sorted(list(citations_bound_set))


# ==============================================================================
# MAIN GENERATOR AGENT (Engine 4 Synthesizer + Orchestration)
# ==============================================================================

class GeneratorAgent:
    """Phase 6 Generation Engine main orchestrator v1.1 Frozen."""

    def __init__(self, model=None):
        if model is not None:
            self.model = model
        else:
            try:
                from utils.llm_factory import get_llm
                self.model = get_llm(temperature=0.0, max_tokens=1500)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to initialize LLM via get_llm(): {e}")
                self.model = None

    def generate(
        self,
        query: str,
        documents: List[Document],
        relevance_decision: Optional[Dict[str, Any]] = None,
        planner_decision: Optional[Dict[str, Any]] = None,
        conversation_context: Optional[ConversationContext] = None
    ) -> GeneratorOutput:
        start_time = time.time()
        rel_dec = relevance_decision or {}
        strategy = rel_dec.get("generation_strategy", "COMPLETE")

        if not documents:
            return GeneratorOutput(
                generator_schema_version=GENERATOR_CONTRACT_VERSION,
                generation_strategy_used=strategy,
                structured_answer="عذراً، لم تتوفر أي أدلة قانونية في حزمة المستندات لإصدار إجابة مستندة.",
                failure_mode="EMPTY_GRAPH",
                metadata=DraftMetadata(
                    generation_time_ms=(time.time() - start_time) * 1000,
                    strategy_used=strategy,
                    provenance=GenerationProvenance(
                        prompt_version=PROMPT_TEMPLATE_VERSION,
                        compression_profile="EMERGENCY"
                    )
                )
            )

        # Engine 1: Build Evidence Reasoning Graph
        graph = EvidenceReasoningGraphBuilder.build_graph(documents)

        # Engine 2: Optimize Prompt Context & Token Budgeting
        cache_build_start = time.time()
        optimized_context, comp_ratio, comp_profile = PromptContextOptimizer.optimize_context(
            graph=graph,
            conversation_context=conversation_context
        )
        cache_build_ms = (time.time() - cache_build_start) * 1000

        # Build prompt cache key
        cache_key = hashlib.md5(f"{query}_{len(documents)}_{strategy}".encode('utf-8')).hexdigest()[:12]

        # Engine 3: Get Layout Instructions
        layout_instructions = StrategyLayoutRouter.get_layout_instructions(strategy)

        # Detect target output language from planner_decision or conversation_context
        target_lang = (planner_decision or {}).get("output_language", "Arabic")
        if conversation_context and conversation_context.conversation_language in ("en", "English"):
            target_lang = "English"

        lang_instruction = (
            "IMPORTANT: The user requested an ENGLISH response. Write the complete structured answer in clear, professional English. "
            "Translate all Arabic legal evidence into accurate English while retaining exact [CLAIM:node_id] tags."
            if target_lang.lower() == "english"
            else "تنبيه: قم بكتابة الإجابة باللغة العربية الفصحى القانونية الدقيقة مع الالتزام التام بوضع رموز [CLAIM:node_id]."
        )

        # Engine 4: Grounded Draft Synthesizer Prompt
        system_prompt = f"""أنت محرك التوليد والاستدلال القانوني في نظام RagnrAI للتشريعات الإماراتية.
مهمتك: صياغة إجابة قانونية دقيقة، رصينة، ومستندة 100% إلى أدلة نصوص المواد المقدمة.

توجيه اللغة:
{lang_instruction}

قواعد التوليد الصارمة:
1. لا تقم بتوليد أي ادعاء قانوني غير موجود بالنصوص.
2. لكل ادعاء أو حكم تذكره، يجب أن ترفقه بنهاية الجملة برمز معرف العقدة بالشكل: [CLAIM:node_id] (مثال: [CLAIM:LAW_20_UNDATED_ART_16]).
3. لا تقم بكتابة نصوص الإحالة أو أرقام القوانين من تلقاء نفسك؛ استخدم فقط رمز [CLAIM:node_id] وسيقوم المحرك بتركيب الهامش القانوني تلقائياً.

هيكل الإجابة المطلوب:
{layout_instructions}

الأدلة والبيانات القانونية المتاحة:
{optimized_context}
"""

        user_prompt = f"السؤال القانوني: {query}"
        prompt_tokens = int(len(system_prompt.split()) * 1.3 + len(user_prompt.split()) * 1.3)

        if self.model is None:
            raw_draft = f"بناءً على نصوص المواد المتاحة: [CLAIM:{list(graph.nodes.keys())[0]}]"
        else:
            try:
                response = self.model.invoke([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ])
                raw_draft = response.content
            except Exception as e:
                return GeneratorOutput(
                    generator_schema_version=GENERATOR_CONTRACT_VERSION,
                    generation_strategy_used=strategy,
                    structured_answer="حدث خطأ أثناء الاتصال بمحرك التوليد.",
                    failure_mode="LLM_TIMEOUT",
                    metadata=DraftMetadata(
                        generation_time_ms=(time.time() - start_time) * 1000,
                        prompt_tokens=prompt_tokens,
                        strategy_used=strategy,
                        provenance=GenerationProvenance(
                            prompt_version=PROMPT_TEMPLATE_VERSION,
                            compression_profile=comp_profile,
                            cache_key=cache_key,
                            cache_build_ms=cache_build_ms
                        )
                    )
                )

        completion_tokens = int(len(raw_draft.split()) * 1.3)

        # Engine 5: Claim & Citation Binder
        clean_answer, claims, citations_bound = ClaimCitationBinder.bind_claims_and_citations(raw_draft, graph)

        exec_time = (time.time() - start_time) * 1000

        raw_gaps = rel_dec.get("evidence_gaps", [])
        structured_gaps = []
        if isinstance(raw_gaps, list):
            for gap in raw_gaps:
                if isinstance(gap, dict):
                    structured_gaps.append(UnresolvedGap(
                        gap_type="MISSING_ROLE" if gap.get("missing_role") else "INSUFFICIENT_EVIDENCE",
                        description=f"نقص في تغطية الدور الأدلي: {gap.get('missing_role') or 'غير محدد'}",
                        target_article_or_role=gap.get("missing_role")
                    ))

        disclaimers = []
        if strategy == "PARTIAL_WITH_WARNING" or structured_gaps:
            disclaimers.append("تنبيه: تحتوي الأدلة الحالية على تغطية جزئية قد لا تشمل جميع الأحكام الاستثنائية.")

        return GeneratorOutput(
            generator_schema_version=GENERATOR_CONTRACT_VERSION,
            generation_strategy_used=strategy,
            structured_answer=clean_answer,
            claims=claims,
            citations_bound=citations_bound,
            unresolved_gaps=structured_gaps,
            warnings_and_disclaimers=disclaimers,
            failure_mode="NONE" if claims else "EMPTY_OUTPUT",
            metadata=DraftMetadata(
                total_claims=len(claims),
                claims_bound=len([c for c in claims if c.canonical_citations]),
                citations_bound_count=len(citations_bound),
                legal_documents_used=graph.laws_covered,
                generation_time_ms=exec_time,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                graph_nodes_count=len(graph.nodes),
                graph_edges_count=len(graph.relational_edges),
                compression_ratio=comp_ratio,
                answer_language="ar",
                layout_used=strategy,
                strategy_used=strategy,
                provenance=GenerationProvenance(
                    prompt_version=PROMPT_TEMPLATE_VERSION,
                    compression_profile=comp_profile,
                    cache_key=cache_key,
                    cache_build_ms=cache_build_ms
                )
            )
        )
