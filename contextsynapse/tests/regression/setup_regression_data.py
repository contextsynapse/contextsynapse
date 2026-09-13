#!/usr/bin/env python3
"""
Setup Regression Test Data
Runs ingestion pipelines to create test data for regression tests.
Supports multiple chunking strategies: recursive, fixed-size, semantic, section-based.
"""
import sys
import logging
from pathlib import Path
from typing import List, Optional

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse import AIContextDB
from contextsynapse.aiql.engine import AIQLExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Regression test namespace
REGRESSION_NAMESPACE = "tcs_regression_test"

# Pipeline files
PIPELINE_DIR = Path(__file__).parent / "pipelines"
PIPELINES = {
    "recursive": PIPELINE_DIR / "regression_ingestion_recursive.aiql",
    "fixed_size": PIPELINE_DIR / "regression_ingestion_fixed_size.aiql",
    "semantic": PIPELINE_DIR / "regression_ingestion_semantic.aiql",
    "section": PIPELINE_DIR / "regression_ingestion_section.aiql",
}


def load_pipeline_query(pipeline_file: Path) -> str:
    """Load pipeline query from file."""
    try:
        with open(pipeline_file, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load pipeline from {pipeline_file}: {e}")
        return None


def create_namespace(executor: AIQLExecutor, namespace: str) -> bool:
    """Create namespace if it doesn't exist."""
    try:
        create_query = f"CREATE NAMESPACE {namespace}"
        result = executor.execute(create_query)
        logger.info(f"Created namespace: {namespace}")
        return True
    except Exception as e:
        # Namespace might already exist
        logger.debug(f"Namespace creation (may already exist): {e}")
        return True


def create_pipeline(executor: AIQLExecutor, pipeline_query: str) -> bool:
    """Create pipeline from query."""
    try:
        # Set namespace first
        use_query = f"USE NAMESPACE {REGRESSION_NAMESPACE}"
        executor.execute(use_query)
        
        # Create pipeline
        result = executor.execute(pipeline_query)
        logger.info("Pipeline created successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to create pipeline: {e}")
        return False


def run_pipeline(executor: AIQLExecutor, pipeline_name: str, input_file: str) -> bool:
    """Run pipeline on input file."""
    try:
        # Set namespace first
        use_query = f"USE NAMESPACE {REGRESSION_NAMESPACE}"
        executor.execute(use_query)
        
        # Run pipeline
        run_query = f'RUN PIPELINE {pipeline_name} ON "{input_file}"'
        result = executor.execute(run_query)
        logger.info(f"Pipeline {pipeline_name} executed successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to run pipeline {pipeline_name}: {e}")
        return False


def setup_regression_data(
    strategies: Optional[List[str]] = None,
    input_file: Optional[str] = None,
    create_only: bool = False
) -> bool:
    """
    Setup regression test data by creating and optionally running ingestion pipelines.
    
    Args:
        strategies: List of strategies to use (recursive, fixed_size, semantic, section).
                   If None, uses all strategies.
        input_file: Path to input PDF file. Required if create_only=False.
        create_only: If True, only create pipelines without running them.
    
    Returns:
        True if successful, False otherwise.
    """
    if strategies is None:
        strategies = list(PIPELINES.keys())
    
    # Initialize executor
    try:
        graph = AIContextDB(REGRESSION_NAMESPACE, storage_backend='csr')
        executor = AIQLExecutor(contextcore=graph)
        executor.active_namespace = REGRESSION_NAMESPACE
        
        # Create namespace
        if not create_namespace(executor, REGRESSION_NAMESPACE):
            logger.error("Failed to create namespace")
            return False
        
        # Create and optionally run pipelines
        success_count = 0
        for strategy in strategies:
            if strategy not in PIPELINES:
                logger.warning(f"Unknown strategy: {strategy}, skipping")
                continue
            
            pipeline_file = PIPELINES[strategy]
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing strategy: {strategy}")
            logger.info(f"{'='*60}")
            
            # Load pipeline query
            pipeline_query = load_pipeline_query(pipeline_file)
            if not pipeline_query:
                logger.error(f"Failed to load pipeline for {strategy}")
                continue
            
            # Create pipeline
            pipeline_name = f"regression_ingestion_{strategy}"
            logger.info(f"Creating pipeline: {pipeline_name}")
            if not create_pipeline(executor, pipeline_query):
                logger.error(f"Failed to create pipeline: {pipeline_name}")
                continue
            
            # Run pipeline if requested
            if not create_only:
                if not input_file:
                    logger.error("input_file required when create_only=False")
                    return False
                
                logger.info(f"Running pipeline: {pipeline_name} on {input_file}")
                if run_pipeline(executor, pipeline_name, input_file):
                    success_count += 1
                else:
                    logger.error(f"Failed to run pipeline: {pipeline_name}")
            else:
                logger.info(f"Pipeline {pipeline_name} created (not running)")
                success_count += 1
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Setup complete: {success_count}/{len(strategies)} pipelines processed")
        logger.info(f"{'='*60}")
        
        return success_count == len(strategies)
    
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        return False


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Setup regression test data")
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=list(PIPELINES.keys()),
        default=list(PIPELINES.keys()),
        help="Chunking strategies to use (default: all)"
    )
    parser.add_argument(
        "--input-file",
        type=str,
        help="Path to input PDF file (required if --create-only is not set)"
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="Only create pipelines without running them"
    )
    
    args = parser.parse_args()
    
    if not args.create_only and not args.input_file:
        parser.error("--input-file is required when --create-only is not set")
    
    success = setup_regression_data(
        strategies=args.strategies,
        input_file=args.input_file,
        create_only=args.create_only
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

