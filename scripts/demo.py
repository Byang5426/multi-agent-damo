"""Demo script: run the multi-agent workflow end-to-end.

Usage:
    # Set your API key first:
    export OPENAI_API_KEY="sk-..."
    # Or copy .env.example to .env and fill in

    # Run the demo:
    python scripts/demo.py

    # Or run the server:
    python scripts/demo.py server
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("demo")


async def demo_instant_task():
    """Demo 1: Instant task - single worker execution."""
    from multi_agent.graph.workflow import run_workflow

    print("\n" + "=" * 60)
    print("DEMO 1: Instant Task (Single Worker)")
    print("=" * 60)

    result = await run_workflow(
        user_input="Analyze the pros and cons of microservices vs monolith architecture for a startup with 5 developers.",
        request_id="demo-instant-001",
    )

    print(f"\n--- Response ---\n{result.get('final_response', 'N/A')}")
    print(f"\n--- Trace count: {len(result.get('trace_logs', []))} ---")


async def demo_project_task():
    """Demo 2: Project task - PM decomposition + multiple workers."""
    from multi_agent.graph.workflow import run_workflow

    print("\n" + "=" * 60)
    print("DEMO 2: Project Task (PM + Multiple Workers)")
    print("=" * 60)

    result = await run_workflow(
        user_input=(
            "Build a simple REST API for a todo app with the following requirements:\n"
            "1. First, analyze the requirements and design the API endpoints\n"
            "2. Then implement the Python FastAPI code with CRUD operations\n"
            "3. Finally, write a test plan covering happy path and edge cases"
        ),
        request_id="demo-project-001",
    )

    print(f"\n--- Response ---\n{result.get('final_response', 'N/A')}")

    project = result.get("project")
    if project:
        print(f"\n--- Project ID: {project.get('project_id')} ---")

    tasks = result.get("tasks", [])
    if tasks:
        print(f"\n--- Tasks ({len(tasks)}) ---")
        for t in tasks:
            print(f"  [{t.get('status', '?')}] {t.get('title', 'N/A')}")

    print(f"\n--- Trace count: {len(result.get('trace_logs', []))} ---")


async def demo_api_server():
    """Demo 3: Start the FastAPI server."""
    import uvicorn

    print("\n" + "=" * 60)
    print("DEMO 3: Starting API Server")
    print("=" * 60)
    print("Server will be available at http://localhost:8000")
    print("Try: curl -X POST http://localhost:8000/api/v1/chat \\")
    print('     -H "Content-Type: application/json" \\')
    print('     -d \'{"message": "Analyze Python vs Go for backend development"}\'')
    print()

    config = uvicorn.Config(
        "multi_agent.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Run all demos or start the server based on args."""
    from multi_agent.config import settings

    if not settings.openai_api_key:
        print("ERROR: OPENAI_API_KEY not set.")
        print("Please set it via environment variable or .env file.")
        print("  export OPENAI_API_KEY='sk-...'")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "server":
        await demo_api_server()
    elif len(sys.argv) > 1 and sys.argv[1] == "instant":
        await demo_instant_task()
    elif len(sys.argv) > 1 and sys.argv[1] == "project":
        await demo_project_task()
    else:
        print("Multi-Agent System Demo")
        print("Usage:")
        print("  python scripts/demo.py instant  - Run instant task demo")
        print("  python scripts/demo.py project  - Run project task demo")
        print("  python scripts/demo.py server   - Start the API server")
        print()
        await demo_instant_task()
        await demo_project_task()


if __name__ == "__main__":
    asyncio.run(main())
