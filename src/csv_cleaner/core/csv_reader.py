"""
CSV Reader and Analyzer - Pure data reading without any hardcoded validation.
All analysis and validation decisions are made by the LLM.
"""

import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from loguru import logger

from ..config.settings import settings


class CSVReader:
    """
    Handles CSV file reading and basic structure analysis.
    No hardcoded validation - all decisions delegated to LLM.
    """
    
    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.file_path: Optional[Path] = None
        self.column_headers: List[str] = []
        self.total_records: int = 0
    
    def read_csv(self, file_path: str, **kwargs) -> pd.DataFrame:
        """
        Read CSV file with flexible options.
        
        Args:
            file_path: Path to CSV file
            **kwargs: Additional pandas read_csv parameters
            
        Returns:
            DataFrame with CSV data
        """
        try:
            self.file_path = Path(file_path)
            logger.info(f"Reading CSV file: {file_path}")
            
            # Default reading parameters
            default_params = {
                'encoding': 'utf-8',
                'low_memory': False,
                'skipinitialspace': True
            }
            default_params.update(kwargs)
            
            self.df = pd.read_csv(file_path, **default_params)
            self.column_headers = list(self.df.columns)
            self.total_records = len(self.df)
            
            logger.info(f"Successfully loaded {self.total_records} records with {len(self.column_headers)} columns")
            logger.debug(f"Column headers: {self.column_headers}")
            
            return self.df
            
        except Exception as e:
            logger.error(f"Error reading CSV file {file_path}: {str(e)}")
            raise
    
    def get_file_info(self) -> Dict[str, Any]:
        """
        Get basic information about the loaded CSV file.
        
        Returns:
            Dictionary with file information
        """
        if self.df is None:
            raise ValueError("No CSV file loaded")
        
        return {
            'file_path': str(self.file_path),
            'total_records': self.total_records,
            'column_count': len(self.column_headers),
            'column_headers': self.column_headers,
            'file_size_mb': self.file_path.stat().st_size / (1024 * 1024) if self.file_path else 0,
            'memory_usage_mb': self.df.memory_usage(deep=True).sum() / (1024 * 1024)
        }
    
    def get_sample_data(self, n_rows: int = 5) -> Dict[str, Any]:
        """
        Get sample data for LLM analysis.
        
        Args:
            n_rows: Number of sample rows to return
            
        Returns:
            Dictionary with sample data and metadata
        """
        if self.df is None:
            raise ValueError("No CSV file loaded")
        
        sample_df = self.df.head(n_rows)
        
        return {
            'column_headers': self.column_headers,
            'sample_data': sample_df.to_dict('records'),
            'data_types': self.df.dtypes.astype(str).to_dict(),
            'null_counts': self.df.isnull().sum().to_dict(),
            'total_records': self.total_records
        }
    
    def create_chunks(self, chunk_size: Optional[int] = None) -> List[Tuple[int, pd.DataFrame]]:
        """
        Split CSV data into chunks for processing.
        
        Args:
            chunk_size: Size of each chunk (uses config default if None)
            
        Returns:
            List of tuples (chunk_index, chunk_dataframe)
        """
        if self.df is None:
            raise ValueError("No CSV file loaded")
        
        chunk_size = chunk_size or settings.chunk_size
        chunks = []
        
        for i in range(0, len(self.df), chunk_size):
            chunk_df = self.df.iloc[i:i + chunk_size].copy()
            chunks.append((i // chunk_size, chunk_df))
        
        logger.info(f"Created {len(chunks)} chunks of size {chunk_size}")
        return chunks
    
    def get_chunk_with_headers(self, chunk_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Prepare chunk data with headers for LLM processing.
        
        Args:
            chunk_df: Chunk dataframe
            
        Returns:
            Dictionary with chunk data and headers
        """
        return {
            'headers': list(chunk_df.columns),
            'data': chunk_df.to_dict('records'),
            'chunk_size': len(chunk_df),
            'data_csv': chunk_df.to_csv(index=False)
        }
    
    def detect_potential_field_types(self) -> Dict[str, List[str]]:
        """
        Suggest potential field types based on column names for LLM guidance.
        No hardcoded rules - just suggestions for LLM to consider.
        
        Returns:
            Dictionary mapping potential types to column names
        """
        if self.df is None:
            raise ValueError("No CSV file loaded")
        
        # Simple keyword-based suggestions (not rules!)
        suggestions = {
            'potential_name_fields': [],
            'potential_email_fields': [],
            'potential_phone_fields': [],
            'potential_address_fields': [],
            'potential_geographic_fields': [],
            'potential_date_fields': [],
            'potential_numeric_fields': []
        }
        
        for col in self.column_headers:
            col_lower = col.lower()
            
            # Name field suggestions
            if any(keyword in col_lower for keyword in ['name', 'first', 'last', 'full']):
                suggestions['potential_name_fields'].append(col)
            
            # Email field suggestions
            if any(keyword in col_lower for keyword in ['email', 'mail', '@']):
                suggestions['potential_email_fields'].append(col)
            
            # Phone field suggestions
            if any(keyword in col_lower for keyword in ['phone', 'tel', 'mobile', 'cell']):
                suggestions['potential_phone_fields'].append(col)
            
            # Address field suggestions
            if any(keyword in col_lower for keyword in ['address', 'street', 'addr']):
                suggestions['potential_address_fields'].append(col)
            
            # Geographic field suggestions
            if any(keyword in col_lower for keyword in ['city', 'state', 'country', 'zip', 'postal']):
                suggestions['potential_geographic_fields'].append(col)
            
            # Date field suggestions
            if any(keyword in col_lower for keyword in ['date', 'time', 'created', 'updated']):
                suggestions['potential_date_fields'].append(col)
        
        # Numeric field suggestions based on data types
        numeric_cols = self.df.select_dtypes(include=['int64', 'float64']).columns.tolist()
        suggestions['potential_numeric_fields'] = numeric_cols
        
        return suggestions
