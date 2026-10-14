"""Public API response contracts."""
from pydantic import BaseModel, Field

class HealthResponse(BaseModel):
    status:str
    database_ready:bool

class QueryValidationResponse(BaseModel):
    supported_domain:bool
    safe_intents:list[str]
    requires_llm:bool
    message:str|None=None

class QueryResponse(BaseModel):
    answer:str
    plan:dict
    evidence:dict|None=None
    warnings:list[str]=Field(default_factory=list)
    clarification_needed:bool=False
    options:list[str]|None=None
    execution_status:str='completed'
    result_count:int=0
    source_tables:list[str]=Field(default_factory=list)
    referenced_node_ids:list[str]=Field(default_factory=list)
    metric_definitions:list[str]=Field(default_factory=list)

class DatasetStatsResponse(BaseModel):
    tables:list[dict]
    ingestion_errors:int

class GraphResponse(BaseModel):
    nodes:list[dict]
    edges:list[dict]
    total_nodes:int
    total_edges:int

class EntitySearchResponse(BaseModel):
    results:list[dict]
    count:int

class TopProductsResponse(BaseModel):
    results:list[dict]
    rank_by:str
    definition:str

class QualityReportResponse(BaseModel):
    ingestion_errors:list[dict]
    profile:dict|None=None
    unresolved_billing_references:list[dict]=Field(default_factory=list)
    ambiguous_billing_references:list[dict]=Field(default_factory=list)
