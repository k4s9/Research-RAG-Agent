from src.core.agent.orchestrator import AgentOrchestrator
from src.core.ingest.pipeline import DocumentIngestPipeline
from src.core.retrieval.hybrid_search import HybridSearch


def get_ingest_pipeline() -> DocumentIngestPipeline:
    return DocumentIngestPipeline()


def get_searcher() -> HybridSearch:
    return HybridSearch()


def get_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator()
