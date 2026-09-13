"""
Example Usage of AIQL Extraction

Demonstrates how to use the extraction subsystem programmatically.
"""

from contextsynapse.extraction import (
    ExtractionConfig,
    ExtractionMode,
    run_extraction,
    extract_many_documents_with_ray
)


def example_single_file():
    """Example: Extract a single file."""
    config = ExtractionConfig(
        file_path="document.pdf",
        reader="AUTO",
        detect=["TEXT", "TABLES", "IMAGES"],
        pages_mode=ExtractionMode.FULL,
        parse_metadata=True,
        output_dir="contextcore_data"
    )
    
    document_id = run_extraction(config)
    print(f"Extraction complete! Document ID: {document_id}")


def example_page_range():
    """Example: Extract specific page range."""
    config = ExtractionConfig(
        file_path="document.pdf",
        reader="pdf_v1",
        detect=["TEXT", "TABLES"],
        pages_mode=ExtractionMode.RANGE,
        pages_range=(1, 10),  # Pages 1-10
        output_dir="contextcore_data"
    )
    
    document_id = run_extraction(config)
    print(f"Extraction complete! Document ID: {document_id}")


def example_page_list():
    """Example: Extract specific pages."""
    config = ExtractionConfig(
        file_path="document.pdf",
        reader="AUTO",
        detect=["TEXT"],
        pages_mode=ExtractionMode.LIST,
        pages_list=[1, 3, 5, 7],  # Only these pages
        output_dir="contextcore_data"
    )
    
    document_id = run_extraction(config)
    print(f"Extraction complete! Document ID: {document_id}")


def example_batch_with_ray():
    """Example: Extract multiple files with Ray."""
    configs = [
        ExtractionConfig(
            file_path=f"document_{i}.pdf",
            reader="AUTO",
            detect=["TEXT", "TABLES"],
            output_dir="contextcore_data"
        )
        for i in range(1, 11)  # 10 documents
    ]
    
    results = extract_many_documents_with_ray(configs, num_cpus=4)
    
    successful = sum(1 for r in results if r["success"])
    print(f"Batch extraction complete: {successful}/{len(results)} successful")


if __name__ == "__main__":
    print("AIQL Extraction Examples")
    print("=" * 50)
    
    # Uncomment to run examples:
    # example_single_file()
    # example_page_range()
    # example_page_list()
    # example_batch_with_ray()
































