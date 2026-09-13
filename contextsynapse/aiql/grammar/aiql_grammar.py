"""
AIQL (Quantum Graph Query Language) - EBNF Grammar Definition
A declarative, SQL-inspired but graph-native query language designed for both:
1. Pure graph querying and analytics
2. Hybrid Graph + RAG (retrieval-augmented) search
"""

# AIQL EBNF Grammar - Lark/PEG Compatible

AIQL_GRAMMAR = """
// AIQL Grammar Definition - Comprehensive Quantum Graph Query Language
// Supports variable declarations, graph operations, traversal, retrieval, analytics, and generation

// Main entry point - Support both unified and legacy queries
// Order matters! More specific rules first
start: (top_level_statement) (";" top_level_statement)* (";")?

// Top-level statements (standalone, not within pipelines)
// Separate names to avoid reduce/reduce with pipeline stages
// Priority: More specific queries first to avoid ambiguity
?top_level_statement: top_select | top_match | top_update_node | top_delete_node | top_update_edge | top_delete_edge
                    | top_create_graph | top_create_graph_as | top_create_graph_with_structure | top_create_graph_from_schema | top_create_node | top_create_edge | top_drop_graph 
                    | top_create_namespace | top_use_namespace | top_use_graph | top_show_namespaces | top_show_collections | top_show_pipelines | top_show_graphs | top_show_current_graph | top_show_stats
                    | top_create_index | top_show_indexes | top_describe
                    | top_pagerank | top_hop_query | top_shortest_path | top_community_detection | top_graph_summary
                    | top_create_collection | top_use_collection | top_traverse | top_merge_node
                    | top_neighbors | top_create_subgraph | top_find_by_uuid
                    | create_pipeline | retrieval_pipeline | run_pipeline_cmd | classify_file_cmd | simple_rag_query | rag_generate | rag_query_one_shot
                    | pipeline | hybrid_search | search_query | search_query_basic | search_query_collections | graph_search_query | graph_search_from_via_to | aggregate_query | set_mode_statement
                    | variable_decl | insert_into | evaluate_rag | update_pipeline | for_loop
                    | session_statement | create_function | use_function | agent_statement | memory_statement | operational_statement
                    | top_diff_node | top_diff_edges
                    | top_read_url | top_read_domain | top_extract_from_raw | top_extract_entities
                    | top_create_evalset | top_abtest_rag | top_attach_policy | top_set_guardrails
                    | top_pre_guard | top_post_guard | top_create_fine_tune_dataset | top_fine_tune_model
                    | top_register_model | top_promote_model | top_rollback_model | top_merge_results | top_rerank | top_reason
                    | top_blockchain_verify | top_blockchain_block | top_blockchain_length | top_blockchain_latest
                    | top_blockchain_audit | top_blockchain_merkle | top_blockchain_blocks_range | top_blockchain_verify_block
                    | top_begin_transaction | top_commit_transaction | top_rollback_transaction

// Top-level SELECT (standalone version with aggregation support)
// Support both: SELECT * FROM Person AND SELECT NODE Person
top_select: "SELECT" select_target ("AS" identifier)? ("FROM" (node_spec | variable))? (as_of_clause | for_system_time)? ("USE" "INDEX" identifier)? ("WHERE" condition)? ("GROUP" "BY" qualified_identifier_list)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))? ("OFFSET" (number | variable))?
          | "SELECT" "NODE" identifier (traverse_path)? ("WHERE" condition)? ("LIMIT" (number | variable))? ("OFFSET" (number | variable))?
          | "SELECT" "EDGE" ("WHERE" condition)? ("LIMIT" (number | variable))? ("OFFSET" (number | variable))?
select_target: select_item ("," select_item)*
select_item: aggregation_function ("AS" identifier)? | qualified_identifier ("AS" identifier)? | "*" | "DISTINCT" qualified_identifier ("AS" identifier)?
qualified_identifier_list: qualified_identifier ("," qualified_identifier)*
traverse_path: ("->" identifier)+

// Top-level MATCH (standalone version)
top_match: "MATCH" match_pattern (return_clause)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))? ("OFFSET" (number | variable))?
          | "MATCH" "NODE" identifier ("AS" identifier)? ("WHERE" condition)? (return_clause)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))? ("OFFSET" (number | variable))?
match_pattern: match_path ("WHERE" condition)?
match_path: match_node (match_edge_arrow match_node)+
match_node: "(" identifier (":" identifier)? ("AS" identifier)? ("{" property_list "}")? ")"
match_edge: "[" (identifier ":")? ":"? identifier ("|" identifier)* ("*" (number ".." number)?)? ("AS" identifier)? ("{" property_list "}")? "]"
match_edge_arrow: "-" match_edge "->" | "<-" match_edge "-" | "-" match_edge "-"

// Top-level DELETE (standalone version)
top_delete_node: "DELETE" "ALL" "NODES" ("WHERE" condition)? | "DELETE" "NODE" node_spec ("WHERE" condition)?

// Top-level MERGE NODE
top_merge_node: "MERGE" "NODE" node_spec ("SET" "{" update_assignment_list "}")?

// Top-level Transaction Control (like Neo4j)
top_begin_transaction: "BEGIN" "TRANSACTION" | "BEGIN"
top_commit_transaction: "COMMIT" "TRANSACTION" | "COMMIT"
top_rollback_transaction: "ROLLBACK" "TRANSACTION" | "ROLLBACK"

// Top-level CREATE NAMESPACE (standalone version) - with optional MODE clause
top_create_namespace: "CREATE" "NAMESPACE" identifier ("MODE" namespace_mode)?
namespace_mode: "INTERACTIVE" | "PERSISTENT"

// Top-level USE NAMESPACE (standalone version) - with optional MODE clause
top_use_namespace: "USE" "NAMESPACE" identifier ("MODE" namespace_mode)?

// Top-level USE GRAPH (standalone version)
top_use_graph: "USE" "GRAPH" identifier

// Top-level SHOW NAMESPACES (standalone version)
top_show_namespaces: "SHOW" "NAMESPACES"

// Top-level SHOW GRAPHS (standalone version)
top_show_graphs: "SHOW" "GRAPHS"

// Top-level SHOW CURRENT GRAPH (standalone version)
top_show_current_graph: "SHOW" "CURRENT" "GRAPH"

// Top-level SHOW COLLECTIONS (standalone version)
top_show_collections: "SHOW" "COLLECTIONS"

// Top-level SHOW PIPELINES (standalone version)
top_show_pipelines: "SHOW" "PIPELINES"

// Top-level CREATE INDEX (standalone version)
top_create_index: "CREATE" "INDEX" identifier "ON" identifier "(" identifier_list ")" "TYPE" index_type ("MODEL" "=" (string | variable))?

// Top-level SHOW INDEXES (standalone version)
top_show_indexes: "SHOW" "INDEXES" ("IN" "COLLECTION" identifier)?

// Top-level SHOW STATS (standalone version)
top_show_stats: "SHOW" "STATS" | "SHOW" "STATISTICS"

// Top-level DESCRIBE (standalone version)
top_describe: "DESCRIBE" describe_target

// Top-level PAGERANK query
top_pagerank: "PAGERANK" ("ON" identifier)? (("PARAMETERS" "(" parameter_dict ")") | ("ITERATIONS" number)? ("DAMPING_FACTOR" | "DAMPING") number?)? ("RETURN" return_expression ("," return_expression)*)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))?

// Top-level HOP query
top_hop_query: "HOP" "FROM" identifier "TO" identifier ("DEPTH" number)?

// Top-level SHORTEST PATH query
top_shortest_path: "SHORTEST_PATH" "FROM" (string | variable | typed_node_ref | identifier) "TO" (string | variable | typed_node_ref | identifier) ("VIA" "EDGES" ("[" (identifier_list | string_list) "]" | "(" (identifier_list | string_list) ")"))? ("MAX_DEPTH" number)? ("WEIGHT_FIELD" identifier)? ("RETURN" return_expression ("," return_expression)*)?

// Top-level COMMUNITY DETECTION query
top_community_detection: "COMMUNITY_DETECTION" ("ON" identifier)? ("USING" "ALGORITHM" (string | identifier))? ("PARAMETERS" "(" parameter_dict ")")? ("RETURN" return_expression ("," return_expression)*)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))?

// Top-level GRAPH SUMMARY query
top_graph_summary: "GRAPH" "SUMMARY" ("ON" (identifier | "(" identifier_list ")")?)? ("RETURN" "{" summary_field_list "}")?

// Top-level CREATE COLLECTION
top_create_collection: "CREATE" "COLLECTION" identifier ("{" collection_schema "}")?
collection_schema: "fields" ":" "[" field_definition ("," field_definition)* "]"
field_definition: "{" identifier ":" type_spec "}"
type_spec: "string" | "number" | "float" | "int" | "boolean" | "datetime" | "date" | "time" | "array" | "object"

// Top-level USE COLLECTION
top_use_collection: "USE" "COLLECTION" identifier

// INSERT INTO
insert_into: "INSERT" "INTO" identifier "VALUES" "{" property_list "}"

// UPDATE PIPELINE
update_pipeline: "UPDATE" "PIPELINE" identifier "SET" "{" property_list "}"

// FOR loop
for_loop: "FOR" "each" variable "IN" (variable | top_select) "DO" (top_level_statement | variable_decl)+ "END"

// Top-level TRAVERSE
top_traverse: "TRAVERSE" traverse_spec (where_stage | "WHERE" condition)? return_clause? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))? (as_of_clause)?
                | "TRAVERSE" "FROM" (identifier | variable) ("WHERE" condition)? ("AT" "TIMESTAMP" (string | variable))? (("VIA" ("(" identifier_list ")" | identifier))? ("TO" identifier)? | ("TO" identifier)? ("VIA" ("(" identifier_list ")" | identifier))?) ("MAX" "DEPTH" number)? ("PARAMETERS" "(" parameter_dict ")")? ("WHERE" condition)? ("ORDER" "BY" order_by_clause)? ("LIMIT" (number | variable))?

// Top-level NEIGHBORS
top_neighbors: "NEIGHBORS" "FROM" node_spec ("WHERE" condition)? ("DEPTH" number)? ("VIA" "(" identifier_list ")")? return_clause? ("LIMIT" (number | variable))?

// Top-level FIND BY UUID
top_find_by_uuid: "FIND_BY_UUID" (string | variable)
                | "FIND" "BY" "UUID" (string | variable)

// Top-level CREATE SUBGRAPH
top_create_subgraph: "CREATE" "SUBGRAPH" identifier "AS" "MATCH" match_pattern return_clause

// Web scraping and ingestion
top_read_url: "READ" "URL" string ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" "RAW" identifier)?
top_read_domain: "READ" "DOMAIN" string ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" "RAW" identifier)?
top_extract_from_raw: "EXTRACT" "FROM" "RAW" (identifier | variable) ("AS" extract_target)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" node_spec)? ("LINK" link_spec)?
extract_target: "NODE" "ONLY" "TYPE" identifier | "EDGE" "ONLY" "TYPE" identifier | "NODE" "TYPE" identifier "AND" "EDGE" "TYPE" identifier
link_spec: "(" node_spec "TO" node_spec ")"
top_extract_entities: "EXTRACT" "ENTITIES" "FROM" (identifier | "(" identifier_list ")") ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?

// Evaluation
top_create_evalset: "CREATE" "EVALSET" (identifier | variable) "FROM" "NAMESPACE" (identifier | variable) ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "IN" "COLLECTION" identifier)?
top_abtest_rag: "ABTEST" "RAG" "ON" (identifier | variable) "VARIANTS" "(" variant_pair ("," variant_pair)* ")" ("PARAMETERS" "(" parameter_dict ")")? ("METRICS" ("[" (identifier | string) ("," (identifier | string))* "]" | "(" (identifier | string) ("," (identifier | string))* ")"))? ("STORE" "IN" "COLLECTION" identifier)?
variant_pair: identifier ":" (parameter_dict | "{" parameter_dict "}")

// Guardrails
top_attach_policy: "ATTACH" "POLICY" string "TO" ("NAMESPACE" | "PIPELINE") (identifier | variable)
top_set_guardrails: "SET" "GUARDRAILS" "FOR" ("NAMESPACE" | "PIPELINE") (identifier | variable) "{" parameter_dict "}"
top_pre_guard: "PRE_GUARD" "CHECK" ("QUERY" | "ANSWER") ("USING" "POLICY" string)? ("ON" string)?
top_post_guard: "POST_GUARD" "CHECK" ("QUERY" | "ANSWER") ("USING" "POLICY" string)? ("REQUIRE" "{" parameter_dict "}")?

// Fine-tuning
top_create_fine_tune_dataset: "CREATE" "FINE_TUNE_DATASET" string "FROM" "NAMESPACE" (identifier | variable) ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "IN" string)?
top_fine_tune_model: "FINE_TUNE" "MODEL" (string | variable) "USING" "DATASET" string ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "AS" string)?
top_register_model: "REGISTER" "MODEL" string "WITH" "{" parameter_dict "}"
top_promote_model: "PROMOTE" "MODEL" string "TO" string ("IF" condition)?
top_rollback_model: "ROLLBACK" "MODEL" string "TO" string ("REASON" string)?

// Result operations
top_merge_results: "MERGE" "RESULTS" "(" (identifier | variable) ("," (identifier | variable))* ")" ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" ("IN" variable)?)?
top_rerank: "RERANK" (identifier | variable) ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" ("IN" variable)?)?
top_reason: "REASON" "ON" ("COLLECTION" | identifier) (identifier | variable) ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" "AS" variable)?

// Blockchain/Traceability Operations
top_blockchain_verify: "VERIFY" "BLOCKCHAIN" ("FOR" identifier)?
top_blockchain_block: "GET" "BLOCK" number ("FOR" identifier)?
top_blockchain_length: "GET" "BLOCKCHAIN" "LENGTH" ("FOR" identifier)?
top_blockchain_latest: "GET" "LATEST" "BLOCK" ("FOR" identifier)?
top_blockchain_audit: "GET" "AUDIT" "TRAIL" ("FOR" identifier)? ("WHERE" blockchain_audit_filter)?
top_blockchain_merkle: "GET" "MERKLE" "ROOT" ("FOR" identifier)?
top_blockchain_blocks_range: "GET" "BLOCKS" "FROM" number "TO" number ("FOR" identifier)?
top_blockchain_verify_block: "VERIFY" "BLOCK" number ("FOR" identifier)?
blockchain_audit_filter: blockchain_filter_item ("AND" blockchain_filter_item)*
blockchain_filter_item: ("entity_id" "=" (string | identifier)) 
                       | ("entity_type" "=" (string | identifier))
                       | ("operation_type" "=" (string | identifier))
                       | ("start_time" "=" (string | number))
                       | ("end_time" "=" (string | number))

// Temporal diff operations
top_diff_node: "DIFF" "NODE" (node_spec | identifier ("id" "=" string)?) "AT" string "VS" string
top_diff_edges: "DIFF" "EDGES" identifier "FOR" diff_edge_spec "AT" string "VS" string
diff_edge_spec: "source" "=" string | "target" "=" string | "source" "=" string "AND" "target" "=" string

// Top-level versions (aliases for standalone use)
top_create_graph: "CREATE" "GRAPH" identifier
top_create_graph_as: "CREATE" "GRAPH" identifier "AS" graph_pipeline
top_create_graph_with_structure: "CREATE" "GRAPH" identifier ("THEN" create_graph_statement)+
create_graph_statement: "CREATE" "NODE" node_spec ("UNIQUE" "KEY" "(" identifier_list ")")?
                    | "CREATE" "EDGE" identifier edge_spec_with_endpoints ("UNIQUE" "KEY" "(" identifier_list ")")?
                    | "CREATE" "EDGE" edge_spec_with_endpoints ("UNIQUE" "KEY" "(" identifier_list ")")?
top_create_graph_from_schema: "CREATE" "GRAPH" identifier "FROM" "SCHEMA" string
top_create_node: "CREATE" "NODE" node_spec ("UNIQUE" "KEY" "(" identifier_list ")")?
top_create_edge: "CREATE" "EDGE" identifier edge_spec_with_endpoints ("UNIQUE" "KEY" "(" identifier_list ")")?
                | "CREATE" "EDGE" edge_spec_with_endpoints ("UNIQUE" "KEY" "(" identifier_list ")")?
                | "CREATE" "EDGE" identifier "->" identifier ("{" property_list? "}")?
                | "CREATE" "EDGE" identifier "->" identifier "->" identifier ("{" property_list? "}")?
top_drop_graph: "DROP" "GRAPH" identifier ("CASCADE")?

// Basic types defined early to avoid forward references
identifier: IDENTIFIER
string: STRING
number: NUMBER
boolean: "TRUE" | "FALSE"
list: "[" (value ("," value)*)? "]"

// Variable declarations (standalone and within pipelines)
variable_decl: "LET" variable "=" value ("STORE" "RESULT" ("IN" variable)?)?
variable: "$" IDENTIFIER  
value: string | number | boolean | identifier | list | variable | subquery | search_query_basic | search_query_collections | graph_search_query | graph_search_from_via_to | rag_generate | rag_query_one_shot | embed_function | semantic_hash_function | now_function | top_traverse
embed_function: "EMBED" (string | variable) ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")?
semantic_hash_function: "SEMANTIC_HASH" (string | variable) ("USING" "MODEL" (string | variable))?
now_function: "NOW" "(" ")"

// Multimodal Pipeline Grammar
multimodal_pipeline: create_multimodal_pipeline | run_pipeline_cmd

// Retrieval Pipeline Grammar
retrieval_pipeline: create_retrieval_pipeline | run_retrieval_query | run_pipeline_with_input

// Search Grammar (DENSE, SPARSE, HYBRID, SEMANTIC, GRAPH)
search_type: "DENSE" | "SPARSE" | "HYBRID" | "SEMANTIC" | "GRAPH"
search_query_basic: ("DENSE" | "SPARSE" | "HYBRID" | "SEMANTIC" | "GRAPH") "SEARCH" (string | variable) "IN" identifier ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("LIMIT" (number | variable))?
search_query_with_weights: search_type SEARCH (string | variable) "IN" identifier "WITH" weight_list (LIMIT number)?
search_query_multimodal: search_type SEARCH (string | variable) "IN" "(" identifier_list ")" ("WITH" weight_list)? (LIMIT number)?
search_query_namespace: search_type SEARCH (string | variable) "IN" "NAMESPACE" identifier ("USING" "COLLECTION" identifier)? ("MODE" mode_type)? (LIMIT number)?
// SEMANTIC SEARCH with IN COLLECTIONS
search_query_collections: "SEMANTIC" "SEARCH" (string | variable) "IN" "COLLECTIONS" ("[" identifier_list "]" | "(" identifier_list ")") ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("LIMIT" (number | variable))?
// GRAPH SEARCH with VIA and DEPTH
graph_search_query: "GRAPH" SEARCH (string | variable) "VIA" "(" identifier_list ")" ("DEPTH" number)?
// GRAPH SEARCH with FROM/VIA/TO
graph_search_from_via_to: "GRAPH" "SEARCH" ("FROM" identifier ("WHERE" condition)?)? ("VIA" "(" identifier_list ")")? ("TO" identifier)? ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("LIMIT" (number | variable))?

// Weight list for search (semantic=0.6, keyword=0.4, graph=0.1, etc.)
weight_list: weight_item ("," weight_item)*
weight_item: identifier "=" number

// Hybrid Search Grammar
hybrid_search: hybrid_search_query | hybrid_search_simple | hybrid_search_with_weights | hybrid_search_with_profile | search_query_basic | search_query_with_weights | search_query_multimodal | search_query_namespace

// Basic hybrid search query (full syntax with NAMESPACE)
hybrid_search_query: HYBRID SEARCH string IN NAMESPACE identifier (USING COLLECTION identifier)? (MODE mode_type)? (LIMIT number)?

// Simple hybrid search query (simplified syntax: IN node_type)
hybrid_search_simple: HYBRID SEARCH string "IN" identifier (WITH weight_list)? (USING MODEL string)? (LIMIT number)?

// SEARCH Query Grammar (ported from QGQL)
// Support both: SEARCH 'text' IN NODES AND SEARCH NODE Person 'text'
search_query: "SEARCH" string "IN" ("NODES" | "EDGES") ("USING" "STRATEGY" search_strategy)? ("WHERE" search_condition)? ("LIMIT" number)?
            | "SEARCH" "NODE" identifier string ("WHERE" search_condition)? ("LIMIT" number)?
            | "SEARCH" "EDGE" string ("WHERE" search_condition)? ("LIMIT" number)?
search_strategy: "hybrid" | "similarity" | "keyword" | "sparse"
search_condition: identifier search_operator value
search_operator: "=" | "!=" | ">" | "<" | ">=" | "<=" | "CONTAINS" | "STARTS" "WITH" | "ENDS" "WITH" | "IN" | "NOT" "IN"

// AGGREGATE Query Grammar (ported from QGQL)
aggregate_query: "AGGREGATE" aggregate_function "(" (qualified_identifier | "*") ")" ("FROM" node_spec)? ("WHERE" condition)?
aggregate_function: "COUNT" | "SUM" | "AVG" | "MIN" | "MAX"

// Hybrid search with custom weights
hybrid_search_with_weights: HYBRID SEARCH string IN NAMESPACE identifier USING COLLECTION identifier WITH WEIGHTS weight_configuration (LIMIT number)?

// Hybrid search with predefined profile
hybrid_search_with_profile: HYBRID SEARCH string IN NAMESPACE identifier USING COLLECTION identifier WITH "PROFILE" identifier (LIMIT number)?

// Weight configuration
weight_configuration: "(" DENSE "=" number "," SPARSE "=" number "," GRAPH "=" number ")"

// Mode types
mode_type: "AUTO" | "DYNAMIC" | "STATIC" | "DEFAULT" | "DECLARATIVE" | "INTERACTIVE" | "PIPELINE"

// SET MODE statement for execution context
set_mode_statement: "SET" "MODE" mode_type

// CREATE PIPELINE statement (for retrieval pipelines)
create_retrieval_pipeline: "CREATE" "PIPELINE" identifier "IN" "NAMESPACE" identifier collection_spec "DESCRIPTION" string ("CHECKPOINT" "POLICY" ("enabled" | "disabled"))? "STAGES" "=" "[" retrieval_stage_list "]"
collection_spec: "SOURCE" "COLLECTION" identifier | "USING" "COLLECTION" identifier

// RUN PIPELINE statement (with query injection)
run_retrieval_query: RUN QUERY string USING PIPELINE identifier ("AS" "RUN" string)?
run_pipeline_with_input: "RUN" "PIPELINE" identifier "WITH" "INPUT" value ("AS" "RUN" string)?

// Retrieval stage list
retrieval_stage_list: retrieval_stage ("," retrieval_stage)*

// Individual retrieval stages
retrieval_stage: retrieval_function_stage | retrieval_step_stage

// Function-based retrieval stage with STEP syntax
retrieval_function_stage: "STEP" identifier ("DESCRIPTION" string)? retrieval_function_call

// Retrieval step stage (new syntax)
retrieval_step_stage: "STEP" identifier ("DESCRIPTION" string)? (retrieve_stage | context_stage | reason_stage | evaluate_stage)

// Retrieve stage
retrieve_stage: "RETRIEVE" "USING" ("PROFILE" | "FUNCTION") identifier retrieve_options*
retrieve_options: ("STRATEGY" identifier) | ("INCLUDE" retrieval_types) | ("TEMPORAL" "POLICY" identifier)

// Reason stage
reason_stage: "REASON" "USING" ("MODEL" | "FUNCTION") identifier reason_options*
reason_options: ("PROMPT" string) | ("VERIFY" "USING" identifier) | ("PARAMETERS" parameters_block)

// Evaluate stage
evaluate_stage: "EVALUATE" "USING" "SUITE" identifier evaluate_options*
evaluate_options: ("METRICS" "[" metric_list "]") | ("STORE" "RESULTS" "IN" "COLLECTION" identifier) | ("PARAMETERS" parameters_block)

// Context stage
context_stage: "CONTEXT" "USING" identifier context_options*
context_options: ("MODE" identifier) | ("WINDOW_SIZE" number) | ("DEDUPLICATE" boolean) | ("MERGE" "NODES" "(" node_type_list ")") | ("GROUP_BY" "ENTITY") | ("MULTIMODAL" boolean)

// Retrieval function call
retrieval_function_call: "USING" "FUNCTION" retrieval_function_name ("PARAMETERS" parameters_block)? (input_output_clauses)?
retrieval_function_name: identifier ("." identifier)*
input_output_clauses: ("INPUT" "FROM" collection_name)? ("OUTPUT" "TO" variable_name | "OUTPUT" "TO" "COLLECTION" identifier)
collection_name: identifier
variable_name: "$" identifier
parameters_block: "(" (retrieval_parameter ("," retrieval_parameter)*)? ")"
retrieval_parameter: identifier ("=" | ":") value

// Legacy retrieval stages (kept for backward compatibility, commented out as we use new syntax above)
// context_stage: CONTEXT USING identifier (WINDOW_SIZE number)? (MODE identifier)? (DEDUPLICATE boolean)? (MERGE NODES "(" node_type_list ")")?
// reason_stage: REASON USING MODEL identifier (INSTRUCTION string)? (PROMPT string)? (VERIFY USING identifier)?
verify_stage: "VERIFY" "USING" identifier
// evaluate_stage: EVALUATE USING SUITE identifier (METRICS "[" metric_list "]")? (STORE RESULTS IN COLLECTION identifier)?

// Supporting grammar elements
retrieval_types: retrieval_type ("," retrieval_type)*
retrieval_type: "ENTITY" | "RELATIONSHIP" | "TABLE" | "IMAGE" | "CHUNK" | "DOCUMENT"

node_type_list: identifier ("," identifier)*
metric_list: string ("," string)*

// CREATE PIPELINE statement (simplified name - works for all pipeline types)
create_pipeline: "CREATE" "PIPELINE" identifier ("IN" "NAMESPACE" (identifier | variable))? ("SOURCE" "COLLECTION" identifier)? ("TARGET" "COLLECTION" identifier)? ("DESCRIPTION" string)? ("EXECUTION_MODE" identifier)? ("PARAMETERS" "(" parameter_dict ")")? ("STAGES" "=" "[" enhanced_stage_list "]")?
                 | "CREATE" "PIPELINE" identifier "IN" "NAMESPACE" identifier "SOURCE" "COLLECTION" identifier "TARGET" "COLLECTION" identifier "DESCRIPTION" string ("EXECUTION_MODE" identifier)? ("CHECKPOINT" "POLICY" ("enabled" | "disabled"))? "STAGES" "=" "[" enhanced_stage_list "]"
// Legacy alias for backward compatibility
create_multimodal_pipeline: create_pipeline

// RUN PIPELINE statement with enhanced options
run_pipeline_cmd: "RUN" "PIPELINE" identifier run_pipeline_options*
run_pipeline_options: "DRY_RUN" | "LAZY" | "STEP_BY_STEP"
                    | "FROM" "STEP" identifier
                    | "RESTART" "STEP" identifier
                    | "STEPS" "[" step_range "]"
                    | "OVERRIDE" "PARAMETERS" "(" parameter_dict ")"
                    | "PARAMETERS" "(" parameter_dict ")"
                    | "FROM" "CHECKPOINT" identifier
                    | "WITH" "PARAMETERS" parameters_block
                    | "WITH" "INTENT" string
                    | "AS" string
step_range: identifier ("->" identifier)?
step_list: identifier ("," identifier)*

// CLASSIFY FILE command — detect file type and structure
classify_file_cmd: "CLASSIFY" "FILE" (string | variable)

// Enhanced multimodal stage list
enhanced_stage_list: enhanced_stage ("," enhanced_stage)* (",")?

// Individual enhanced multimodal stages with STEP prefix
enhanced_stage: step_stage | standalone_stage

// STEP-prefixed stages
step_stage: "STEP" identifier ("DESCRIPTION" string)? stage_action

// Standalone stages (no STEP prefix)
standalone_stage: enhanced_connect_stage | enhanced_index_stage

// Stage action - enhanced stages with function support and pipeline references
stage_action: enhanced_extract_stage | enhanced_preprocess_stage | enhanced_chunk_stage
             | enhanced_entity_extract_stage | enhanced_relationship_extract_stage
             | enhanced_embed_stage | enhanced_connect_stage | enhanced_index_stage | function_stage
             | pipeline_reference_stage | extract_from_folder_stage | chunk_by_semantic_stage
             | embed_into_stage | hybrid_search_stage | traverse_stage_pipeline | generate_stage
             | evaluate_rag_stage | insert_into_stage

// EXTRACT FROM FOLDER stage
extract_from_folder_stage: "EXTRACT" "FROM" "FOLDER" string ("DETECT" "(" detect_types ")")? ("STORE" "AS" "NODE" "TYPES" "(" node_types ")")?

// CHUNK BY semantic stage
chunk_by_semantic_stage: "CHUNK" "BY" chunk_method ("PARAMETERS" "(" parameter_dict ")")? ("FROM" identifier)? ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?

// EMBED INTO stage
embed_into_stage: "EMBED" ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? "INTO" identifier

// HYBRID SEARCH stage with STORE RESULT AS
hybrid_search_stage: "HYBRID" "SEARCH" (string | variable) "IN" identifier ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" "AS" identifier)?

// TRAVERSE stage with STORE RESULT AS (for pipeline context)
traverse_stage_pipeline: "TRAVERSE" "FROM" (identifier | variable) ("VIA" "(" identifier_list ")")? ("TO" identifier)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" "AS" identifier)?

// GENERATE stage with PROMPT, USER_QUERY, CONTEXT FROM, GUARDRAILS
generate_stage: "GENERATE" ("USING" "MODEL" (string | variable))? ("PROMPT" string)? ("USER_QUERY" (string | variable))? ("CONTEXT" "FROM" context_sources)? ("PARAMETERS" "(" parameter_dict ")")? ("GUARDRAILS" "{" parameter_dict "}")? ("STORE" "RESULT" "AS" identifier)?
context_sources: (identifier | variable) ("," (identifier | variable))*

// EVALUATE RAG stage with STORE RESULT AS
evaluate_rag_stage: "EVAL" "RAG" "ON" (identifier | variable) ("USING" "MODEL" (string | variable) | parameter_dict)? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" "AS" identifier)?

// INSERT INTO stage
insert_into_stage: "INSERT" "INTO" identifier "VALUES" "{" property_list "}"

// Pipeline reference stage - allows pipelines to reference other pipelines as stages
pipeline_reference_stage: "RUN" "PIPELINE" identifier (pipeline_ref_option)*
pipeline_ref_option: "WITH" "PARAMETERS" "(" parameter_dict ")" | "FROM" "CHECKPOINT" identifier | "AS" string

function_stage_input: "FROM" "FILE" string ("FORMAT" string)?

// Function-based stage (new cleaner syntax) with optional storage/linking clauses
function_stage: function_stage_input? "USING" "FUNCTION" (identifier | FUNCTION_NAME) ("PARAMETERS" "(" parameter_dict ")")? function_stage_output*
function_stage_output: ("STORE" "AS" ("NODE" "TYPE" identifier | "NODE" "TYPES" "(" node_types ")" | "EDGE" "TYPE" identifier)
    | "LINK" ("(" link_specs ")" | "TO" identifier "ON" identifier | "USING" identifier)
    | "OUTPUT" "TO" "COLLECTION" identifier
    | "STORE" "INTERMEDIATE" "IN" ("COLLECTION" identifier | "MEMORY")
    | "NORMALIZE" boolean
    | "CONTEXT_POLICY" string
    | "STORE" "EMBEDDING" "IN" identifier "." identifier
    | "BUILD_INDEX" string
    | "STORE" "IN" "COLLECTION" identifier)

// Parameter dictionary (key-value pairs)
parameter_dict: parameter_pair ("," parameter_pair)* | "{" parameter_pair ("," parameter_pair)* "}"
parameter_pair: (identifier | string) (":" | "=") parameter_value
parameter_value: string | number | boolean | list | identifier | variable | qualified_variable | "[" (string ("," string)*) "]" | object
object: "{" parameter_pair ("," parameter_pair)* "}"
// Qualified variable for parameter references like $params.dense_weight
qualified_variable: variable ("." identifier)+

// Detect types for multimodal extraction
detect_types: detect_type ("," detect_type)*
detect_type: IDENTIFIER  // TEXT, TABLES, IMAGES, AUDIO, VIDEO are IDENTIFIER tokens

// Node types for storage  
node_types: IDENTIFIER ("," IDENTIFIER)*

// Link specifications
link_specs: link_item ("," link_item)*
link_item: IDENTIFIER "TO" IDENTIFIER

// Unified USING clause - supports built-in, LLM, or custom functions
using_clause: "USING" using_option
using_option: "READER" string           // Built-in reader (e.g., "pdfplumber")
    | "MODEL" string                    // Built-in model
    | "PROFILE" (string | identifier)   // Built-in profile (e.g., embedding profile) - can be string or identifier
    | "LLM" string                      // LLM model (e.g., "gpt-4", "claude-3-opus")
    | "FUNCTION" function_path          // Custom Python function
    | string                            // Legacy: just a string (backward compatibility)

// Function path (supports module.path or file.py:function)
function_path: (identifier ("." identifier)*) | (string ":" identifier)

// Parameter clause (consistent across all stages)
parameter_clause: "PARAMETERS" "(" parameter_dict ")"

// Storage clauses (consistent across all stages)
storage_clauses: storage_clause*
storage_clause: "STORE" "AS" ("NODE" "TYPE" identifier | "NODE" "TYPES" "(" node_types ")")
    | "STORE" "AS" "EDGE" "TYPE" identifier
    | "LINK" ("(" link_specs ")" | "TO" identifier "ON" identifier | "USING" identifier)
    | "OUTPUT" "TO" "COLLECTION" identifier
    | "STORE" "INTERMEDIATE" "IN" ("COLLECTION" identifier | "MEMORY")
    | "STORE" "EMBEDDING" "IN" identifier "." identifier
    | "BUILD_INDEX" string
    | "NORMALIZE" boolean
    | "CONTEXT_POLICY" string
    | "STORE_NORMALIZED" boolean              // Store normalized JSON output (Stage-1)
    | "OUTPUT_DIR" string                     // Base directory for extraction output

// Enhanced Stage Definitions - Unified Syntax
// EXTRACT stage - unified syntax for ingestion (Stage-1: Extraction & Normalization)
// Supports: extraction modes (FULL, RANGE, LIST), page selection, reader selection, detect modalities
enhanced_extract_stage: "EXTRACT" extract_source? extract_mode? using_clause? detect_clause? parse_metadata_clause? parameter_clause? ("BY" "TOPICS" topic_config)? storage_clauses*
extract_source: "FROM" "FILE" string ("FORMAT" string)?
extract_mode: "MODE" extraction_mode | "PAGES" page_spec
extraction_mode: "FULL" | "RANGE" | "LIST"
page_spec: page_range | page_list
page_range: number ".." number | number "-" number
page_list: "[" number ("," number)* "]"
detect_clause: "DETECT" "(" detect_types ")"
parse_metadata_clause: "PARSE_METADATA" boolean
topic_config: ("MAX_TOPICS" number)? ("MIN_TOPIC_SIZE" number)? ("TOPIC_PROMPT" string)?

// PREPROCESS stage - unified syntax
enhanced_preprocess_stage: "PREPROCESS" using_clause? parameter_clause? storage_clauses*

// Preprocessing functions
preprocessing_functions: preprocessing_function ("PLUS" preprocessing_function)*
preprocessing_function: identifier ("." identifier)?

// CHUNK stage - unified syntax
enhanced_chunk_stage: "CHUNK" (("BY" chunk_method) | using_clause | parameter_clause | storage_clauses)*

// CHUNK stage (legacy)
chunk_stage_multimodal: "CHUNK" "BY" chunk_method "USING" "MODEL" identifier

// Chunking methods
chunk_method: "semantic" | "layout" | "semantic_plus_layout" | ("semantic" "+" "layout") | "fixed" | "sentence" | "paragraph" | "recursive" | "sliding_window" | "hierarchical" | "parent_child" | "table_aware" | "smart" | "token_based" | "semantic_similarity" | "topic_aware" | "qa_aware" | "code_aware" | "image_aware" | "adaptive" | "entity_aware" | "multilang" | "citation_aware" | "dialogue_aware" | "formula_aware" | "list_aware" | "cross_page"

// DETECT TABLES stage
detect_tables_stage: "DETECT" "TABLES" "USING" "MODEL" string ("EXTRACT" extract_options)? ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?

// DETECT IMAGES stage
detect_images_stage: "DETECT" "IMAGES" "USING" "MODEL" string ("OCR" "USING" string)? ("DESCRIBE" "USING" string)? ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?

// DETECT AUDIO stage
detect_audio_stage: "DETECT" "AUDIO" "USING" "MODEL" string ("EXTRACT" "METADATA" "(" audio_metadata_list ")")? ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?

// Audio metadata list
audio_metadata_list: audio_metadata_item ("," audio_metadata_item)*
audio_metadata_item: "duration" | "speaker" | "transcript"

// OCR stage
ocr_stage: "OCR" "USING" string

// CAPTION stage
caption_stage: "DESCRIBE" "USING" string

// ENTITY EXTRACT stage (enhanced)
enhanced_entity_extract_stage: "EXTRACT" "ENTITIES" (("FROM" "CHUNKS" "OF" "SOURCE") | ("USING" "LLM" string) | ("PASSES" "(" identifier_list ")") | ("PROMPT" string) | ("STORE" "AS" "NODE" "TYPE" identifier) | ("LINK" "TO" identifier "ON" identifier) | ("OUTPUT" "TO" "COLLECTION" identifier) | ("STORE" "INTERMEDIATE" "IN" ("COLLECTION" identifier | "MEMORY")))*

// ENTITY EXTRACT stage (legacy)
entity_extract_stage: "ENTITY_EXTRACT" "USING" "MODEL" string ("STORE" "AS" "NODE" "TYPE" identifier)? ("LINK" "TO" identifier "ON" identifier)?

// RELATIONSHIP EXTRACT stage - unified syntax
enhanced_relationship_extract_stage: "EXTRACT" "RELATIONSHIPS" (("FROM" "CHUNKS" "OF" "SOURCE") | using_clause | parameter_clause | storage_clauses)*

// RELATE stage
relate_stage: "RELATE" "USING" "MODEL" string ("LINK" "TYPES" "=" "[" link_types_list "]")? ("CONFIDENCE_THRESHOLD" "=" number)?

// Link types list
link_types_list: string ("," string)*

// EMBED stage (enhanced)
enhanced_embed_stage: "EMBED" (("USING" "PROFILE" identifier) | ("PARAMETERS" "(" parameter_list ")") | ("CONTEXT_POLICY" string) | ("PROMPT" string) | ("STORE" "EMBEDDING" "IN" identifier "." identifier) | ("STORE" "INTERMEDIATE" "IN" ("COLLECTION" identifier | "MEMORY")))*

// EMBED stage (legacy)
embed_stage: "EMBED" "USING" "PROFILE" identifier

// CONNECT stage (enhanced)
enhanced_connect_stage: "CONNECT" "AUTO" (("(" identifier ">" number "," identifier ">" number ")") | ("INCLUDE" "EDGES" "[" string_list "]") | ("CONFIDENCE_THRESHOLD" number) | ("TEMPORAL_POLICY" identifier) | ("STORE" "INTERMEDIATE" "IN" ("COLLECTION" identifier | "MEMORY")))*
                        | "CONNECT" "FROM" "NORMALIZED" (connect_from_normalized_option)* storage_clauses*

// CONNECT FROM NORMALIZED option (can appear multiple times)
connect_from_normalized_option: "CREATE" "NODES" "(" node_types ")"
                              | "LINK" "(" link_specs ")"
                              | "LINK" link_specs

// CONNECT stage (legacy)
connect_stage: "CONNECT" "AUTO" ("(" connect_params ")")?

// Connect parameters
connect_params: connect_param ("," connect_param)*
connect_param: identifier ">" number

// INDEX stage (enhanced)
enhanced_index_stage: "INDEX" (("USING" "{" index_options "}") | ("BUILD_INDEX" string) | ("STORE" "IN" "COLLECTION" identifier) | ("PROMPT" string))*

// INDEX stage (legacy)
index_stage_multimodal: "INDEX" "USING" "{" index_options "}"

// Index options
index_options: index_option ("," index_option)*
index_option: identifier ":" (boolean | string)

// Extract options
extract_options: extract_option ("," extract_option)*
extract_option: "CELLS" | "HEADERS" | "CAPTIONS"

// Pipeline parameters
pipeline_params: "{" pipeline_param ("," pipeline_param)* "}"
pipeline_param: identifier ":" value

// Pipeline with THEN sequencing and PARALLEL blocks
// IMPORTANT: pipeline_without_seed removed - standalone queries shouldn't match pipeline
// Only explicit PIPELINE SEED {...} syntax matches pipeline
pipeline: pipeline_with_seed
pipeline_with_seed: "PIPELINE" "SEED" string "{" pipeline_body "}"
pipeline_body: pipeline_item ("THEN" pipeline_item | pipeline_item)*
pipeline_item: variable_decl | stage | parallel_stage
?stage: create_stage | drop_stage | use_stage | load_stage | chunk_stage | extract_stage | generate_stage_legacy 
     | embedding_stage | retrieval_stage | traverse_stage | analytics_stage 
     | where_stage | select_stage | index_stage | shard_stage | schema_stage
     | model_stage | prompt_stage | namespace_stage | grant_stage 
     | define_node_stage | define_edge_stage | view_stage | materialized_view_stage 
     | temporal_stage | temporal_query | stream_stage | udf_stage | transaction_stage | explain_stage
     | delete_stage | update_stage | generation_stage | agent_stage | governance_stage
     | eval_stage | web_stage | rag_pipeline_stage | pipeline_management_stage
     | file_reader_stage | multimodal_extraction_stage | smart_chunking_stage | evaluation_stage
     | retrieval_system_stage | return_stage | match_entity_stage

// Parallel execution stage
parallel_stage: "PARALLEL" stage | "PARALLEL" "(" stage ("," stage)* ")"

// Graph creation and mutation
create_graph: "CREATE" "GRAPH" identifier
create_graph_as: "CREATE" "GRAPH" identifier "AS" graph_pipeline
create_stage: create_graph | create_graph_as | create_node | create_edge

// Graph deletion
drop_stage: drop_graph
drop_graph: "DROP" "GRAPH" identifier ("CASCADE")?

// Graph selection
use_stage: "USE" "GRAPH" identifier
create_node: "CREATE" "NODE" node_spec ("UNIQUE" "KEY" "(" identifier_list ")")?

create_edge: "CREATE" "EDGE" identifier edge_spec_with_endpoints ("UNIQUE" "KEY" "(" identifier_list ")")?
            | "CREATE" "EDGE" edge_spec_with_endpoints ("UNIQUE" "KEY" "(" identifier_list ")")?
            | "CREATE" "EDGE" identifier "->" identifier ("{" property_list? "}")?
            | "CREATE" "EDGE" identifier "->" identifier "->" identifier ("{" property_list? "}")?
edge_spec_with_endpoints: "(" node_ref ")" edge_spec "(" node_ref ")"
                        | "(" node_ref ")" "->" "(" node_ref ")" ("{" property_list? "}")?
                        | edge_spec
                        | edge_from_to
edge_from_to: identifier ("AS" identifier)? ("{" property_list? "}")? "SRC" node_selector "DEST" node_selector
                        | identifier ("AS" identifier)? "SRC" node_selector "DEST" node_selector ("{" property_list? "}")?
                        | identifier "CONNECT" node_selector "DEST" node_selector ("{" property_list? "}")?
                        | ("{" property_list? "}")? "SRC" node_selector "DEST" node_selector
                        | "SRC" node_selector "DEST" node_selector ("{" property_list? "}")?
                        | identifier ("AS" identifier)? ("{" property_list? "}")? "FROM" node_selector "TO" node_selector
                        | identifier ("AS" identifier)? "FROM" node_selector "TO" node_selector ("{" property_list? "}")?
                        | ("{" property_list? "}")? "FROM" node_selector "TO" node_selector
                        | "FROM" node_selector "TO" node_selector ("{" property_list? "}")?

// Node selector for CREATE EDGE - supports UUID strings or node type with WHERE clause  
node_selector: string 
             | identifier "WHERE" condition
             | identifier

// Graph deletion and updates
delete_stage: delete_node | delete_edge
delete_node: "DELETE" "ALL" "NODES" ("WHERE" condition)? | "DELETE" "NODE" node_spec ("WHERE" condition)?
delete_edge: "DELETE" "EDGE" identifier ("WHERE" condition)? | "DELETE" "EDGE" identifier "SRC" node_selector "DEST" node_selector

update_stage: update_node | update_edge
update_node: "UPDATE" "NODE" node_spec "SET" "{" update_assignment_list "}" ("WHERE" condition)?
update_edge: "UPDATE" "EDGE" identifier ("AS" identifier)? "SET" "{" update_assignment_list "}" ("WHERE" condition)?

// Top-level UPDATE (standalone version)
// Support both: UPDATE NODE Person SET {...} WHERE ... AND UPDATE NODE Person WHERE ... SET {...}
top_update_node: "UPDATE" "NODE" node_spec ("WHERE" condition)? "SET" "{" update_assignment_list "}"
                | "UPDATE" "NODE" node_spec "SET" "{" update_assignment_list "}" ("WHERE" condition)?
top_update_edge: "UPDATE" "EDGE" identifier ("AS" identifier)? ("SET" "{" update_assignment_list "}" ("WHERE" condition)? | ("WHERE" condition)? "SET" "{" update_assignment_list "}")
top_delete_edge: "DELETE" "EDGE" identifier "SRC" node_selector "DEST" node_selector
                | "DELETE" "EDGE" edge_spec ("WHERE" condition)?
                | "DELETE" "EDGE" ("WHERE" condition)?

// Property lists for CREATE (use colon)
node_spec: identifier ("AS" identifier)? ("{" property_list? "}")?
edge_spec: identifier ("AS" identifier)? ("{" property_list? "}")?
property_list: property ("," property)*
property: identifier ":" value

// Assignment lists for UPDATE (use equals or colon)
update_assignment_list: update_assignment ("," update_assignment)*
update_assignment: identifier ("=" | ":") value

// Data ingestion and bulk loading
load_stage: load_api_source | load_document | load_folder | load_csv | load_excel | load_json | load_xml | load_sql
load_document: "LOAD" "DOCUMENT" (string | variable) ("INTO" identifier)? ("WITH" "OPTIONS" "{" load_option_list "}" | "WITH" load_options)?
load_folder: "LOAD" "FOLDER" (string | variable) ("INTO" identifier)? ("WITH" "OPTIONS" "{" load_option_list "}" | "WITH" load_options)? ("FILTER" string)?
load_csv: "LOAD" "CSV" (string | variable) ("INTO" identifier)? ("HEADER" boolean | "DELIMITER" string | "WITH" "OPTIONS" "{" load_option_list "}" | "WITH" csv_options)?
load_excel: "LOAD" "EXCEL" (string | variable) ("INTO" identifier)? ("USING" "PIPELINE" identifier)? ("WITH" "OPTIONS" "{" load_option_list "}" | "WITH" load_options)?
load_json: "LOAD" "JSON" (string | variable) ("INTO" identifier)? ("PATH" string | "WITH" "OPTIONS" "{" load_option_list "}" | "WITH" json_options)?
load_xml: "LOAD" "XML" (string | variable) ("INTO" identifier)? ("XPATH" string | "WITH" "OPTIONS" "{" load_option_list "}" | "WITH" xml_options)?
load_sql: "LOAD" "SQL" (string | variable) ("FROM" identifier)? ("INTO" identifier)? ("QUERY" string | "TABLE" string | "WITH" sql_options)?
load_api_source: "LOAD" "API" (string | variable) ("INTO" identifier)? ("METHOD" string | "HEADERS" "{" property_list "}" | "PARAMS" "{" property_list "}")?

// Load option lists
load_option_list: load_option ("," load_option)*

// Bulk loading options
load_options: load_option ("," load_option)*
load_option: identifier "=" value

csv_options: csv_option ("," csv_option)*
csv_option: "DELIMITER" "=" string | "HEADER" "=" boolean | "ENCODING" "=" string | "SKIP_ROWS" "=" number

json_options: json_option ("," json_option)*
json_option: "PATH" "=" string | "ARRAY_MODE" "=" boolean | "FLATTEN" "=" boolean

xml_options: xml_option ("," xml_option)*
xml_option: "ROOT_PATH" "=" string | "NAMESPACE" "=" string | "VALIDATE" "=" boolean

sql_options: sql_option ("," sql_option)*
sql_option: "CONNECTION" "=" string | "QUERY" "=" string | "BATCH_SIZE" "=" number

api_options: api_option ("," api_option)*
api_option: "METHOD" "=" string | "HEADERS" "=" string | "AUTH" "=" string | "PAGINATION" "=" boolean

// Chunking and model operations
chunk_stage: "CHUNK" "BY" "USING" chunk_strategy
chunk_strategy: identifier ("{" parameter_list "}")?
extract_stage: "EXTRACT" "USING" model_spec
generate_stage_legacy: "GENERATE" "WITH" "MODEL" identifier "PROMPT" string | "GENERATE" "BY" "USING" model_spec
embedding_stage: "ADD EMBEDDINGS" "MODEL" identifier "TARGET" identifier | "ADD EMBEDDINGS" "USING" model_spec ("INTO" identifier)?

model_spec: identifier ("{" parameter_list "}")?
parameter_list: parameter ("," parameter)*
parameter: identifier ("=" | ":") value

// Retrieval operators
// retrieval_stage: advanced_retrieve | sparse_match | dense_match | hybrid_match | rerank_stage | retrieve_query | rag_query

// Advanced RAG retrieval syntax
advanced_retrieve: "RETRIEVE" ("TOP" number)? "FROM" identifier ("USING" hybrid_spec)? (where_stage)? ("ORDER" "BY" order_by_clause)? ("AUGMENT" augment_spec)? ("GROUND" ground_spec)? ("RETURN" return_spec)?

// RAG QUERY syntax (alias for RETRIEVE)
rag_query: "RAG" "QUERY" string ("IN" identifier)? ("USING" hybrid_spec)? ("LIMIT" number)?
// Simple RAG syntax for natural language questions
simple_rag_query: "RAG" string ("AT" "NAMESPACE" identifier)?

// RAG GENERATE with CONTEXT FROM
rag_generate: "RAG" "GENERATE" (string | variable) ("USING" "MODEL" (string | variable))? ("CONTEXT" "FROM" variable)? ("PARAMETERS" "(" parameter_dict ")")? ("GUARDRAILS" "{" parameter_dict "}")? ("STORE" "RESULT" ("IN" variable)?)?

// RAG QUERY one-shot
rag_query_one_shot: "RAG" "QUERY" (string | variable) "IN" identifier ("USING" "{" parameter_dict "}")? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "RESULT" "IN" variable)?

// EVALUATE RAG
evaluate_rag: "EVAL" "RAG" "ON" (variable | identifier) ("USING" ("MODEL" (string | variable) | parameter_dict))? ("PARAMETERS" "(" parameter_dict ")")? ("STORE" "IN" "COLLECTION" identifier)?

// Unified AIQL Query Definitions (Placeholder - will be fully implemented after grammar is tested)
unified_aiql_query: simple_rag_query

// Hybrid retrieval parameters
hybrid_spec: "HYBRID" "(" hybrid_params ")"
hybrid_params: hybrid_param ("," hybrid_param)*
hybrid_param: identifier "=" number

// Augmentation (entity/claim/citation enrichment)
augment_spec: "WITH" augment_method "(" augment_params? ")"
augment_method: "ENTITIES" | "CLAIMS" | "CITATIONS"
augment_params: augment_param ("," augment_param)*
augment_param: identifier "=" (number | string)

// Grounding (convert to context window)
ground_spec: "INTO" identifier "(" ground_params? ")"
ground_params: ground_param ("," ground_param)*
ground_param: identifier "=" (number | string)

// Return specification
return_spec: identifier_list

// Legacy retrieval (maintained for backward compatibility)
retrieve_query: "RETRIEVE" identifier (where_stage)?
sparse_match: "SPARSE MATCH" "(" (string | variable) ")" ("IN" identifier | "AS" identifier)? ("LIMIT" number)?
dense_match: "DENSE MATCH" string ("MODEL" identifier)? ("IN" identifier | "AS" identifier)? ("LIMIT" number)? | "DENSE MATCH" "(" (string | variable) ("," "MODEL" "=" (string | identifier))? ")" ("IN" identifier | "AS" identifier)? ("LIMIT" number)?
hybrid_match: "HYBRID MATCH" "(" (string | variable) ("," "SPARSE_WEIGHT" "=" number)? ("," "DENSE_WEIGHT" "=" number)? ("," "DENSE_MODEL" "=" identifier)? ")" ("IN" identifier | "AS" identifier)? ("LIMIT" number)?
rerank_stage: "RERANK" "USING" identifier ("LIMIT" number)?

weights: weight_spec ("," weight_spec)*
weight_spec: identifier "=" number

// Generation stages
// LLM Generation and Prompt Management
generation_stage: generate_with_llm | generate_with_prompt_template | register_prompt_stmt | list_prompts_stmt

// Generate using LLM provider
generate_with_llm: "GENERATE" "USING" llm_provider llm_spec ("PROMPT" string)? ("SYSTEM" string)? ("WITH" llm_options)?

llm_provider: "OPENAI" | "ANTHROPIC" | "GOOGLE" | "OLLAMA" | identifier
llm_spec: "MODEL" identifier ("TEMPERATURE" number)? ("MAX_TOKENS" number)?
llm_options: llm_option ("," llm_option)*
llm_option: identifier "=" value

// Generate using named prompt template
generate_with_prompt_template: "GENERATE" "WITH" "PROMPT" identifier ("VARIABLES" prompt_variables)?
prompt_variables: "{" prompt_variable ("," prompt_variable)* "}"
prompt_variable: identifier ":" value

// Register new prompt template
register_prompt_stmt: "REGISTER" "PROMPT" identifier "TYPE" prompt_type "TEMPLATE" string "VARIABLES" "[" identifier_list "]"
prompt_type: "RAG" | "AGENT" | "EXTRACTION" | "SUMMARIZATION" | "CUSTOM"

// List available prompts
list_prompts_stmt: "LIST" "PROMPTS" ("TYPE" prompt_type)?

context_spec: context_assignment ("," context_assignment)*
context_assignment: identifier "=" (string | variable | identifier)

// Agent Orchestration
agent_stage: plan_agent_legacy | execute_agent_legacy | agent_task | agent_tool_use | call_agent
plan_agent_legacy: "PLAN" "AGENT" string ("INPUT" agent_input)? ("TOOLS" tool_list_legacy)? ("POLICY" string)?
agent_input: identifier | variable
tool_list_legacy: "[" string ("," string)* "]"
execute_agent_legacy: "EXECUTE" ("RETURN" return_list)?
return_list: identifier ("," identifier)*
agent_task: "CREATE" "TASK" string ("FOR" identifier)? ("WITH" task_params)?
agent_tool_use: "USE" "TOOL" identifier "WITH" tool_params
call_agent: "CALL" "AGENT" string "WITH" agent_params (upsert_clause)? (provenance_clause)?
agent_params: "{" agent_param ("," agent_param)* "}"
agent_param: string ":" (string | number | boolean | variable)
task_params: task_param ("," task_param)*
task_param: identifier "=" (string | number | boolean)
tool_params: tool_param ("," tool_param)*
tool_param: identifier "=" (string | number | boolean | list)

// Upsert and Provenance for agent operations
upsert_clause: "UPSERT" "INTO" "GRAPH" (upsert_options)?
upsert_options: identifier "=" (string | variable)
provenance_clause: "WITH" "PROVENANCE" "(" provenance_params ")"
provenance_params: provenance_param ("," provenance_param)*
provenance_param: identifier "=" (string | variable)

// Governance and Policy
governance_stage: set_policy | set_namespace | redact_clause | create_policy | audit_query
set_policy: "SET" "POLICY" identifier "=" string
set_namespace: "SET" "NAMESPACE" string
redact_clause: "WITH" "REDACT" "(" redact_params ")"
redact_params: redact_param ("," redact_param)*
redact_param: identifier ("," "mask" "=" string)?
create_policy: "CREATE" "POLICY" identifier "AS" policy_body
policy_body: "{" policy_rules "}"
policy_rules: policy_rule ("," policy_rule)*
policy_rule: "IF" condition "THEN" policy_action
policy_action: "ALLOW" | "DENY" | "REDACT" | "LOG"
audit_query: "SHOW" "AUDIT" "LOG" ("WHERE" condition)? ("LIMIT" number)?

// Web Integration - Direct web access for Agentic RAG
web_stage: web_fetch | web_search | web_api | web_hybrid_retrieve
web_fetch: "WEB" "(" (string | variable) ")" ("AS" identifier)? (web_options)?
web_search: "WEB SEARCH" (string | variable) ("OPTIONS" web_search_options)? ("AS" identifier)?
web_api: "WEB API" (string | variable) ("WITH" web_api_options)? ("AS" identifier)?
web_hybrid_retrieve: "RETRIEVE" retrieve_sources where_stage? ("GROUND" ground_spec)?

// Web search options
web_search_options: "{" web_search_option ("," web_search_option)* "}"
web_search_option: identifier ":" (string | number | boolean)

// Web API options
web_api_options: "{" web_api_option ("," web_api_option)* "}"
web_api_option: identifier ":" (string | number | boolean | web_headers | web_params)
web_headers: "HEADERS" "{" string ":" string ("," string ":" string)* "}"
web_params: "PARAMS" "{" string ":" (string | number | boolean) ("," string ":" (string | number | boolean))* "}"

// Web options
web_options: "WITH" "(" web_option_list ")"
web_option_list: web_option ("," web_option)*
web_option: identifier "=" (string | number | boolean)

// Retrieve from multiple sources (graph + web)
retrieve_sources: retrieve_source ("," retrieve_source)*
retrieve_source: "GRAPH" identifier | "WEB" "(" (string | variable) ")" | "WEB API" "(" (string | variable) ")"

// Evaluation and Metrics
eval_stage: "EVAL" ("USING" metric_spec)? ("COMPARE" comparison_spec)? ("SAVE" "AS" string)?
metric_spec: "METRICS" "(" metric_list ")"
// metric_list: metric ("," metric)*
metric: metric_at_k | metric_with_params | identifier
metric_at_k: identifier "@" number
metric_with_params: identifier "(" metric_params ")"
metric_params: metric_param ("," metric_param)*
metric_param: identifier "=" (number | string | boolean)
comparison_spec: "WITH" comparison_target
comparison_target: "RUN" string | "BASELINE" identifier

// Graph traversal
traverse_stage: "TRAVERSE" strategy? traverse_spec strategy_suffix? traversal_options? as_of_clause? where_stage? return_clause? ("ORDER" "BY" order_by_clause)?
return_clause: "RETURN" return_expression ("," return_expression)*
return_expression: return_function | qualified_identifier ("AS" identifier)? | variable | "*" | typed_node_ref ("AS" identifier)?
return_function: "PATH_LENGTH" | "TOTAL_WEIGHT" | "PATH_COST" | "HOP_COUNT"

// Simple RETURN stage for pipelines
return_stage: "RETURN" (variable | "*" | qualified_identifier)
strategy: DFS | BFS
strategy_suffix: "STRATEGY" (DFS | BFS)
traversal_options: traversal_option+
traversal_option: "MAX DEPTH" number | "MIN DEPTH" number | LIMIT number
traverse_spec: inline_traversal | verbose_traversal | recursive_traversal | simple_traversal | from_traversal

// Inline chain syntax: Node:a -EDGE-> Node:b -EDGE-> Node:c
inline_traversal: node_ref ("-" edge_ref "->" node_ref)+
node_ref: (identifier | typed_node_ref) ("AS" identifier)?
typed_node_ref: identifier ":" identifier
edge_ref: identifier ("AS" identifier)? | "CONTAINS" ("AS" identifier)? | "RELATIONSHIP" ("AS" identifier)? | "HAS_CHUNK" ("AS" identifier)? | "MENTIONS" ("AS" identifier)? | "SIMILAR" ("AS" identifier)? | "RELATED_TO" ("AS" identifier)?

// Verbose syntax: SRC Node AS a OUTGOING EDGE DEST Node AS b OR Node VIA EDGE Edge DEST Node
// Also support: SRC Node -EDGE-> Node (mixed format)
// Support both DEST and TO for compatibility
verbose_traversal: "SRC" node_ref direction edge_ref ("DEST" | "TO") node_ref | "SRC" node_ref direction edge_ref ("DEST" | "TO") node_ref chained_hops | "SRC" node_ref ("-" edge_ref "->" node_ref)+ | node_ref "VIA" "EDGE" traverse_edge_pattern ("DEST" | "TO") node_ref ("STRATEGY" (DFS | BFS))?
chained_hops: direction edge_ref "DEST" node_ref chained_hops | direction edge_ref "DEST" node_ref
direction: "OUTGOING" | "INCOMING"
traverse_edge_pattern: edge_ref | edge_wildcard | recursive_edge_pattern
edge_wildcard: "*"

// Recursive patterns: Node AS p VIA EDGE CITE+ TO Node AS p2
recursive_traversal: node_ref "VIA" "EDGE" recursive_edge_pattern "TO" node_ref
recursive_edge_pattern: (identifier | "*") quantifier
quantifier: "+" | "*" | "?" | range_quantifier
range_quantifier: "{" number "}" | "{" number "," number "}" | "{" number ",}" | "{," number "}"

// Simple traversal syntax: SRC Person WHERE condition DEPTH number
simple_traversal: "SRC" node_ref where_stage? ("DEPTH" number)?

// FROM traversal: FROM NodeType EDGE_TYPE -> NodeType
from_traversal: "FROM" identifier "TO" identifier where_stage? ("DEPTH" number)?
                | "FROM" identifier "TO" identifier ("DEPTH" number)? where_stage?
                | "FROM" identifier ("DEPTH" number)? where_stage?
                | "FROM" identifier ("MAX" "DEPTH" number)? where_stage?
                | "FROM" identifier edge_ref "->" identifier where_stage? ("DEPTH" number)?
                | "FROM" identifier where_stage edge_ref "->" identifier where_stage? ("DEPTH" number)?
                | "FROM" identifier edge_ref "->" identifier chained_from_traversal 
                | "FROM" identifier where_stage edge_ref "->" identifier chained_from_traversal
chained_from_traversal: edge_ref "->" identifier where_stage? | edge_ref "->" identifier chained_from_traversal

// Global strategy declaration
global_strategy: "STRATEGY" ("DFS" | "BFS")

// Filtering and projection
where_stage: "WHERE" condition
condition: or_condition
or_condition: and_condition ("OR" and_condition)*
and_condition: simple_condition ("AND" simple_condition)*
simple_condition: "(" condition ")" | "NOT" simple_condition | (qualified_identifier | aggregation_function) operator value | qualified_identifier BETWEEN value "AND" value | qualified_identifier "SIMILAR" "TO" value | aggregation_function operator value

// AT temporal clause for time-travel queries (using AT instead of AS OF to avoid ambiguity)
as_of_clause: "AT" as_of_spec
as_of_spec: "TIMESTAMP" (string | variable)
          | "COMMIT" (string | variable)
          | "RUN" (string | variable)

for_system_time: "FOR" "SYSTEM_TIME" system_time_spec
system_time_spec: "ALL"
                | "FROM" (string | variable) "TO" (string | variable)
                | "BETWEEN" (string | variable) "AND" (string | variable)
                | "CONTAINED" "IN" "(" (string | variable) "," (string | variable) ")"

EQ: "="
NE: "!="
LT: "<"
GT: ">"
LE: "<="
GE: ">="
IN: "IN"
CONTAINS: "CONTAINS"
STARTS: "STARTS"
ENDS: "ENDS"

operator: EQ | NE | LT | GT | LE | GE | IN | CONTAINS | "STARTS" "WITH" | "ENDS" "WITH" | STARTS | ENDS | SIMILAR | BETWEEN | "NOT" "IN"

// Unified SELECT stage that handles all query types (basic, aggregation, group by, having)
select_stage: "SELECT" projection_list ("FROM" from_clause)? (as_of_clause | for_system_time)? ("WHERE" where_clause)? ("GROUP" "BY" group_by_clause)? ("HAVING" having_clause)? ("ORDER" "BY" order_by_clause)? ("LIMIT" limit_clause)?

having_clause: having_condition ("AND" having_condition | "OR" having_condition)*
having_condition: (qualified_identifier | aggregation_function) operator value
projection_list: projection ("," projection)*
projection: "*" 
          | "NODE" qualified_identifier ("AS" identifier)?      // Explicit node selection
          | "EDGE" qualified_identifier ("AS" identifier)?       // Explicit edge selection  
          | "PROPERTY" qualified_identifier ("AS" identifier)?   // Explicit property (optional)
          | qualified_identifier ("AS" identifier)?              // Property (backward compatible)
          | aggregation_function ("AS" identifier)?              // Aggregation
from_clause: identifier ("AS" identifier)? | "(" identifier ("AS" identifier)? ")"
where_clause: condition
select_condition: qualified_identifier operator value

// Qualified identifier for property access (e.g., p.name, node.property)
qualified_identifier: identifier ("." identifier)? | typed_node_ref ("." identifier)?

// Aggregation functions with explicit type support
aggregation_function: COUNT "(" count_target ")" 
                    | SUM "(" agg_target ")"
                    | AVG "(" agg_target ")"
                    | MIN "(" agg_target ")"
                    | MAX "(" agg_target ")"
                    | DISTINCT "(" identifier ")"
                    | UNIQUE "(" identifier ")"
                    | CONCAT "(" identifier ")"
                    | JOIN "(" identifier ")"
                    | DEGREE "(" ("*" | identifier)? ")"

// Count target - explicit types for clarity
count_target: "*"                                          // Count all
            | "NODE" qualified_identifier                   // Count nodes explicitly
            | "EDGE" qualified_identifier                   // Count edges explicitly
            | "DISTINCT" qualified_identifier                // Count distinct values
            | qualified_identifier                          // Count property values (backward compatible)

// Aggregation target for SUM/AVG/MIN/MAX
agg_target: qualified_identifier | graph_native_arg

// Graph-native arguments
graph_native_arg: "OUTGOING" | "INCOMING" | "CONNECTIONS" | "DEGREE" | "OUT" | "INCOMING_DIR" | "CONNECTED"

// Grouping and ordering
group_by_clause: qualified_identifier ("," qualified_identifier)*
order_by_clause: order_spec ("," order_spec)* ("LIMIT" number)?
order_spec: qualified_identifier ("ASC" | "DESC")?
limit_clause: number

// Analytics stages
analytics_stage: pagerank_stage | shortest_path_stage | community_stage | centrality_stage | betweenness_centrality | closeness_centrality | degree_centrality | count_stage | aggregate_stage | graph_summary_stage

// Graph summary for analytics
graph_summary_stage: "GRAPH" "SUMMARY" ("ON" (identifier | "(" identifier_list ")"))? ("RETURN" "{" summary_field_list "}")?
summary_field_list: summary_field ("," summary_field)*
summary_field: identifier ":" aggregation_expression
aggregation_expression: aggregation_function | qualified_identifier "." aggregation_function

// Simple COUNT operation
count_stage: "COUNT" identifier ("WHERE" condition)?
aggregate_stage: "AGGREGATE" identifier "BY" identifier ("WHERE" condition)?
pagerank_stage: "PAGERANK" ("(" ")")? ("ON" identifier)? ("ITERATIONS" number)?
shortest_path_stage: "SHORTEST PATH" "(" "SOURCE" "=" (string | variable) "," "TARGET" "=" (string | variable) ("," "EDGE_TYPES" "=" "[" string_list "]")? ")" | "SHORTEST PATH" "SRC" (string | variable) "DEST" (string | variable) ("MAX_DEPTH" number)? ("WEIGHT_FIELD" identifier)?
community_stage: "COMMUNITY DETECTION" ("(" ("METHOD" "=" string | "ALGORITHM" "=" string)? ")")? ("ON" identifier)? | "COMMUNITY DETECTION" "ALGORITHM" (string | identifier) ("ON" identifier)?
centrality_stage: "CENTRALITY" ("(" (centrality_param ("," centrality_param)*)? ")")? ("ON" identifier)?
betweenness_centrality: "BETWEENNESS CENTRALITY" ("(" (centrality_param ("," centrality_param)*)? ")")? ("ON" identifier)?
closeness_centrality: "CLOSENESS CENTRALITY" ("(" (centrality_param ("," centrality_param)*)? ")")? ("ON" identifier)?
degree_centrality: "DEGREE CENTRALITY" ("(" (centrality_param ("," centrality_param)*)? ")")? ("ON" identifier)?
centrality_param: ("METHOD" | "TYPE") "=" string

string_list: string ("," string)*

// Indexing and sharding
index_stage: "CREATE" "INDEX" identifier "ON" identifier "(" identifier ")" "TYPE" index_type ("MODEL" "=" string)?
index_type: "SPARSE" | "DENSE" | "VECTOR" | "GRAPH" | "TEMPORAL" | "COMPOSITE"

shard_stage: "SHARD" "GRAPH" identifier "BY" "(" identifier ")"

// Schema and entity identification
schema_stage: match_entity_stage | list_entities_stage | show_stage | describe_stage
match_entity_stage: "MATCH" entity_pattern (entity_return_clause | return_clause)?
list_entities_stage: "LIST" "ENTITIES" | "SCHEMA"
show_stage: "SHOW" show_target ("IN" "NAMESPACE" identifier)? ("IN" "COLLECTION" identifier)?
describe_stage: "DESCRIBE" describe_target

// Entity patterns for MATCH
entity_pattern: node_pattern | edge_pattern
node_pattern: "NODE" identifier ("AS" identifier)?
edge_pattern: "EDGE" identifier ("AS" identifier)?

// Return clauses for entity information (renamed to avoid conflict)
entity_return_clause: "RETURN" entity_return_expression ("AS" identifier)?
entity_return_expression: count_expression | properties_expression | schema_expression | wildcard_expression | qualified_identifier
count_expression: COUNT_STAR | COUNT_ID identifier ")"
properties_expression: "properties"
schema_expression: "schema"
wildcard_expression: "*"

// Show and Describe targets
show_target: show_graph | show_node | show_edge | show_index | show_model | show_prompt | show_namespaces | show_pipelines | show_collections | show_schema | show_stats
show_graph: "GRAPH" | "GRAPHS"
show_node: "NODE" | "NODES"
show_stats: "STATS" | "STATISTICS"
show_edge: "EDGE" | "EDGES"
show_index: "INDEX" | "INDEXES"
show_model: "MODEL"
show_prompt: "PROMPT"
show_namespaces: "NAMESPACES"
show_pipelines: "PIPELINE" | "PIPELINES"
show_collections: "COLLECTION" | "COLLECTIONS"
show_schema: "SCHEMA"

describe_target: describe_graph | describe_node | describe_edge
describe_graph: "GRAPH" identifier
describe_node: "NODE" identifier | "NODE" "TYPE" identifier | "NODE" "TYPE" identifier
describe_edge: "EDGE" identifier | "EDGE" "TYPE" identifier

// Model and Prompt declarations
model_stage: "MODEL" identifier "AS" identifier model_config
model_config: model_param+
model_param: identifier "=" (string | number | variable)

prompt_stage: "PROMPT" identifier "AS" string

// Advanced traversal strategies
traversal_strategy: "STRATEGY" ("DFS" | "BFS")

// Namespace and access control
namespace_stage: "CREATE" "NAMESPACE" identifier | "USE" "NAMESPACE" identifier
grant_stage: "GRANT" grant_permission "ON" grant_target "TO" identifier
grant_permission: "READ" | "WRITE" | "EXECUTE"
grant_target: "GRAPH" identifier | "NODE" identifier | "EDGE" identifier

// Schema definitions
define_node_stage: "DEFINE" "NODE" "TYPE" identifier "(" node_field_list ")"
define_edge_stage: "DEFINE" "EDGE" "TYPE" identifier "(" edge_field_list ")" "SRC" identifier "DEST" identifier
node_field_list: node_field ("," node_field)*
edge_field_list: edge_field ("," edge_field)*
node_field: identifier field_type field_constraints?
edge_field: identifier field_type field_constraints?
field_type: "TEXT" | "INT" | "FLOAT" | "BOOLEAN" | "DATE"
field_constraints: constraint+
constraint: "UNIQUE" | "NOT" "NULL" | "PRIMARY" "KEY"

// Views and materialized views
view_stage: "CREATE" "VIEW" identifier "AS" string
// Materialized Context Views - Pre-computed RAG contexts for fast retrieval
materialized_view_stage: create_materialized_view | refresh_materialized_view | drop_materialized_view
create_materialized_view: "CREATE" "MATERIALIZED" ("CONTEXT")? "VIEW" identifier "AS" materialized_view_body ("REFRESH" refresh_trigger)?
materialized_view_body: retrieval_stage ("GROUND" "INTO" "Context" "(" context_params ")")?
context_params: context_param ("," context_param)*
context_param: identifier "=" (number | string | boolean)
refresh_trigger: "ON" refresh_event | "EVERY" time_interval
refresh_event: "chunk_insert" | "chunk_update" | "chunk_delete" | "node_change" | "edge_change"
time_interval: number time_unit
time_unit: "SECOND" | "SECONDS" | "MINUTE" | "MINUTES" | "HOUR" | "HOURS" | "DAY" | "DAYS"
refresh_materialized_view: "REFRESH" "MATERIALIZED" "VIEW" identifier
drop_materialized_view: "DROP" "MATERIALIZED" "VIEW" identifier

// Enhanced Sophisticated RAG Pipeline Extensions
rag_pipeline_stage: "RAG" "PIPELINE" string "{" rag_pipeline_body "}"
rag_pipeline_body: rag_stage ("THEN" rag_stage)*
rag_stage: source_ingestion_stage | file_reader_stage | multimodal_extraction_stage | smart_chunking_stage | embedding_stage | graph_construction_stage | indexing_stage | validation_stage | retrieval_system_stage | evaluation_stage | persistence_stage

// Source Ingestion with File Type Detection
source_ingestion_stage: "LOAD" source_type source_path ("INTO" identifier)? ("WITH" file_type_detection)?
source_type: "FOLDER" | "WEB" | "API" | "DATABASE" | "STREAM"
source_path: string | variable
file_type_detection: "AUTO_DETECT" | "SPECIFY" file_types
file_types: file_type ("AND" file_type)*
file_type: "PDF" | "TXT" | "MD" | "DOCX" | "HTML" | "JSON" | "CSV" | "XML" | "IMAGE" | "AUDIO" | "VIDEO" | "PRESENTATION" | "SPREADSHEET"

// File-Type-Based Readers
file_reader_stage: "READ" "WITH" reader_strategy ("FOR" file_types)?
reader_strategy: "PDF_READER" | "IMAGE_READER" | "AUDIO_READER" | "VIDEO_READER" | "TEXT_READER" | "TABLE_READER" | "OCR_READER" | "SPEECH_READER" | "MULTIMODAL_READER"

// Enhanced Multimodal Extraction
multimodal_extraction_stage: "EXTRACT" "MULTIMODAL" extraction_types ("WITH" extraction_config)?
extraction_types: extraction_type ("AND" extraction_type)*
extraction_type: "ENTITIES" | "TABLES" | "OCR" | "IMAGES" | "AUDIO" | "VIDEO" | "SPEECH" | "OBJECTS" | "FACES" | "SCENES" | "IMAGE_EXPLANATION" | "TABLE_STRUCTURE" | "DOCUMENT_LAYOUT"
extraction_config: "{" extraction_param ("," extraction_param)* "}"
extraction_param: identifier ("=" | ":") (string | number | boolean)

// Enhanced Smart Chunking with Strategy Selection
smart_chunking_stage: "CHUNK" "USING" chunking_strategy chunking_params? ("WITH" chunking_config)?
chunking_strategy: "SMART" | "FIXED_SIZE" | "SEMANTIC" | "SENTENCE" | "PARAGRAPH" | "SECTION" | "TABLE_AWARE" | "SLIDING_WINDOW" | "LLM_TOPIC_AWARE" | "FILE_TYPE_AWARE" | "CONTENT_AWARE"
chunking_params: "{" chunking_param ("," chunking_param)* "}"
chunking_param: identifier ("=" | ":") (string | number | boolean)
chunking_config: "{" chunking_config_param ("," chunking_config_param)* "}"
chunking_config_param: identifier ("=" | ":") (string | number | boolean)

// Enhanced Graph Construction
graph_construction_stage: "CREATE" "GRAPH" "RELATIONSHIPS" relationship_specs? ("WITH" relationship_config)?
relationship_specs: relationship_spec ("AND" relationship_spec)*
relationship_spec: "DOCUMENT_CHUNK" | "CHUNK_ENTITY" | "ENTITY_ENTITY" | "MULTIMODAL" | "SEMANTIC_SIMILARITY" | "TABLE_RELATIONSHIP" | "IMAGE_RELATIONSHIP" | "AUDIO_RELATIONSHIP" | "VIDEO_RELATIONSHIP"
relationship_config: "{" relationship_param ("," relationship_param)* "}"
relationship_param: identifier ("=" | ":") (string | number | boolean)

// Enhanced Indexing
indexing_stage: "CREATE" "INDEX" "ON" identifier "WHERE" condition "FOR" identifier ("WITH" index_config)?
index_config: "{" index_param ("," index_param)* "}"
index_param: identifier ("=" | ":") (string | number | boolean)

// Retrieval System
retrieval_system_stage: "RETRIEVAL" "SYSTEM" retrieval_config?
retrieval_config: "{" retrieval_param ("," retrieval_param)* "}"
retrieval_param: identifier ("=" | ":") (string | number | boolean)

// Evaluation System
evaluation_stage: "EVALUATE" "PIPELINE" evaluation_config?
evaluation_config: "{" evaluation_param ("," evaluation_param)* "}"
evaluation_param: identifier ("=" | ":") (string | number | boolean)

// Enhanced Validation
validation_stage: "VALIDATE" "PIPELINE" validation_rules? ("WITH" validation_config)?
validation_rules: validation_rule ("," validation_rule)*
validation_rule: "QUALITY_THRESHOLD" | "CONFIDENCE_THRESHOLD" | "COMPLETENESS_CHECK" | "CONSISTENCY_CHECK" | "ACCURACY_CHECK" | "COVERAGE_CHECK" | "RETRIEVAL_QUALITY" | "EXTRACTION_QUALITY"
validation_config: "{" validation_param ("," validation_param)* "}"
validation_param: identifier ("=" | ":") (string | number | boolean)

// Enhanced Persistence
persistence_stage: "SAVE" "PIPELINE" persistence_type "AS" string ("WITH" persistence_config)?
persistence_type: "STATE" | "CONFIG" | "METADATA" | "RESULTS" | "EVALUATION" | "RETRIEVAL_METRICS"
persistence_config: "{" persistence_param ("," persistence_param)* "}"
persistence_param: identifier ("=" | ":") (string | number | boolean)

// Enhanced Pipeline Management
pipeline_management_stage: "LOAD" "PIPELINE" string | "REPRODUCE" "PIPELINE" string | "SHOW" "PIPELINE" "METADATA" | "EVALUATE" "PIPELINE" string | "COMPARE" "PIPELINES" string_list

// Recursive path patterns
recursive_pattern: "+" | "{" number ".." number "}"

// Temporal operations
temporal_stage: "AT" "TIME" string
temporal_query: "AT" "TIME" string stage

// Streaming queries
stream_stage: "CREATE" "STREAM" identifier "FROM" "GRAPH" identifier "MATCH" entity_pattern where_clause? "EMIT" "ON" ("INSERT" | "UPDATE" | "DELETE")

// User-defined functions
udf_stage: "DEFINE" "FUNCTION" identifier "(" identifier ")" "AS" "RETURN" string

// Transactions
transaction_stage: "BEGIN" | "COMMIT" | "ROLLBACK"

// Explain query plan
explain_stage: "EXPLAIN" pipeline

// Event-driven operations
event_stage: "ON" "EVENT" event_spec agent_stage
event_spec: "Drift" "(" identifier ("," drift_params)? ")"
drift_params: drift_param ("," drift_param)*
drift_param: identifier "=" (number | string)

// CREATE GRAPH AS Pipeline - Declarative graph construction
graph_pipeline: pipeline_source chunk_spec? extract_spec upsert_spec? metadata_spec?

// Pipeline source (document, web, text, API)
pipeline_source: chunk_source | web_source | text_source | api_source
chunk_source: "CHUNK" (string | variable)
web_source: "WEB" (string | variable)
text_source: "TEXT" (string | variable)
api_source: web_api

// Chunking specification
chunk_spec: "CHUNK" "USING" chunk_params
chunk_params: chunk_param ("," chunk_param)*
chunk_param: identifier "=" (string | number)

// LLM extraction specification
extract_spec: "EXTRACT" "GRAPH" "WITH" "LLM" (string | identifier) llm_extraction_config
llm_extraction_config: "PASSES" "(" pass_list ")" schema_spec? prompt_spec?
pass_list: pass_name ("," pass_name)*
pass_name: "entity_extraction" | "relation_induction" | "canonicalization" | "schema_alignment" | identifier
schema_spec: "SCHEMA" (string | identifier)
prompt_spec: "PROMPT" string

// Upsert specification
upsert_spec: "UPSERT" "INTO" "GRAPH" upsert_params?
upsert_params: identifier "=" (string | variable) ("," identifier "=" (string | variable))*

// Metadata specification
metadata_spec: "WITH" "{" metadata_list "}"
metadata_list: metadata_item ("," metadata_item)*
metadata_item: identifier ":" (string | number | variable | function_call)
function_call: identifier "(" ")"

// Subqueries
subquery: "(" pipeline ")"


// Basic tokens (defined early at top, kept here for reference)
// identifier: IDENTIFIER (moved to top)
// string: STRING (moved to top)
// number: NUMBER (moved to top)
// boolean: "TRUE" | "FALSE" (moved to top)
// list: "[" (value ("," value)*)? "]" (moved to top)
identifier_list: identifier ("," identifier)*

// Aggregation function tokens
COUNT: "COUNT"
SUM: "SUM"
AVG: "AVG"
MIN: "MIN"
MAX: "MAX"
DISTINCT: "DISTINCT"
UNIQUE: "UNIQUE"
CONCAT: "CONCAT"
JOIN: "JOIN"
BETWEENNESS: "BETWEENNESS"
CLOSENESS: "CLOSENESS"
DEGREE: "DEGREE"
TRUE: "true"
FALSE: "false"
DAMPING: "DAMPING"
DAMPING_FACTOR: "DAMPING_FACTOR"
TOLERANCE: "TOLERANCE"
RESOLUTION: "RESOLUTION"
ITERATIONS: "ITERATIONS"
WITH: "WITH"
SRC: "SRC"
DEST: "DEST"
HOP: "HOP"
PATHS: "PATHS"
CLUSTERS: "CLUSTERS"
AS: "AS"

// Graph-native argument tokens
OUTGOING: "OUTGOING"
INCOMING: "INCOMING"
CONNECTIONS: "CONNECTIONS"
OUT: "OUT"
CONNECTED: "CONNECTED"

// Schema tokens
MATCH: "MATCH"
MERGE: "MERGE"
NODE: "NODE"
NODES: "NODES"
EDGE: "EDGE"
EDGES: "EDGES"
PROPERTY: "PROPERTY"
VIA: "VIA"
RETURN: "RETURN"
LIST: "LIST"
ENTITIES: "ENTITIES"
SCHEMA: "SCHEMA"
COUNT_SCHEMA: "count"
PROPERTIES: "properties"
COUNT_STAR: "count(*)"
COUNT_ID: "count("

// Show and Describe tokens
SHOW: "SHOW"
DESCRIBE: "DESCRIBE"
// GRAPH: "GRAPH"

// Model and Prompt tokens
MODEL: "MODEL"
PROMPT: "PROMPT"
TYPE: "TYPE"
PROVIDER: "PROVIDER"
API_KEY: "API_KEY"
TEMPERATURE: "TEMPERATURE"
MAX_TOKENS: "MAX_TOKENS"
MODEL_ID: "MODEL_ID"

// LLM Provider tokens
OPENAI: "OPENAI"
ANTHROPIC: "ANTHROPIC"
GOOGLE: "GOOGLE"
OLLAMA: "OLLAMA"
REGISTER: "REGISTER"
VARIABLES: "VARIABLES"
SYSTEM: "SYSTEM"
TEMPLATE: "TEMPLATE"

// Traversal strategy tokens
STRATEGY: "STRATEGY"
DFS: "DFS"
BFS: "BFS"
MAX_DEPTH: "MAX_DEPTH"
MIN_DEPTH: "MIN_DEPTH"
LIMIT: "LIMIT"
OFFSET: "OFFSET"

// Namespace and access control tokens
NAMESPACE: "NAMESPACE"
NAMESPACES: "NAMESPACES"
WITHIN: "WITHIN"
COLLECTION: "COLLECTION"
DESCRIPTION: "DESCRIPTION"
STAGES: "STAGES"
CREATE: "CREATE"
RETRIEVAL_PIPELINE: "RETRIEVAL_PIPELINE"
// PIPELINE: "PIPELINE"
// RUN: "RUN"
// QUERY: "QUERY"
RETRIEVE: "RETRIEVE"
// PROFILE: "PROFILE"
// STRATEGY: "STRATEGY"
INCLUDE: "INCLUDE"
TEMPORAL: "TEMPORAL"
POLICY: "POLICY"
CONTEXT: "CONTEXT"
WINDOW_SIZE: "WINDOW_SIZE"
MODE: "MODE"
DEDUPLICATE: "DEDUPLICATE"
// NODES: "NODES"
REASON: "REASON"
// MODEL: "MODEL"
INSTRUCTION: "INSTRUCTION"
VERIFY: "VERIFY"
// EVALUATE: "EVALUATE"
SUITE: "SUITE"
// METRICS: "METRICS"
STORE: "STORE"
// RESULTS: "RESULTS"
SIMILAR: "SIMILAR"
BETWEEN: "BETWEEN"
AGGREGATE: "AGGREGATE"
PLUS: "+"
GRANT: "GRANT"
READ: "READ"
WRITE: "WRITE"
EXECUTE: "EXECUTE"
TO: "TO"
CONNECT: "CONNECT"
// WITH: "WITH"
INPUT: "INPUT"

// Schema definition tokens
DEFINE: "DEFINE"
TEXT: "TEXT"
INT: "INT"
FLOAT: "FLOAT"
BOOLEAN: "BOOLEAN"
DATE: "DATE"
NOT: "NOT"
NULL: "NULL"
PRIMARY: "PRIMARY"
KEY: "KEY"
FROM: "FROM"

// View tokens
VIEW: "VIEW"
MATERIALIZE: "MATERIALIZE"

// Temporal tokens
AT: "AT"
TIME: "TIME"

// Streaming tokens
STREAM: "STREAM"
EMIT: "EMIT"
ON: "ON"
INSERT: "INSERT"
UPDATE: "UPDATE"
DELETE: "DELETE"

// Function tokens
FUNCTION: "FUNCTION"

// Transaction tokens
BEGIN: "BEGIN"
COMMIT: "COMMIT"
ROLLBACK: "ROLLBACK"

// Temporal tokens
TIMESTAMP: "TIMESTAMP"
RUN: "RUN"

// Explain token
EXPLAIN: "EXPLAIN"

// Evaluation tokens
EVAL: "EVAL"
METRICS: "METRICS"
COMPARE: "COMPARE"
BASELINE: "BASELINE"
SAVE: "SAVE"

// Web integration tokens
WEB: "WEB"
WEB_SEARCH: "WEB_SEARCH"
WEB_API: "WEB_API"
ADD_EMBEDDINGS: "ADD_EMBEDDINGS"
SPARSE_MATCH: "SPARSE_MATCH"
DENSE_MATCH: "DENSE_MATCH"
HYBRID_MATCH: "HYBRID_MATCH"
SHORTEST_PATH: "SHORTEST_PATH"
COMMUNITY_DETECTION: "COMMUNITY_DETECTION"
OPTIONS: "OPTIONS"
CALL: "CALL"
UPSERT: "UPSERT"
PROVENANCE: "PROVENANCE"
EVENT: "EVENT"
DRIFT: "Drift"

// CREATE GRAPH AS tokens
CHUNK: "CHUNK"
EXTRACT: "EXTRACT"
PASSES: "PASSES"
ENTITY_EXTRACTION: "entity_extraction"
RELATION_INDUCTION: "relation_induction"
CANONICALIZATION: "canonicalization"
SCHEMA_ALIGNMENT: "schema_alignment"
NOW: "NOW"
USING: "USING"

// New tokens for enhanced features
SET: "SET"
HAVING: "HAVING"
SOURCE: "SOURCE"
TARGET: "TARGET"
EDGE_TYPES: "EDGE_TYPES"
METHOD: "METHOD"
VECTOR: "VECTOR"

// Bulk loading tokens
CSV: "CSV"
JSON: "JSON"
XML: "XML"
SQL: "SQL"
API_LOAD: "API"
DELIMITER: "DELIMITER"
HEADER: "HEADER"
ENCODING: "ENCODING"
SKIP_ROWS: "SKIP_ROWS"
PATH: "PATH"
ARRAY_MODE: "ARRAY_MODE"
FLATTEN: "FLATTEN"
ROOT_PATH: "ROOT_PATH"
VALIDATE: "VALIDATE"
CONNECTION: "CONNECTION"
QUERY: "QUERY"
BATCH_SIZE: "BATCH_SIZE"
HEADERS: "HEADERS"
PARAMS: "PARAMS"
AUTH: "AUTH"
PAGINATION: "PAGINATION"

// Enhanced RAG Pipeline Tokens
MULTIMODAL: "MULTIMODAL"
FILE_TYPE_AWARE: "FILE_TYPE_AWARE"
CONTENT_AWARE: "CONTENT_AWARE"
TABLE_AWARE: "TABLE_AWARE"
RETRIEVAL_SYSTEM: "RETRIEVAL_SYSTEM"
EVALUATE: "EVALUATE"
PIPELINE: "PIPELINE"
PDF_READER: "PDF_READER"
IMAGE_READER: "IMAGE_READER"
AUDIO_READER: "AUDIO_READER"
VIDEO_READER: "VIDEO_READER"
TEXT_READER: "TEXT_READER"
TABLE_READER: "TABLE_READER"
OCR_READER: "OCR_READER"
SPEECH_READER: "SPEECH_READER"
MULTIMODAL_READER: "MULTIMODAL_READER"
TABLES: "TABLES"
IMAGE_EXPLANATION: "IMAGE_EXPLANATION"
TABLE_STRUCTURE: "TABLE_STRUCTURE"
DOCUMENT_LAYOUT: "DOCUMENT_LAYOUT"
AUDIO_TRANSCRIPT: "AUDIO_TRANSCRIPT"
VIDEO_FRAMES: "VIDEO_FRAMES"
OBJECT_DETECTION: "OBJECT_DETECTION"
SCENE_ANALYSIS: "SCENE_ANALYSIS"
SPEECH: "SPEECH"
IMAGES: "IMAGES"
AUDIO: "AUDIO"
VIDEO: "VIDEO"
SCENES: "SCENES"
OCR: "OCR"
OBJECTS: "OBJECTS"
FACES: "FACES"
PDF: "PDF"
TXT: "TXT"
MD: "MD"
DOCX: "DOCX"
HTML: "HTML"
PRESENTATION: "PRESENTATION"
SPREADSHEET: "SPREADSHEET"
SMART: "SMART"
FIXED_SIZE: "FIXED_SIZE"
SEMANTIC: "SEMANTIC"
SENTENCE: "SENTENCE"
PARAGRAPH: "PARAGRAPH"
SECTION: "SECTION"
SLIDING_WINDOW: "SLIDING_WINDOW"
LLM_TOPIC_AWARE: "LLM_TOPIC_AWARE"
DOCUMENT_CHUNK: "DOCUMENT_CHUNK"
CHUNK_ENTITY: "CHUNK_ENTITY"
ENTITY_ENTITY: "ENTITY_ENTITY"
SEMANTIC_SIMILARITY: "SEMANTIC_SIMILARITY"
TABLE_RELATIONSHIP: "TABLE_RELATIONSHIP"
IMAGE_RELATIONSHIP: "IMAGE_RELATIONSHIP"
AUDIO_RELATIONSHIP: "AUDIO_RELATIONSHIP"
VIDEO_RELATIONSHIP: "VIDEO_RELATIONSHIP"
QUALITY_THRESHOLD: "QUALITY_THRESHOLD"
CONFIDENCE_THRESHOLD: "CONFIDENCE_THRESHOLD"
COMPLETENESS_CHECK: "COMPLETENESS_CHECK"
CONSISTENCY_CHECK: "CONSISTENCY_CHECK"
ACCURACY_CHECK: "ACCURACY_CHECK"
COVERAGE_CHECK: "COVERAGE_CHECK"
RETRIEVAL_QUALITY: "RETRIEVAL_QUALITY"
EXTRACTION_QUALITY: "EXTRACTION_QUALITY"
STATE: "STATE"
CONFIG: "CONFIG"
METADATA: "METADATA"
RESULTS: "RESULTS"
EVALUATION: "EVALUATION"
RETRIEVAL_METRICS: "RETRIEVAL_METRICS"
REPRODUCE: "REPRODUCE"
AUTO_DETECT: "AUTO_DETECT"
SPECIFY: "SPECIFY"

// Enhanced Pipeline Tokens
FILE: "FILE"
READER: "READER"
DETECT: "DETECT"
// TEXT: "TEXT" // Defined earlier
// DESCRIBE: "DESCRIBE" // Defined earlier
// EXTRACT: "EXTRACT" // Defined earlier
// STORE: "STORE" // Defined earlier
// AS: "AS" // Defined earlier
// NODE: "NODE" // Defined earlier
// TYPES: "TYPES" // Need to check if defined
// LINK: "LINK" // Need to check if defined
PARSE_METADATA: "PARSE_METADATA"
NORMALIZE: "NORMALIZE"
REMOVE_DUPLICATES: "REMOVE_DUPLICATES"
EXPAND_ABBREVIATIONS: "EXPAND_ABBREVIATIONS"
INTERMEDIATE: "INTERMEDIATE"
ARROW: "->"
MINUS: "-"
// IN: "IN"
// COLLECTION: "COLLECTION"
BY: "BY"
// MODEL: "MODEL"
PARAMETERS: "PARAMETERS"
// TO: "TO"
// ON: "ON"
// PROMPT: "PROMPT"
// ENTITIES: "ENTITIES"
CHUNKS: "CHUNKS"
OF: "OF"
// SOURCE: "SOURCE"
// USING: "USING"
LLM: "LLM"
// PASSES: "PASSES"
OUTPUT: "OUTPUT"
RELATIONSHIPS: "RELATIONSHIPS"
RELATIONSHIP_TYPES: "RELATIONSHIP_TYPES"
// EDGE: "EDGE"
// TYPE: "TYPE"
// FROM: "FROM"
EMBED: "EMBED"
// PROFILE: "PROFILE"
CONTEXT_POLICY: "CONTEXT_POLICY"
EMBEDDING: "EMBEDDING"
// CONNECT: "CONNECT"
// AUTO: "AUTO"
// INCLUDE: "INCLUDE"
// EDGES: "EDGES"
TEMPORAL_POLICY: "TEMPORAL_POLICY"
INDEX: "INDEX"
BUILD_INDEX: "BUILD_INDEX"
// IMAGES: "IMAGES"
// AUDIO: "AUDIO"
// VIDEO: "VIDEO"
// TABLES: "TABLES"
// OCR: "OCR"
// PLUS: "+"

// Token definitions
// Session Management
session_statement: begin_session | end_session | save_session | load_session | pause_session | resume_session | show_session | set_session
begin_session: ("BEGIN" | "START") "SESSION" (string | identifier) ("OPTIONS" "(" session_options ")")?
end_session: "END" "SESSION" ("SAVE")?
save_session: "SAVE" "SESSION" ("TO" string)?
load_session: "LOAD" "SESSION" "FROM" string
pause_session: "PAUSE" "SESSION"
resume_session: "RESUME" "SESSION" (string | identifier)
show_session: "SHOW" "SESSION"
set_session: "SET" "SESSION" session_options
session_options: session_option ("," session_option)*
session_option: identifier ":" (string | boolean | number)

// User Functions
create_function: "CREATE" "FUNCTION" identifier "{" function_definition "}"
function_definition: function_property ("," function_property)*
function_property: identifier ":" (string | identifier | list)
use_function: "USE" "FUNCTION" identifier ("PARAMETERS" "(" parameter_dict ")")? ("INPUT" "FROM" (identifier | variable))? ("STORE" "RESULT" "AS" identifier)?

// Agentic Wrapping
agent_statement: create_agent | plan_agent | execute_agent
create_agent: "CREATE" "AGENT" string "{" agent_definition "}"
agent_definition: agent_property ("," agent_property)*
agent_property: identifier ":" (string | boolean | list | "{" parameter_dict "}")
plan_agent: "PLAN" "AGENT" string ("INPUT" "{" parameter_dict "}")? ("TOOLS" "[" tool_list "]")? ("STRATEGY" "{" parameter_dict "}")?
tool_list: tool_spec ("," tool_spec)*
tool_spec: string ("{" parameter_dict "}")?
execute_agent: "EXECUTE" "AGENT" string ("WITH" execute_options)* ("RETURN" identifier_list)?
execute_options: "AUTO_SAVE_FINDINGS" "TO" "GRAPH" | "MEMORY" "CONTEXT" boolean

// Memory Management
memory_statement: store_memory | retrieve_memory
store_memory: "STORE" "IN" "MEMORY" "{" property_list "}"
retrieve_memory: "RETRIEVE" "FROM" "MEMORY" ("WHERE" condition)? ("LIMIT" number)?

// Operational Controls
operational_statement: explain_plan | optimize_query | cache_statement | attach_policy | set_guardrails | set_logging
explain_plan: "EXPLAIN" "PLAN" "FOR" (top_select | search_query_basic | hybrid_search)
optimize_query: "OPTIMIZE" "QUERY" (top_select | search_query_basic | hybrid_search) ("RETURN" string)?
cache_statement: cache_warmup | cache_stats | cache_clear
cache_warmup: "CACHE" "WARMUP" ("FOR" "NAMESPACE" (identifier | variable))?
cache_stats: "CACHE" "STATS"
cache_clear: "CACHE" "CLEAR"
attach_policy: "ATTACH" "POLICY" string "TO" "PIPELINE" identifier
set_guardrails: "SET" "GUARDRAILS" "FOR" "PIPELINE" identifier "{" parameter_dict "}"
set_logging: "SET" "LOGGING" "ON" "FOR" "PIPELINE" identifier ("WITH" "LEVEL" string)?

IDENTIFIER: /[a-zA-Z_][a-zA-Z0-9_-]*/
FUNCTION_NAME: /[a-zA-Z_][a-zA-Z0-9_.-]+/
DOT: "."

// Hybrid Search Terminals
HYBRID: "HYBRID"
SEARCH: "SEARCH"
WEIGHTS: "WEIGHTS"
// PROFILE: "PROFILE"
DENSE: "DENSE"
SPARSE: "SPARSE"
GRAPH: "GRAPH"
// AUTO: "AUTO"
DYNAMIC: "DYNAMIC"
STATIC: "STATIC"
DEFAULT: "DEFAULT"
// Improved STRING token to handle multi-line strings better
STRING: /"([^"\\\\]|\\.|\\\\n)*"/ | /'([^'\\\\]|\\.|\\\\n)*'/
NUMBER: /\\d+(\\.\\d+)?([eE][+-]?\\d+)?/

// Legacy support for backward compatibility
legacy_pipeline: pipeline (";" pipeline)* (";")?
legacy_retrieval_pipeline: retrieval_pipeline (";" retrieval_pipeline)* (";")?
legacy_hybrid_search: hybrid_search (";" hybrid_search)* (";")?

// Comment handling - lines starting with -- or #
_COMMENT: "--" /.*/
_WS: /\\s+/

%ignore _WS
%ignore _COMMENT
"""

