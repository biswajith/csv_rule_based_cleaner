"""
Data Processor - High-level interface for data processing operations.
"""

from typing import Dict, List, Any, Optional, Union
from loguru import logger

from .cleaner import CSVCleaner
from pathlib import Path


class DataProcessor:
    """
    High-level data processing interface.
    Provides simplified access to CSV cleaning functionality.
    """
    
    def __init__(self):
        self.cleaner = CSVCleaner()
    
    async def process_file(
        self, 
        input_file: str, 
        rules: Union[List[str], str, Path],
        output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a CSV file with the given rules.
        
        Args:
            input_file: Path to input CSV file
            rules: List of cleaning rules, path to rules file, or Path object
            output_file: Optional output file path
            
        Returns:
            Processing results
        """
        logger.info(f"Processing file: {input_file}")
        return await self.cleaner.clean_csv(input_file, rules, output_file)
    
    async def preview_file(
        self, 
        input_file: str, 
        rules: Union[List[str], str, Path],
        preview_rows: int = 10
    ) -> Dict[str, Any]:
        """
        Preview processing results on a sample.
        
        Args:
            input_file: Path to input CSV file
            rules: List of cleaning rules, path to rules file, or Path object
            preview_rows: Number of rows to preview
            
        Returns:
            Preview results
        """
        logger.info(f"Previewing file: {input_file}")
        return await self.cleaner.preview_cleaning(input_file, rules, preview_rows)
    
    def get_session_info(self) -> Dict[str, Any]:
        """Get current session information."""
        return self.cleaner.get_session_info()
    
    def get_results_summary(self) -> Dict[str, Any]:
        """Get summary of last processing results."""
        return self.cleaner.get_results_summary()
    
    def reset_session(self) -> None:
        """Reset the processing session."""
        self.cleaner.reset_session()
