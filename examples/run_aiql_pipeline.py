#!/usr/bin/env python3
"""
Run the complete ingestion pipeline using AIQL.

This script:
1. Reads the pipeline_complete_ingestion.aiql file
2. Creates the pipeline definitions
3. Runs the pipeline
"""
import sys
import os
import time
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding issues
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    import io
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor
from contextsynapse.core.registry import graph_registry


def generate_namespace(base_name: str = "ingestion") -> str:
    """Generate a descriptive namespace name with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base_name}_{timestamp}"


def read_and_prepare_aiql_file(file_path: str, namespace: str) -> str:
    """Read AIQL file and replace namespace placeholder."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace namespace placeholder
    content = content.replace('{namespace}', namespace)
    
    return content


def execute_aiql_statements(executor: AIQLExecutor, aiql_content: str) -> bool:
    """Execute AIQL statements, splitting by semicolons."""
    # Split by semicolons but preserve CREATE PIPELINE blocks
    statements = []
    current_statement = []
    in_pipeline = False
    
    lines = aiql_content.split('\n')
    for line in lines:
        stripped = line.strip()
        
        # Skip comments and empty lines
        if stripped.startswith('--') or not stripped:
            continue
        
        current_statement.append(line)
        
        # Check if we're in a CREATE PIPELINE block
        if 'CREATE PIPELINE' in stripped.upper():
            in_pipeline = True
        
        # End of statement (semicolon at end of line or standalone)
        if stripped.endswith(';') and not in_pipeline:
            statements.append('\n'.join(current_statement))
            current_statement = []
        elif stripped.endswith('];') and in_pipeline:
            # End of CREATE PIPELINE
            statements.append('\n'.join(current_statement))
            current_statement = []
            in_pipeline = False
    
    # Add any remaining statement
    if current_statement:
        statements.append('\n'.join(current_statement))
    
    # Execute each statement
    success_count = 0
    for i, statement in enumerate(statements):
        statement = statement.strip()
        if not statement:
            continue
        
        try:
            print(f"\n[INFO] Executing statement {i+1}/{len(statements)}...")
            result = executor.execute(statement)
            
            if isinstance(result, dict):
                if result.get("success", True):
                    success_count += 1
                    print(f"[OK] Statement {i+1} executed successfully")
                else:
                    error = result.get("error", "Unknown error")
                    # Check if it's a "already exists" error (which is OK)
                    if "already exists" in error.lower() or "duplicate" in error.lower():
                        print(f"[INFO] Statement {i+1}: {error}")
                        success_count += 1
                    else:
                        print(f"[WARN] Statement {i+1} warning: {error}")
            else:
                success_count += 1
                print(f"[OK] Statement {i+1} executed successfully")
                
        except Exception as e:
            error_str = str(e).lower()
            if "already exists" in error_str or "duplicate" in error_str:
                print(f"[INFO] Statement {i+1}: {e}")
                success_count += 1
            else:
                print(f"[ERROR] Statement {i+1} failed: {e}")
                import traceback
                traceback.print_exc()
    
    return success_count == len(statements)


