"""
Agent Templates — predefined agent configurations for the playground.
"""

AGENT_TEMPLATES = {
    "researcher": {
        "name": "Research Agent",
        "system_prompt": (
            "You are a research agent. Your job is to explore the knowledge graph, "
            "find relevant information, extract key entities and facts, and summarize findings. "
            "Use search_nodes to find data, briefing for overview, rag_query for specific questions, "
            "and add_knowledge to record your findings."
        ),
        "tools": ["search_nodes", "briefing", "rag_query", "ask", "add_knowledge", "get_context"],
        "description": "Explores contexts, extracts knowledge, and summarizes findings",
    },
    "analyst": {
        "name": "Analysis Agent",
        "system_prompt": (
            "You are a data analyst agent. Analyze the knowledge graph for patterns, "
            "conflicts, and insights. Use graph_summary for structure, detect_conflicts "
            "to find inconsistencies, search_nodes for specific data, and add_knowledge "
            "to record your analysis."
        ),
        "tools": ["search_nodes", "query_graph", "graph_summary", "detect_conflicts",
                  "get_suggestions", "check_freshness", "add_knowledge"],
        "description": "Analyzes data for patterns, conflicts, and insights",
    },
    "writer": {
        "name": "Content Writer",
        "system_prompt": (
            "You are a content writer agent. Use the context to write well-structured "
            "content. Query the knowledge graph for facts and entities, then produce "
            "clear, accurate written output. Store your output using add_knowledge."
        ),
        "tools": ["rag_query", "rag_graph", "get_context", "search_nodes",
                  "briefing", "add_knowledge"],
        "description": "Writes content based on context knowledge",
    },
    "coder": {
        "name": "Code Agent",
        "system_prompt": (
            "You are a software development agent. Read requirements from the knowledge graph, "
            "write code files, and commit changes. Use search_nodes to find requirements, "
            "ws_read_file to examine existing code, ws_write_file to create/modify files, "
            "and ws_commit to save your work."
        ),
        "tools": ["search_nodes", "rag_query", "ws_read_file", "ws_write_file",
                  "ws_list_files", "ws_commit", "ws_git_status", "add_knowledge"],
        "description": "Implements features based on requirements in the graph",
    },
    "custom": {
        "name": "Custom Agent",
        "system_prompt": "You are a helpful AI agent with access to the knowledge graph tools.",
        "tools": [],  # User selects
        "description": "Fully customizable agent — define your own prompt and tools",
    },
}

FRAMEWORK_INFO = {
    "openai": {
        "name": "OpenAI Function Calling",
        "description": "Agent uses LLM with function-calling to decide which tools to invoke. Simplest and most reliable.",
        "icon": "zap",
        "color": "#10b981",
    },
    "crewai": {
        "name": "CrewAI",
        "description": "Multi-agent crew with defined roles. Agents collaborate and delegate tasks to each other.",
        "icon": "users",
        "color": "#3b82f6",
    },
    "langgraph": {
        "name": "LangGraph",
        "description": "Stateful agent graph with checkpoints. Tools are graph nodes, LLM routes between them.",
        "icon": "git-branch",
        "color": "#8b5cf6",
    },
    "mcp": {
        "name": "Raw MCP Tools",
        "description": "Direct tool call sequences. No LLM — deterministic execution of tool steps.",
        "icon": "terminal",
        "color": "#f59e0b",
    },
}
