"""
Main CSV Cleaner - Orchestrates the entire LLM-driven cleaning process.
"""

import asyncio
from typing import List, Dict, Any, Optional, Union
from pathlib import Path
from loguru import logger
from datetime import datetime
import json

from .csv_reader import CSVReader
from .llm_client import LLMClient
from .rule_manager import RuleManager
from .validation_manager import ValidationManager
from ..processors.chunk_processor import ChunkProcessor
from ..processors.parallel_processor import ParallelProcessor
from ..processors.change_tracker import ChangeTracker, ChangeType, ConfidenceLevel
from ..utils.output_generator import OutputGenerator
from ..utils.error_handler import ErrorHandler, ErrorCategory, ErrorSeverity
from ..utils.validation_reporter import ValidationReporter
from ..utils.performance_monitor import PerformanceMonitor
from ..utils.memory_optimizer import MemoryOptimizer
from ..config.settings import settings


class CSVCleaner:
    """
    Main CSV cleaning orchestrator using LLM for all decisions.
    """
    
    def __init__(self):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Core components
        self.csv_reader = CSVReader()
        self.llm_client = LLMClient()
        self.rule_manager = RuleManager(self.llm_client)
        self.chunk_processor = ChunkProcessor(self.llm_client)
        self.parallel_processor = ParallelProcessor(self.llm_client)
        self.output_generator = OutputGenerator()
        
        # Tracking and reporting
        self.change_tracker = ChangeTracker(self.session_id)
        self.error_handler = ErrorHandler(self.session_id)
        self.validation_reporter = ValidationReporter(self.session_id)
        self.performance_monitor = PerformanceMonitor(self.session_id)
        self.memory_optimizer = MemoryOptimizer()
        
        # Centralized validation manager
        self.validation_manager = ValidationManager(self.llm_client)
        
        self.cleaning_results: Dict[str, Any] = {}
        
        logger.info(f"Initialized Enhanced CSV Cleaner session: {self.session_id}")
    
    async def clean_csv(
        self, 
        file_path: str, 
        user_rules: Union[List[str], str, Path],
        output_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Main method to clean a CSV file using LLM-driven approach.
        
        Args:
            file_path: Path to input CSV file
            user_rules: List of user-provided cleaning rules
            output_path: Optional output path (auto-generated if None)
            
        Returns:
            Complete cleaning results
        """
        try:
            start_time = datetime.now()
            logger.info(f"Starting CSV cleaning process for: {file_path}")
            
            # Start performance monitoring
            self.performance_monitor.start_monitoring()
            
            async with self.performance_monitor.profile_operation("csv_cleaning_complete", {"file_path": file_path, "rules_count": len(user_rules)}):
            
                # Step 1: Read and analyze CSV with memory optimization
                logger.info("Step 1: Reading and analyzing CSV file")
                async with self.performance_monitor.profile_operation("csv_reading", {"file_path": file_path}):
                    with self.memory_optimizer.memory_limit_context("csv_reading"):
                        self.csv_reader.read_csv(file_path)
                        file_info = self.csv_reader.get_file_info()
                        
                        # Optimize DataFrame memory usage
                        if self.csv_reader.df is not None:
                            self.csv_reader.df = self.memory_optimizer.optimize_dataframe(self.csv_reader.df)
                        
                        sample_data = self.csv_reader.get_sample_data()
                
                # Record data volume metrics
                self.performance_monitor.record_data_volume_metrics(
                    file_info.get("total_records", 0),
                    file_info.get("file_size_mb", 0)
                )
            
                # Step 2: Analyze data quality via LLM
                logger.info("Step 2: Analyzing data quality via LLM")
                async with self.performance_monitor.profile_operation("quality_analysis"):
                    quality_analysis = await self.llm_client.analyze_data_quality(sample_data)
            
                # Step 3: Initialize and expand user rules (centralized)
                logger.info("Step 3: Initializing and expanding user rules")
                async with self.performance_monitor.profile_operation("rule_expansion"):
                    rule_initialization = await self.rule_manager.initialize_rules(
                        user_rules,
                        self.csv_reader.column_headers
                    )
            
            if rule_initialization.get("status") not in ["success", "warning"]:
                logger.error("Failed to initialize rules")
                return {
                    "status": "error",
                    "error": "Failed to initialize user rules",
                    "details": rule_initialization
                }
            
            expanded_rules = self.rule_manager.get_expanded_rules()
            
            # Step 4: Create and process chunks with optimization
            logger.info("Step 4: Creating data chunks for processing")
            async with self.performance_monitor.profile_operation("chunk_creation"):
                # Optimize chunk size based on memory
                estimated_record_size = file_info.get("memory_usage_mb", 10) * 1024 * 1024 / max(file_info.get("total_records", 1), 1)
                optimal_chunk_size = self.memory_optimizer.optimize_chunk_size(
                    file_info.get("total_records", 0),
                    estimated_record_size
                )
                
                chunks = self.csv_reader.create_chunks(chunk_size=optimal_chunk_size)
                
                logger.info(f"Created {len(chunks)} chunks with optimized size: {optimal_chunk_size}")
        
            logger.info("Step 5: Processing chunks via LLM")
            async with self.performance_monitor.profile_operation("chunk_processing", {"chunk_count": len(chunks)}):
                with self.memory_optimizer.memory_limit_context("chunk_processing"):
                    processing_results = await self.chunk_processor.process_all_chunks(
                        chunks, expanded_rules, self.csv_reader
                    )
                    
                    # Record chunk processing metrics
                    for i, (chunk_index, chunk_df) in enumerate(chunks):
                        success = i < processing_results.get("successful_chunks", 0)
                        processing_time = processing_results.get("processing_time_seconds", 0) / len(chunks)
                        self.performance_monitor.record_chunk_processing_metrics(
                            len(chunk_df), processing_time, success
                        )
            
            # Step 6: Enhanced specialized processing with new validators
            logger.info("Step 6: Performing comprehensive specialized validations")
            
            successful_results = self.chunk_processor.get_successful_results()
            
            # Comprehensive validation (replaces separate phone, geo, cross-field validation)
            logger.info("Step 6: Performing comprehensive validation")
            comprehensive_validation = await self.validation_manager.validate_comprehensive(
                data_records=sample_data.get("sample_data", []),
                column_headers=self.csv_reader.column_headers,
                user_rules=self.rule_manager.get_original_rules()
            )
            self.validation_reporter.add_validation_results("comprehensive_validation", comprehensive_validation)
            
            # Step 7: Generate comprehensive output with enhanced reporting
            logger.info("Step 7: Generating comprehensive output files and reports")
            output_path = output_path or self._generate_output_path(file_path)
            
            # Generate comprehensive validation report
            original_data = sample_data.get("sample_data", [])
            cleaned_data = []
            
            # Extract cleaned data from processing results
            for result in processing_results.get("results", []):
                if result.get("status") == "success":
                    result_data = result.get("result", {})
                    if isinstance(result_data, dict) and "cleaned_data" in result_data:
                        cleaned_data.extend(result_data.get("cleaned_data", []))
            
            # Generate comprehensive validation report
            validation_report = self.validation_reporter.generate_comprehensive_report(
                original_data,
                cleaned_data,
                processing_results,
                self.change_tracker,
                self.error_handler
            )
            
            output_results = await self.output_generator.generate_output(
                processing_results,
                output_path,
                {
                    "file_info": file_info,
                    "quality_analysis": quality_analysis,
                    "rule_initialization": rule_initialization,
                    "comprehensive_validation": comprehensive_validation,
                    "validation_report": validation_report,
                    "change_tracker": self.change_tracker.generate_change_report(),
                    "error_report": self.error_handler.get_error_report(),
                    "session_id": self.session_id
                }
            )
            
            # Note: All reports are now consolidated into summary.json by OutputGenerator
            # No separate export files needed
            
            end_time = datetime.now()
            total_time = (end_time - start_time).total_seconds()
            
            # Stop performance monitoring
            self.performance_monitor.stop_monitoring()
            
            # Compile final results
            self.cleaning_results = {
                    "status": "completed",
                    "session_id": self.session_id,
                    "input_file": file_path,
                    "output_files": output_results.get("output_files", {}),
                    "processing_summary": {
                        "total_time_seconds": total_time,
                        "records_processed": file_info.get("total_records", 0),
                        "chunks_processed": processing_results.get("successful_chunks", 0),
                        "chunks_failed": processing_results.get("failed_chunks", 0),
                        "success_rate": processing_results.get("success_rate", 0),
                        "total_changes": processing_results.get("total_changes_made", 0)
                    },
                    "quality_analysis": quality_analysis,
                    "rule_processing": {
                        "original_rules": self.rule_manager.get_original_rules(),
                        "expanded_rules": expanded_rules,
                        "expansion_status": rule_initialization.get("status")
                    },
                    "specialized_processing": {
                        "comprehensive_validation": comprehensive_validation
                    },
                    "validation_report": validation_report,
                    "change_summary": self.change_tracker.get_change_statistics(),
                    "error_summary": self.error_handler.get_error_statistics(),
                    "performance_summary": self.performance_monitor.get_performance_summary(),
                    "memory_usage": self.memory_optimizer.get_memory_usage(),
                    "detailed_results": processing_results,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat()
            }
            
            logger.info(f"CSV cleaning completed successfully in {total_time:.2f} seconds")
            logger.info(f"Processed {file_info.get('total_records', 0)} records in {processing_results.get('successful_chunks', 0)} chunks")
            
            return self.cleaning_results
            
        except Exception as e:
            # Handle error through the error handler
            error_result = await self.error_handler.handle_error(
                error=e,
                context={
                    "function": "clean_csv",
                    "input_file": file_path,
                    "processing_stage": "main_processing"
                },
                category=ErrorCategory.SYSTEM_ERROR,
                severity=ErrorSeverity.CRITICAL
            )
            
            logger.error(f"Critical error in CSV cleaning process: {str(e)}")
            
            return {
                "status": "error",
                "session_id": self.session_id,
                "error": str(e),
                "error_handling_result": error_result,
                "input_file": file_path,
                "timestamp": datetime.now().isoformat(),
                "error_report": self.error_handler.get_error_report()
            }
    
    async def preview_cleaning(
        self, 
        file_path: str, 
        user_rules: List[str],
        preview_rows: int = 10
    ) -> Dict[str, Any]:
        """
        Preview cleaning results on a small sample without processing the entire file.
        
        Args:
            file_path: Path to input CSV file
            user_rules: List of user-provided cleaning rules
            preview_rows: Number of rows to preview (default 10)
            
        Returns:
            Preview results
        """
        try:
            logger.info(f"Starting cleaning preview for: {file_path}")
            
            # Read file and get sample
            self.csv_reader.read_csv(file_path)
            sample_data = self.csv_reader.get_sample_data(preview_rows)
            
            # Expand rules
            rule_initialization = await self.rule_manager.initialize_rules(
                user_rules,
                self.csv_reader.column_headers
            )
            
            if rule_initialization.get("status") not in ["success", "warning"]:
                return {
                    "status": "error",
                    "error": "Failed to initialize rules for preview",
                    "details": rule_initialization
                }
            
            # Test expanded rules on sample
            expanded_rules = self.rule_manager.get_expanded_rules()
            if expanded_rules:
                # Use LLM to test the expanded rules on sample data
                test_result = await self.llm_client.clean_data_chunk(
                    {"sample_data": sample_data}, 
                    expanded_rules
                )
                preview_result = {
                    "status": "success" if test_result.get("status") == "success" else "partial",
                    "sample_cleaning": test_result,
                    "expanded_rules_used": expanded_rules
                }
            else:
                preview_result = {
                    "status": "warning",
                    "message": "No expanded rules available for testing"
                }
            
            return {
                "status": "success",
                "preview_data": sample_data,
                "rule_initialization": rule_initialization,
                "preview_result": preview_result,
                "file_info": self.csv_reader.get_file_info()
            }
            
        except Exception as e:
            logger.error(f"Error in cleaning preview: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_session_info(self) -> Dict[str, Any]:
        """
        Get information about the current cleaning session.
        
        Returns:
            Session information
        """
        return {
            "session_id": self.session_id,
            "has_loaded_file": self.csv_reader.df is not None,
            "file_info": self.csv_reader.get_file_info() if self.csv_reader.df is not None else None,
            "current_rules": self.rule_processor.get_rules_summary(),
            "processing_stats": self.chunk_processor.get_processing_statistics(),
            "has_results": bool(self.cleaning_results)
        }
    
    def get_results_summary(self) -> Dict[str, Any]:
        """
        Get summary of the last cleaning results.
        
        Returns:
            Results summary
        """
        if not self.cleaning_results:
            return {"status": "no_results", "message": "No cleaning results available"}
        
        return {
            "status": self.cleaning_results.get("status"),
            "session_id": self.cleaning_results.get("session_id"),
            "processing_summary": self.cleaning_results.get("processing_summary"),
            "output_files": self.cleaning_results.get("output_files"),
            "rule_count": len(self.cleaning_results.get("rule_processing", {}).get("original_rules", [])),
            "specialized_processing_status": {
                "geo_validation": self.cleaning_results.get("specialized_processing", {}).get("geographical_validation", {}).get("status"),
                "phone_normalization": self.cleaning_results.get("specialized_processing", {}).get("phone_normalization", {}).get("status")
            }
        }
    
    def _generate_output_path(self, input_path: str) -> str:
        """
        Generate output path based on input path and session ID.
        
        Args:
            input_path: Input file path
            
        Returns:
            Generated output path
        """
        input_file = Path(input_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"{input_file.stem}_cleaned_{timestamp}"
        output_path = f"output/{output_name}"
        
        # Create output directory
        Path("output").mkdir(exist_ok=True)
        
        return output_path
    
    def reset_session(self) -> None:
        """Reset the cleaning session and clear all data."""
        self.csv_reader = CSVReader()
        self.rule_processor.clear_rules()
        self.chunk_processor.clear_results()
        self.cleaning_results.clear()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Reset cleaning session: {self.session_id}")
