"""
Columnar Storage V2 Implementation
Enhanced version with better performance and features
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Union, Tuple
import logging
from pathlib import Path
import json
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime

logger = logging.getLogger(__name__)

class ColumnarStoreV2:
    """Enhanced columnar storage with PyArrow backend."""
    
    def __init__(self, storage_path: str = "contextcore_data/parquet"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.tables: Dict[str, pa.Table] = {}
        self.schemas: Dict[str, pa.Schema] = {}
        
    def create_table(self, table_name: str, schema: Dict[str, str]) -> bool:
        """Create a new table with specified schema."""
        try:
            # Convert schema to PyArrow schema
            pa_schema = self._convert_schema(schema)
            self.schemas[table_name] = pa_schema
            
            # Create empty table
            empty_data = {col: [] for col in schema.keys()}
            self.tables[table_name] = pa.table(empty_data, schema=pa_schema)
            
            # Save to disk
            self._save_table(table_name)
            logger.info(f"Created table '{table_name}' with enhanced schema: {schema}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create table '{table_name}': {e}")
            return False
    
    def insert_data(self, table_name: str, data: List[Dict[str, Any]]) -> bool:
        """Insert data into table with batch processing."""
        try:
            if table_name not in self.tables:
                logger.error(f"Table '{table_name}' does not exist")
                return False
            
            # Convert to PyArrow table
            new_table = pa.table(data, schema=self.schemas[table_name])
            
            # Concatenate tables
            self.tables[table_name] = pa.concat_tables([self.tables[table_name], new_table])
            
            # Save to disk
            self._save_table(table_name)
            logger.info(f"Inserted {len(data)} rows into table '{table_name}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to insert data into table '{table_name}': {e}")
            return False
    
    def query(self, table_name: str, conditions: Optional[Dict[str, Any]] = None,
              columns: Optional[List[str]] = None) -> pa.Table:
        """Query table with optional conditions and column selection."""
        try:
            if table_name not in self.tables:
                logger.error(f"Table '{table_name}' does not exist")
                return pa.table({})
            
            table = self.tables[table_name]
            
            # Column selection
            if columns:
                table = table.select(columns)
            
            # Apply conditions
            if conditions:
                for column, value in conditions.items():
                    if column in table.column_names:
                        mask = table[column] == value
                        table = table.filter(mask)
            
            return table
            
        except Exception as e:
            logger.error(f"Failed to query table '{table_name}': {e}")
            return pa.table({})
    
    def aggregate(self, table_name: str, group_by: List[str], 
                  aggregations: Dict[str, str]) -> pa.Table:
        """Perform aggregations using PyArrow compute functions."""
        try:
            if table_name not in self.tables:
                logger.error(f"Table '{table_name}' does not exist")
                return pa.table({})
            
            table = self.tables[table_name]
            
            # Convert to pandas for aggregation (PyArrow compute is limited)
            df = table.to_pandas()
            result_df = df.groupby(group_by).agg(aggregations).reset_index()
            
            return pa.table(result_df)
            
        except Exception as e:
            logger.error(f"Failed to aggregate table '{table_name}': {e}")
            return pa.table({})
    
    def _convert_schema(self, schema: Dict[str, str]) -> pa.Schema:
        """Convert string schema to PyArrow schema."""
        pa_types = {
            'string': pa.string(),
            'int': pa.int64(),
            'float': pa.float64(),
            'bool': pa.bool_(),
            'timestamp': pa.timestamp('us')
        }
        
        fields = []
        for col_name, col_type in schema.items():
            pa_type = pa_types.get(col_type, pa.string())
            fields.append(pa.field(col_name, pa_type))
        
        return pa.schema(fields)
    
    def _save_table(self, table_name: str):
        """Save table to disk as Parquet file using PyArrow."""
        try:
            file_path = self.storage_path / f"{table_name}.parquet"
            pq.write_table(self.tables[table_name], file_path)
        except Exception as e:
            logger.error(f"Failed to save table '{table_name}': {e}")
    
    def _load_table(self, table_name: str) -> bool:
        """Load table from disk using PyArrow."""
        try:
            file_path = self.storage_path / f"{table_name}.parquet"
            if file_path.exists():
                self.tables[table_name] = pq.read_table(file_path)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to load table '{table_name}': {e}")
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get enhanced storage statistics."""
        total_rows = sum(table.num_rows for table in self.tables.values())
        total_size = sum(table.nbytes for table in self.tables.values())
        
        return {
            'total_tables': len(self.tables),
            'table_names': list(self.tables.keys()),
            'total_rows': total_rows,
            'total_size_bytes': total_size,
            'storage_path': str(self.storage_path),
            'backend': 'pyarrow'
        }