# Grammar validation and helper functions
def validate_grammar():
    """Validate the AIQL grammar syntax."""
    try:
        import lark
        parser = lark.Lark(AIQL_GRAMMAR, parser='lalr')
        return True, "Grammar is valid"
    except Exception as e:
        return False, f"Grammar validation failed: {e}"

def get_grammar():
    """Get the AIQL grammar definition."""
    return AIQL_GRAMMAR

# Example queries for testing
EXAMPLE_QUERIES = {
    "variable_declaration": """
LET $author = "Alice"
LET $limit = 10
""",
    
    "graph_creation": """
CREATE GRAPH social_graph
THEN CREATE NODE Person AS p {name: "Alice", age: 30} UNIQUE KEY(name)
THEN CREATE EDGE KNOWS AS k {since: 2020} UNIQUE KEY(since)
""",
    
    "inline_traversal": """
TRAVERSE Person:a -KNOWS-> Person:b -KNOWS-> Person:c
WHERE a.name = $author
SELECT c.name
""",
    
    "verbose_traversal": """
TRAVERSE FROM Person AS p OUTGOING KNOWS EDGE TO Person AS f
WHERE p.name = $author
SELECT f.name
""",
    
    "hybrid_search": """
LOAD DOCUMENT "research_paper.pdf" INTO documents
THEN CHUNK BY USING semantic {chunk_size: 500}
THEN ADD EMBEDDINGS USING sentence-transformers {model: "all-MiniLM-L6-v2"}
THEN HYBRID ("machine learning research") IN documents WITH semantic=0.7, keyword=0.3 LIMIT 5
THEN TRAVERSE FROM (results) OUTGOING CITED_BY EDGE TO Document AS cited
SELECT cited.title
""",
    
    "analytics": """
TRAVERSE FROM Person AS p OUTGOING KNOWS EDGE TO Person AS f
THEN PAGERANK ON f ITERATIONS 10
THEN COMMUNITY DETECTION ALGORITHM louvain
""",
    
    "indexing": """
CREATE INDEX DENSE ON documents BY embedding
THEN CREATE INDEX SPARSE ON documents BY content
THEN SHARD GRAPH social_graph BY (community_id)
""",
    
    "aggregation_basic": """
SELECT COUNT(*) FROM Employee
""",
    
    "aggregation_group_by": """
SELECT department, COUNT(*) as employee_count 
FROM Employee 
GROUP BY department
""",
    
    "aggregation_avg": """
SELECT department, AVG(salary) as avg_salary 
FROM Employee 
GROUP BY department
""",
    
    "aggregation_order_by": """
SELECT name, salary 
FROM Employee 
ORDER BY salary DESC 
LIMIT 3
""",
    
    "aggregation_complex": """
SELECT department, COUNT(*) as count, AVG(salary) as avg_salary, MAX(salary) as max_salary
FROM Employee 
WHERE age > 25 
GROUP BY department 
ORDER BY avg_salary DESC
"""
}

if __name__ == "__main__":
    # Test grammar validation
    is_valid, message = validate_grammar()
    print(f"Grammar validation: {message}")
    
    if is_valid:
        print("\nSUCCESS: AIQL Grammar is valid and ready for parser implementation!")
        print("\nExample queries:")
        for name, query in EXAMPLE_QUERIES.items():
            print(f"\n{name.upper()}:")
            print(query.strip())
    else:
        print("FAILURE: Grammar needs fixes before proceeding")
