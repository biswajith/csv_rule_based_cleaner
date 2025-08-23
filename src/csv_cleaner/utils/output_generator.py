"""
Output Generator - Creates JSON output and detailed change logs.
"""

import json
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime
from loguru import logger

from ..config.settings import settings


class OutputGenerator:
    """
    Generates output files and change logs from LLM processing results.
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
        Generate consolidated output files: cleaned_data.json and summary.json.
        
        Args:
            processing_results: Results from chunk processing
            output_path: Base output path (without extension)
            metadata: Additional metadata to include
            
        Returns:
            Information about generated files
        """
        try:
            logger.info(f"Generating consolidated output files at: {output_path}")
            
            # Create output directory
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            
            generated_files = {}
            
            # Generate cleaned data JSON (always generated)
            json_file = await self._generate_json_output(
                processing_results, f"{output_path}.json", metadata
            )
            generated_files["cleaned_data"] = json_file
            
            # Generate consolidated summary report (includes all metadata)
            summary_file = await self._generate_consolidated_summary(
                processing_results, metadata, f"{output_path}_summary.json"
            )
            generated_files["summary"] = summary_file
            
            self.output_files = generated_files
            
            logger.info(f"Generated {len(generated_files)} consolidated output files")
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
    
    async def _generate_json_output(
        self, 
        processing_results: Dict[str, Any], 
        file_path: str,
        metadata: Dict[str, Any]
    ) -> str:
        """
        Generate JSON output with cleaned data.
        
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
                    
                    # Parse cleaned data (assuming it's CSV format from LLM)
                    try:
                        # Convert CSV string to records if needed
                        if isinstance(cleaned_data, str) and cleaned_data.strip():
                            lines = cleaned_data.strip().split('\n')
                            if len(lines) > 1:  # Has header and data
                                headers = lines[0].split(',')
                                for line in lines[1:]:
                                    values = line.split(',')
                                    if len(values) == len(headers):
                                        record = dict(zip(headers, values))
                                        record['_chunk_index'] = result.get("chunk_index")
                                        cleaned_records.append(record)
                        elif isinstance(cleaned_data, list):
                            # Already in record format
                            for record in cleaned_data:
                                record['_chunk_index'] = result.get("chunk_index")
                                cleaned_records.append(record)
                    except Exception as parse_error:
                        logger.warning(f"Could not parse cleaned data from chunk {result.get('chunk_index')}: {parse_error}")
            
            # Create output structure
            output_data = {
                "metadata": {
                    "generated_at": datetime.now().isoformat(),
                    "session_id": metadata.get("session_id"),
                    "input_file": metadata.get("file_info", {}).get("file_path"),
                    "total_records": len(cleaned_records),
                    "total_changes_made": total_changes,
                    "processing_summary": processing_results.get("processing_time_seconds"),
                    "success_rate": processing_results.get("success_rate"),
                    "llm_model": settings.openai_model,
                    "chunk_size": settings.chunk_size
                },
                "cleaning_rules": {
                    "original_rules": metadata.get("rule_expansion", {}).get("original_rules", []),
                    "expanded_rules": metadata.get("rule_expansion", {}).get("expanded_rules", "")
                },
                "quality_analysis": metadata.get("quality_analysis", {}),
                "specialized_processing": {
                    "geographical_validation": metadata.get("geo_validation", {}),
                    "phone_normalization": metadata.get("phone_normalization", {})
                },
                "cleaned_data": cleaned_records
            }
            
            # Write JSON file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Generated JSON output: {file_path} ({len(cleaned_records)} records)")
            return file_path
            
        except Exception as e:
            logger.error(f"Error generating JSON output: {str(e)}")
            raise
    

    
    async def _generate_consolidated_summary(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any],
        file_path: str
    ) -> str:
        """
        Generate consolidated summary report with all metadata and analysis.
        
        Args:
            processing_results: Processing results
            metadata: Additional metadata
            file_path: Output file path
            
        Returns:
            Path to generated file
        """
        try:
            # Extract change log data
            change_log_data = self._extract_change_log_data(processing_results)
            
            summary = {
                "executive_summary": {
                    "session_id": metadata.get("session_id"),
                    "processed_at": datetime.now().isoformat(),
                    "input_file": metadata.get("file_info", {}).get("file_path"),
                    "total_records": metadata.get("file_info", {}).get("total_records", 0),
                    "successful_chunks": processing_results.get("successful_chunks", 0),
                    "failed_chunks": processing_results.get("failed_chunks", 0),
                    "success_rate": processing_results.get("success_rate", 0),
                    "processing_time_seconds": processing_results.get("processing_time_seconds", 0)
                },
                "data_quality_analysis": metadata.get("quality_analysis", {}),
                "rule_processing": {
                    "original_rules": metadata.get("rule_expansion", {}).get("original_rules", []),
                    "rule_expansion_status": metadata.get("rule_expansion", {}).get("status"),
                    "expanded_rules_preview": metadata.get("rule_expansion", {}).get("expanded_rules", "")[:500] + "..." if len(metadata.get("rule_expansion", {}).get("expanded_rules", "")) > 500 else metadata.get("rule_expansion", {}).get("expanded_rules", "")
                },
                "specialized_processing": {
                    "comprehensive_validation": metadata.get("comprehensive_validation", {})
                },
                "performance_metrics": {
                    "chunks_per_second": processing_results.get("chunks_per_second", 0),
                    "records_per_second": (metadata.get("file_info", {}).get("total_records", 0) / processing_results.get("processing_time_seconds", 1)) if processing_results.get("processing_time_seconds", 0) > 0 else 0,
                    "llm_model_used": settings.openai_model,
                    "chunk_size": settings.chunk_size,
                    "max_concurrent_requests": settings.max_concurrent_requests
                },
                "failure_analysis": processing_results.get("failures", []),
                
                # Consolidated data from previously separate files
                "detailed_change_log": change_log_data,
                "validation_report": metadata.get("validation_report", {}),
                "change_tracking_report": metadata.get("change_tracker", {}),
                "error_report": metadata.get("error_report", {}),
                "performance_report": {
                    "summary": "Performance data consolidated from performance monitor",
                    "processing_time_breakdown": processing_results.get("processing_time_seconds", 0),
                    "chunk_processing_stats": {
                        "successful_chunks": processing_results.get("successful_chunks", 0),
                        "failed_chunks": processing_results.get("failed_chunks", 0),
                        "chunks_per_second": processing_results.get("chunks_per_second", 0)
                    }
                }
            }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Generated consolidated summary report: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error generating consolidated summary report: {str(e)}")
            raise
    

    
    def _extract_change_log_data(self, processing_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract and consolidate change log data from processing results.
        
        Args:
            processing_results: Processing results
            
        Returns:
            Consolidated change log data
        """
        try:
            all_changes = []
            
            for result in processing_results.get("results", []):
                if result.get("status") == "success":
                    chunk_changes = result.get("changes", [])
                    chunk_index = result.get("chunk_index", 0)
                    
                    for change in chunk_changes:
                        change_entry = {
                            "chunk_index": chunk_index,
                            "record_index": change.get("record_index"),
                            "field": change.get("field"),
                            "original_value": change.get("original_value"),
                            "cleaned_value": change.get("cleaned_value"),
                            "reason": change.get("reason"),
                            "confidence": change.get("confidence", "high"),
                            "timestamp": datetime.now().isoformat()
                        }
                        all_changes.append(change_entry)
            
            return {
                "metadata": {
                    "generated_at": datetime.now().isoformat(),
                    "total_changes": len(all_changes),
                    "chunks_processed": processing_results.get("successful_chunks", 0),
                    "processing_time": processing_results.get("processing_time_seconds", 0)
                },
                "change_summary": self._summarize_changes(all_changes),
                "detailed_changes": all_changes
            }
        except Exception as e:
            logger.warning(f"Error extracting change log data: {e}")
            return {"metadata": {"error": str(e)}, "change_summary": {}, "detailed_changes": []}
    
    def _summarize_changes(self, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create summary statistics from change log.
        
        Args:
            changes: List of change entries
            
        Returns:
            Change summary statistics
        """
        if not changes:
            return {"total_changes": 0}
        
        # Group changes by field
        field_changes = {}
        reason_counts = {}
        confidence_distribution = {"high": 0, "medium": 0, "low": 0}
        
        for change in changes:
            field = change.get("field", "unknown")
            reason = change.get("reason", "unspecified")
            confidence = change.get("confidence", "high")
            
            field_changes[field] = field_changes.get(field, 0) + 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            
            if confidence in confidence_distribution:
                confidence_distribution[confidence] += 1
        
        return {
            "total_changes": len(changes),
            "changes_by_field": field_changes,
            "changes_by_reason": reason_counts,
            "confidence_distribution": confidence_distribution,
            "most_changed_field": max(field_changes, key=field_changes.get) if field_changes else None,
            "most_common_reason": max(reason_counts, key=reason_counts.get) if reason_counts else None
        }
