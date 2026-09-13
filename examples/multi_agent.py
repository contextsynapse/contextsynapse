"""
Multi-Agent Context Sharing
============================
Demonstrates how multiple AI agents share context through AIContextDB's
Context-as-a-Service.

This example uses the SDK client to interact with a running AIContextDB server.

Prerequisites:
    pip install -e "."
    Start the server: python -m contextsynapse
    Create a user account at http://localhost:3000/signup

Run:
    python examples/multi_agent.py
"""

from contextsynapse import AIContextDB

# Connect to a running AIContextDB server
# Replace with your actual API key after signing up
BASE_URL = "http://localhost:8000"


def main():
    print("=== Multi-Agent Context Sharing Demo ===\n")

    # In production, use real JWT tokens from /auth/login
    # For this demo, we show the SDK's API structure

    print("1. Initialize client")
    print(f"   ctx = AIContextDB(base_url='{BASE_URL}', api_key='your-jwt-token')")

    print("\n2. Register agents")
    print("   researcher = ctx.agents.register('researcher', role='agent', capabilities=['read', 'write'])")
    print("   writer = ctx.agents.register('writer', role='agent', capabilities=['read'])")

    print("\n3. Create shared session")
    print("   session = ctx.sessions.create('research-project')")
    print("   ctx.sessions.grant_access(session.session_id, writer.agent_id, level='read')")

    print("\n4. Researcher adds context")
    print("   session.write('findings', {'topic': 'graph databases', 'summary': '...'})")
    print("   session.tag('findings', tags=['research', 'databases'])")

    print("\n5. Writer reads shared context")
    print("   context = session.export(format='messages', max_tokens=4000)")
    print("   # Pass context to LLM for content generation")

    print("\n6. Search across all graph data")
    print("   results = ctx.search('graph database performance', top_k=5)")

    print("\n--- SDK Quick Reference ---")
    print("""
    from contextsynapse import AIContextDB

    # Connect
    ctx = AIContextDB(base_url="http://localhost:8000", api_key="agent_id:secret")

    # Agents
    agent = ctx.agents.register("my-agent", role="agent")
    agents = ctx.agents.list()
    ctx.agents.delete(agent.agent_id)

    # Sessions (shared context)
    session = ctx.sessions.create("my-session")
    session = ctx.sessions.get("session-id")
    sessions = ctx.sessions.list()
    session.write("key", {"data": "value"})
    context = session.export(format="messages", max_tokens=4000)

    # Search
    results = ctx.search("my query", top_k=10)
    """)


if __name__ == "__main__":
    main()
