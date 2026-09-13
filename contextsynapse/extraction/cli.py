"""
CLI for AIQL Extraction

Command-line interface using typer for extraction operations.
"""

from typing import Optional, List
import typer
from pathlib import Path
import sys

from .config import ExtractionConfig, ExtractionMode
from .extract_engine import run_extraction
from .ray_runner import extract_many_documents_with_ray, extract_folder_with_ray, shutdown_ray

app = typer.Typer(
    name="aiql-extract",
    help="AIQL Extraction & Normalization Subsystem (Stage-1)",
    add_completion=False
)


@app.command()
def extract(
    file_path: str = typer.Argument(..., help="Path to file to extract"),
    reader: str = typer.Option("AUTO", "--reader", "-r", help="Extractor name (AUTO, pdf_v1, docx_v1, etc.)"),
    detect: Optional[str] = typer.Option(None, "--detect", "-d", help="Comma-separated modalities (TEXT,TABLES,IMAGES)"),
    full: bool = typer.Option(False, "--full", help="Extract all pages (default)"),
    page_range: Optional[str] = typer.Option(None, "--page-range", help="Page range (e.g., 1-10 or 1..10)"),
    page_list: Optional[str] = typer.Option(None, "--page-list", help="Comma-separated page numbers (e.g., 1,3,5)"),
    parse_metadata: bool = typer.Option(True, "--parse-metadata/--no-parse-metadata", help="Parse document metadata"),
    output_dir: str = typer.Option("contextcore_data", "--output-dir", "-o", help="Output directory"),
    document_id: Optional[str] = typer.Option(None, "--document-id", help="Document ID (auto-generated if not provided)"),
):
    """
    Extract content from a single file.
    
    Examples:
    
    \b
    # Full extraction
    aiql-extract document.pdf --full
    
    \b
    # Page range
    aiql-extract document.pdf --page-range 1-10
    
    \b
    # Page list
    aiql-extract document.pdf --page-list 1,3,5,7
    
    \b
    # With specific reader and modalities
    aiql-extract document.pdf --reader pdf_v1 --detect TEXT,TABLES,IMAGES
    """
    try:
        # Parse detect list
        detect_list = ["TEXT"]
        if detect:
            detect_list = [d.strip().upper() for d in detect.split(",")]
        
        # Determine extraction mode
        pages_mode = ExtractionMode.FULL
        pages_range = None
        pages_list = None
        
        if page_range:
            pages_mode = ExtractionMode.RANGE
            # Parse range (support both 1-10 and 1..10)
            if ".." in page_range:
                start, end = page_range.split("..")
            elif "-" in page_range:
                start, end = page_range.split("-")
            else:
                raise ValueError(f"Invalid page range format: {page_range}")
            pages_range = (int(start.strip()), int(end.strip()))
        
        elif page_list:
            pages_mode = ExtractionMode.LIST
            pages_list = [int(p.strip()) for p in page_list.split(",")]
        
        # Create config
        config = ExtractionConfig(
            file_path=file_path,
            reader=reader,
            detect=detect_list,
            pages_mode=pages_mode,
            pages_range=pages_range,
            pages_list=pages_list,
            parse_metadata=parse_metadata,
            output_dir=output_dir,
            document_id=document_id,
        )
        
        # Run extraction
        typer.echo(f"Extracting from: {file_path}")
        document_id = run_extraction(config)
        
        typer.echo(f"\n✓ Extraction complete!")
        typer.echo(f"Document ID: {document_id}")
        typer.echo(f"Output: {output_dir}/{document_id}/normalized/")
        
    except Exception as e:
        typer.echo(f"✗ Error: {e}", err=True)
        import traceback
        typer.echo(f"Traceback:", err=True)
        typer.echo(traceback.format_exc(), err=True)
        raise typer.Exit(code=1)