def run_pipeline(executor: AIQLExecutor, namespace: str, pipeline_name: str = "simplified_ingestion_pipeline") -> dict:
    """Run the specified pipeline."""
    print("\n" + "=" * 80)
    print("RUNNING PIPELINE")
    print("=" * 80)
    print(f"Namespace: {namespace}")
    print(f"Pipeline: {pipeline_name}")
    print("=" * 80)
    
    run_query = f"""
    USE NAMESPACE {namespace};
    
    RUN PIPELINE {pipeline_name};
    """
    
    start_time = time.time()
    try:
        print("\n[INFO] Starting pipeline execution (this may take a while)...")
        result = executor.execute(run_query)
        total_time = time.time() - start_time
        
        print("\n" + "=" * 80)
        print("PIPELINE EXECUTION SUMMARY")
        print("=" * 80)
        print(f"Total Time: {total_time:.2f} seconds ({total_time/60:.2f} minutes)")
        
        if isinstance(result, dict):
            success = result.get("success", False)
            if success:
                print("[OK] Pipeline execution completed successfully")
                return {
                    "success": True,
                    "result": result,
                    "execution_time": total_time
                }
            else:
                error = result.get("error", "Unknown error")
                print(f"[ERROR] Pipeline execution failed: {error}")
                return {"success": False, "error": error, "execution_time": total_time}
        else:
            print("[OK] Pipeline execution completed")
            return {"success": True, "result": result, "execution_time": total_time}
            
    except Exception as e:
        print(f"[ERROR] Pipeline execution failed: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "execution_time": time.time() - start_time}


def main():
    """Main execution function."""
    print("=" * 80)
    print("AIQL INGESTION PIPELINE RUNNER")
    print("=" * 80)
    
    # Configuration
    aiql_file = project_root / "pipeline_complete_ingestion.aiql"
    namespace = generate_namespace("ingestion")
    pipeline_name = "simplified_ingestion_pipeline"  # Use simplified for faster execution
    
    # Allow override via command line
    if len(sys.argv) > 1:
        if sys.argv[1] == "complete":
            pipeline_name = "complete_ingestion_pipeline"
        elif sys.argv[1] == "simplified":
            pipeline_name = "simplified_ingestion_pipeline"
    
    print(f"AIQL File: {aiql_file}")
    print(f"Namespace: {namespace}")
    print(f"Pipeline: {pipeline_name}")
    print("=" * 80)
    
    # Check if AIQL file exists
    if not aiql_file.exists():
        print(f"[ERROR] AIQL file not found: {aiql_file}")
        sys.exit(1)
    
    # Initialize executor
    print("\n[1] Initializing AIQL executor...")
    try:
        # Create or get graph from registry
        graph = graph_registry.get_graph(namespace, load_if_missing=True)
        if not graph:
            graph = graph_registry.create_graph(namespace)
        executor = AIQLExecutor(contextsynapse=graph, graph_registry=graph_registry)
        executor.active_namespace = namespace
        print("[OK] Executor initialized")
    except Exception as e:
        print(f"[ERROR] Failed to initialize executor: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Create namespace
    print("\n[2] Creating namespace...")
    try:
        create_ns_query = f"CREATE NAMESPACE {namespace};"
        executor.execute(create_ns_query)
        print(f"[OK] Namespace created: {namespace}")
    except Exception as e:
        if "already exists" not in str(e).lower():
            print(f"[WARN] Namespace creation: {e}")
        print(f"[INFO] Using namespace: {namespace}")
    
    # Use namespace
    try:
        use_ns_query = f"USE NAMESPACE {namespace};"
        executor.execute(use_ns_query)
        print(f"[OK] Using namespace: {namespace}")
    except Exception as e:
        print(f"[WARN] Namespace switch: {e}")
    
    # Read and prepare AIQL file
    print("\n[3] Reading AIQL file...")
    try:
        aiql_content = read_and_prepare_aiql_file(str(aiql_file), namespace)
        print("[OK] AIQL file read and prepared")
    except Exception as e:
        print(f"[ERROR] Failed to read AIQL file: {e}")
        sys.exit(1)
    
    # Execute CREATE PIPELINE statements
    print("\n[4] Creating pipelines...")
    try:
        success = execute_aiql_statements(executor, aiql_content)
        if success:
            print("[OK] All pipeline definitions created successfully")
        else:
            print("[WARN] Some pipeline definitions may have failed (check messages above)")
    except Exception as e:
        print(f"[ERROR] Failed to create pipelines: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Run the pipeline
    print("\n[5] Running pipeline...")
    result = run_pipeline(executor, namespace, pipeline_name)
    
    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    if result.get("success"):
        print("[SUCCESS] Pipeline execution completed successfully!")
        print(f"Namespace: {namespace}")
        print(f"Execution Time: {result.get('execution_time', 0):.2f} seconds")
        print("\nYou can now query the data using AIQL:")
        print(f"  USE NAMESPACE {namespace};")
        print("  SELECT * FROM Document LIMIT 5;")
        print("  SELECT * FROM Chunk LIMIT 5;")
    else:
        print("[ERROR] Pipeline execution failed!")
        print(f"Error: {result.get('error', 'Unknown error')}")
        sys.exit(1)
    print("=" * 80)


if __name__ == "__main__":
    main()




















