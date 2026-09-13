"""
Excel Extractor

Extracts content from Excel (.xlsx, .xls) files using openpyxl.
Returns each sheet as a structured table in ExtractionResult.
"""

from pathlib import Path
from typing import Any, Dict
import logging

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class ExcelExtractor(BaseExtractor):
    """
    Excel extractor using openpyxl for .xlsx files.
    """

    def __init__(self, config):
        super().__init__(config)
        self.extractor_version = "excel_v1"

    def supports_format(self, file_path: str) -> bool:
        suffix = Path(file_path).suffix.lower()
        return suffix in (".xlsx", ".xls")

    def extract(self, file_path: str) -> ExtractionResult:
        result = ExtractionResult(extractor_version=self.extractor_version)

        try:
            import openpyxl
        except ImportError:
            try:
                import pandas as pd
                return self._extract_with_pandas(file_path, result)
            except ImportError:
                result.errors.append("Neither openpyxl nor pandas available for Excel extraction")
                return result

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            total_rows = 0

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append([self._cell_value(c) for c in row])

                if not rows:
                    continue

                # First non-empty row as headers
                headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
                data_rows = rows[1:]
                total_rows += len(data_rows)

                result.tables.append({
                    "id": f"sheet_{sheet_name}",
                    "sheet_name": sheet_name,
                    "headers": headers,
                    "rows": data_rows,
                    "row_count": len(data_rows),
                })

                # Text representation for search/embedding
                text_lines = [" | ".join(headers)]
                for row in data_rows[:50]:  # cap text preview at 50 rows
                    text_lines.append(" | ".join(str(c) if c is not None else "" for c in row))

                result.pages.append({
                    "page_no": wb.sheetnames.index(sheet_name) + 1,
                    "sheet_name": sheet_name,
                    "text": "\n".join(text_lines),
                    "content": "\n".join(text_lines),
                })

            wb.close()

            result.metadata = {
                "title": Path(file_path).stem,
                "sheet_count": len(wb.sheetnames),
                "sheet_names": wb.sheetnames,
                "total_rows": total_rows,
                "file_type": "excel",
            }

        except Exception as e:
            logger.error("Excel extraction failed: %s", e)
            result.errors.append(str(e))

        return result

    def _extract_with_pandas(self, file_path: str, result: ExtractionResult) -> ExtractionResult:
        """Fallback extraction using pandas."""
        import pandas as pd

        try:
            xls = pd.ExcelFile(file_path)
            total_rows = 0

            for sheet_name in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet_name)
                headers = [str(c) for c in df.columns.tolist()]
                data_rows = df.where(df.notna(), None).values.tolist()
                total_rows += len(data_rows)

                result.tables.append({
                    "id": f"sheet_{sheet_name}",
                    "sheet_name": sheet_name,
                    "headers": headers,
                    "rows": [[self._cell_value(c) for c in row] for row in data_rows],
                    "row_count": len(data_rows),
                })

                text_lines = [" | ".join(headers)]
                for row in data_rows[:50]:
                    text_lines.append(" | ".join(str(c) if c is not None else "" for c in row))

                result.pages.append({
                    "page_no": xls.sheet_names.index(sheet_name) + 1,
                    "sheet_name": sheet_name,
                    "text": "\n".join(text_lines),
                    "content": "\n".join(text_lines),
                })

            result.metadata = {
                "title": Path(file_path).stem,
                "sheet_count": len(xls.sheet_names),
                "sheet_names": xls.sheet_names,
                "total_rows": total_rows,
                "file_type": "excel",
            }

        except Exception as e:
            logger.error("Excel pandas extraction failed: %s", e)
            result.errors.append(str(e))

        return result

    @staticmethod
    def _cell_value(val: Any) -> Any:
        """Normalize cell values for JSON serialization."""
        if val is None:
            return None
        if isinstance(val, (int, float, bool, str)):
            return val
        # datetime, date, etc.
        return str(val)