@app.command()
def extract_batch(
    folder_path: str = typer.Argument(..., help="Path to folder containing files"),
    reader: str = typer.Option("AUTO", "--reader", "-r", help="Extractor name"),
    detect: Optional[str] = typer.Option(None, "--detect", "-d", help="Comma-separated modalities"),
    use_ray: bool = typer.Option(True, "--use-ray/--no-ray", help="Use Ray for parallel processing"),
    output_dir: str = typer.Option("contextcore_data", "--output-dir", "-o", help="Output directory"),
    num_cpus: Optional[int] = typer.Option(None, "--num-cpus", help="Number of CPUs for Ray"),
):
    """
    Extract content from all supported files in a folder.
    
    Examples:
    
    \b
    # Extract all files in folder
    aiql-extract batch ./documents --use-ray
    
    \b
    # Without Ray (sequential)
    aiql-extract batch ./documents --no-ray
    """
    try:
        # Parse detect list
        detect_list = ["TEXT"]
        if detect:
            detect_list = [d.strip().upper() for d in detect.split(",")]
        
        if use_ray:
            typer.echo(f"Extracting files from: {folder_path} (using Ray)")
            results = extract_folder_with_ray(
                folder_path=folder_path,
                reader=reader,
                detect=detect_list,
                output_dir=output_dir,
                num_cpus=num_cpus
            )
            
            # Print summary
            successful = sum(1 for r in results if r["success"])
            failed = len(results) - successful
            
            typer.echo(f"\n✓ Batch extraction complete!")
            typer.echo(f"Successful: {successful}")
            typer.echo(f"Failed: {failed}")
            
            if failed > 0:
                typer.echo("\nFailed extractions:")
                for r in results:
                    if not r["success"]:
                        typer.echo(f"  - {r.get('document_id', 'unknown')}: {r.get('error', 'unknown error')}")
        else:
            typer.echo(f"Extracting files from: {folder_path} (sequential)")
            folder = Path(folder_path)
            if not folder.exists():
                raise FileNotFoundError(f"Folder not found: {folder_path}")
            
            supported_extensions = {
                ".pdf", ".docx", ".pptx", ".csv", ".txt", ".html", ".htm",
                ".png", ".jpg", ".jpeg", ".mp3", ".wav", ".mp4", ".avi"
            }
            
            files = [
                f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in supported_extensions
            ]
            
            if not files:
                typer.echo("No supported files found")
                return
            
            typer.echo(f"Found {len(files)} files")
            
            for i, file_path in enumerate(files, 1):
                typer.echo(f"\n[{i}/{len(files)}] Processing: {file_path.name}")
                try:
                    config = ExtractionConfig(
                        file_path=str(file_path),
                        reader=reader,
                        detect=detect_list,
                        output_dir=output_dir
                    )
                    document_id = run_extraction(config)
                    typer.echo(f"  ✓ {document_id}")
                except Exception as e:
                    typer.echo(f"  ✗ Error: {e}", err=True)
            
            typer.echo(f"\n✓ Batch extraction complete!")
        
    except Exception as e:
        typer.echo(f"✗ Error: {e}", err=True)
        import traceback
        typer.echo(f"Traceback:", err=True)
        typer.echo(traceback.format_exc(), err=True)
        raise typer.Exit(code=1)
    finally:
        if use_ray:
            shutdown_ray()


@app.command()
def list_extractors():
    """
    List all registered extractors.
    """
    from .registry import ExtractorRegistry
    
    extractors = ExtractorRegistry.list_registered()
    
    if not extractors:
        typer.echo("No extractors registered")
        return
    
    typer.echo("Registered extractors:")
    for name in extractors:
        typer.echo(f"  - {name}")


@app.command()
def show_version(
    document_id: str = typer.Argument(..., help="Document ID"),
    output_dir: str = typer.Option("contextcore_data", "--output-dir", "-o", help="Output directory"),
):
    """
    Show version information for a document.
    """
    from .file_manager import FileManager
    from .version_manager import VersionManager
    
    try:
        file_manager = FileManager(document_id, output_dir)
        version_manager = VersionManager(file_manager)
        
        versions = version_manager.list_versions()
        
        if not versions:
            typer.echo(f"No versions found for document: {document_id}")
            return
        
        typer.echo(f"Versions for document: {document_id}")
        for version in versions:
            info = version_manager.get_version_info(version)
            if info:
                typer.echo(f"\n{version}:")
                typer.echo(f"  Extractor: {info.get('extractor_version', 'unknown')}")
                typer.echo(f"  Mode: {info.get('pages_mode', 'unknown')}")
                typer.echo(f"  Pages: {info.get('extracted_pages', [])}")
                typer.echo(f"  Created: {info.get('created_at', 'unknown')}")
        
    except Exception as e:
        typer.echo(f"✗ Error: {e}", err=True)
        import traceback
        typer.echo(f"Traceback:", err=True)
        typer.echo(traceback.format_exc(), err=True)
        raise typer.Exit(code=1)


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
