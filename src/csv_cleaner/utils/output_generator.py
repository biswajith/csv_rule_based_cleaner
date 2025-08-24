"""
Output Generator - Creates user-friendly data reports and essential outputs.
"""

import json
import pandas as pd
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime
from loguru import logger

from ..config.settings import settings



class OutputGenerator:
    """
    Generates user-friendly data reports and essential JSON output.
    """
    
    def __init__(self):
        self.output_files: Dict[str, str] = {}
    
    async def generate_output(
        self, 
        processing_results: Dict[str, Any],
        output_path: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate essential output files: cleaned data and user-friendly reports.
        
        Args:
            processing_results: Results from chunk processing
            output_path: Base output path (without extension)
            metadata: Additional metadata to include
            
        Returns:
            Information about generated files
        """
        try:
            logger.info(f"Generating output files at: {output_path}")
            
            # Create output directory
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            
            generated_files = {}
            session_id = metadata.get("session_id", "unknown")
            
            # Generate cleaned CSV file (main output)
            csv_file = await self._generate_cleaned_csv(
                processing_results, f"{output_path}_cleaned.csv", metadata
            )
            generated_files["cleaned_csv"] = csv_file
            
            # Generate cleaned data JSON (for programmatic use)
            json_file = await self._generate_cleaned_data_json(
                processing_results, f"{output_path}.json", metadata
            )
            generated_files["cleaned_data"] = json_file
            

            
            self.output_files = generated_files
            
            logger.info(f"Generated {len(generated_files)} output files")
            return {
                "status": "success",
                "output_files": generated_files,
                "output_directory": str(output_dir)
            }
            
        except Exception as e:
            logger.error(f"Error generating output: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _generate_cleaned_data_json(
        self, 
        processing_results: Dict[str, Any], 
        file_path: str,
        metadata: Dict[str, Any]
    ) -> str:
        """
        Generate simple JSON output with cleaned data.
        
        Args:
            processing_results: Processing results
            file_path: Output file path
            metadata: Additional metadata
            
        Returns:
            Path to generated file
        """
        try:
            # Collect all cleaned data from successful chunks
            cleaned_records = []
            total_changes = 0
            
            for result in processing_results.get("results", []):
                if result.get("status") == "success":
                    cleaned_data = result.get("cleaned_data", "")
                    changes = result.get("changes", [])
                    total_changes += len(changes)
                    
                    # Parse cleaned data
                    if isinstance(cleaned_data, str) and cleaned_data.strip():
                        lines = cleaned_data.strip().split('\n')
                        if len(lines) > 1:  # Has header and data
                            headers = lines[0].split(',')
                            for line in lines[1:]:
                                values = line.split(',')
                                if len(values) == len(headers):
                                    record = dict(zip(headers, values))
                                    cleaned_records.append(record)
                    elif isinstance(cleaned_data, list):
                        cleaned_records.extend(cleaned_data)
            
            # Create simple output structure
            output_data = {
                "session_info": {
                    "session_id": metadata.get("session_id"),
                    "generated_at": datetime.now().isoformat(),
                    "input_file": metadata.get("file_info", {}).get("file_path"),
                    "success_rate": processing_results.get("success_rate", 0),
                    "total_changes_made": total_changes
                },
                "cleaned_data": cleaned_records
            }
            
            # Write JSON file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Generated cleaned data JSON: {file_path} ({len(cleaned_records)} records)")
            return file_path
            
        except Exception as e:
            logger.error(f"Error generating cleaned data JSON: {str(e)}")
            raise
    
    async def _generate_cleaned_csv(
        self, 
        processing_results: Dict[str, Any], 
        file_path: str,
        metadata: Dict[str, Any]
    ) -> str:
        """
        Generate cleaned CSV file that users can open directly.
        
        Args:
            processing_results: Processing results
            file_path: Output CSV file path
            metadata: Additional metadata
            
        Returns:
            Path to generated CSV file
        """
        try:
            # Collect all cleaned data from successful chunks
            all_cleaned_records = []
            
            for result in processing_results.get("results", []):
                if result.get("status") == "success":
                    cleaned_data = result.get("cleaned_data", "")
                    
                    # Parse cleaned data
                    if isinstance(cleaned_data, str) and cleaned_data.strip():

                        # Convert CSV string back to records using pandas for proper CSV parsing
                        try:
                            # Use pandas to parse CSV properly (handles quotes, commas in fields)
                            import io
                            csv_buffer = io.StringIO(cleaned_data.strip())
                            parsed_df = pd.read_csv(csv_buffer)
                            
                            # Convert DataFrame to list of dictionaries
                            for _, row in parsed_df.iterrows():
                                record = row.to_dict()
                                all_cleaned_records.append(record)
                                
                        except Exception as parse_error:
                            logger.warning(f"Pandas CSV parsing failed, falling back to simple parsing: {parse_error}")
                            # Fallback to simple parsing
                            lines = cleaned_data.strip().split('\n')
                            if len(lines) > 1:  # Has header and data
                                headers = [h.strip().strip('"') for h in lines[0].split(',')]
                                for line in lines[1:]:
                                    # Simple CSV parsing (handles basic cases)
                                    values = [v.strip().strip('"') for v in line.split(',')]
                                    if len(values) == len(headers):
                                        record = dict(zip(headers, values))
                                        all_cleaned_records.append(record)
                    elif isinstance(cleaned_data, list):
                        # Already in record format
                        all_cleaned_records.extend(cleaned_data)
            
            # Log the number of records found
            logger.info(f"Parsed {len(all_cleaned_records)} records from LLM response")
            
            # Validate record count against expected total
            expected_total = metadata.get("file_info", {}).get("total_records", 0)
            if expected_total > 0 and len(all_cleaned_records) != expected_total:
                logger.warning(f"FINAL RECORD COUNT MISMATCH: Expected {expected_total} records, got {len(all_cleaned_records)} records")
                logger.warning("Some records may have been dropped during the cleaning process")
            
            if not all_cleaned_records:
                # No successful cleaning - return original data if available
                logger.warning("No cleaned data available, attempting to return original data")
                return await self._generate_fallback_csv(file_path, metadata)
            
            # Convert to DataFrame and save as CSV
            df = pd.DataFrame(all_cleaned_records)
            
            # Save as CSV
            df.to_csv(file_path, index=False, encoding='utf-8')
            
            logger.info(f"Generated cleaned CSV: {file_path} ({len(all_cleaned_records)} records)")
            return file_path
            
        except Exception as e:
            logger.error(f"Error generating cleaned CSV: {str(e)}")
            # Try to generate fallback CSV
            return await self._generate_fallback_csv(file_path, metadata)
    
    async def _generate_fallback_csv(
        self, 
        file_path: str, 
        metadata: Dict[str, Any]
    ) -> str:
        """Generate a fallback CSV file when cleaning fails."""
        try:
            # Create a simple message file
            fallback_content = "# CLEANING FAILED\n"
            fallback_content += f"# Original file: {metadata.get('file_info', {}).get('file_path', 'Unknown')}\n"
            fallback_content += f"# Session: {metadata.get('session_id', 'Unknown')}\n"
            fallback_content += "# No cleaned data could be generated\n"
            fallback_content += "# Please check the error logs and data changes report\n"
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(fallback_content)
            
            logger.warning(f"Generated fallback file: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error generating fallback CSV: {str(e)}")
            raise
    

