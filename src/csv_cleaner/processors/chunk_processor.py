"""
Chunk Processor - Handles processing of data chunks through LLM.
All processing decisions are made by the LLM.
"""

import asyncio
from typing import List, Dict, Any, Tuple
from loguru import logger
import pandas as pd
from datetime import datetime

from ..core.llm_client import LLMClient
from ..core.csv_reader import CSVReader
from ..config.settings import settings


class ChunkProcessor:
    """
    Processes CSV data chunks through LLM-driven cleaning.
    No hardcoded processing logic - everything via LLM.
    """
    
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.processing_results: List[Dict[str, Any]] = []
        self.failed_chunks: List[Tuple[int, str]] = []
        self.total_chunks: int = 0
        self.processed_chunks: int = 0
    
    async def process_all_chunks(
        self, 
        chunks: List[Tuple[int, pd.DataFrame]], 
        expanded_rules: str,
        csv_reader: CSVReader
    ) -> Dict[str, Any]:
        """
        Process all data chunks using LLM cleaning.
        
        Args:
            chunks: List of (chunk_index, chunk_dataframe) tuples
            expanded_rules: LLM-expanded cleaning rules
            csv_reader: CSV reader instance for helper methods
            
        Returns:
            Processing results and statistics
        """
        self.total_chunks = len(chunks)
        self.processed_chunks = 0
        self.processing_results.clear()
        self.failed_chunks.clear()
        
        logger.info(f"Starting processing of {self.total_chunks} chunks")
        start_time = datetime.now()
        
        # Process chunks based on parallel processing flag
        if settings.enable_parallel_processing:
            logger.info("Processing chunks in parallel mode")
            # Process chunks with concurrency control
            semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
            
            async def process_single_chunk(chunk_info: Tuple[int, pd.DataFrame]) -> Dict[str, Any]:
                async with semaphore:
                    return await self._process_chunk(chunk_info, expanded_rules, csv_reader)
            
            # Process all chunks concurrently
            tasks = [process_single_chunk(chunk) for chunk in chunks]
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.info("Processing chunks in sequential mode")
            # Process chunks sequentially
            chunk_results = []
            for i, chunk_info in enumerate(chunks):
                try:
                    result = await self._process_chunk(chunk_info, expanded_rules, csv_reader)
                    chunk_results.append(result)
                    logger.info(f"Completed chunk {i+1}/{len(chunks)}")
                except Exception as e:
                    chunk_results.append(e)
                    logger.error(f"Chunk {i+1} failed: {str(e)}")
        
        # Collect results and handle exceptions
        for i, result in enumerate(chunk_results):
            if isinstance(result, Exception):
                error_msg = str(result)
                logger.error(f"Chunk {i} failed with exception: {error_msg}")
                self.failed_chunks.append((i, error_msg))
            else:
                self.processing_results.append(result)
                if result.get("status") == "success":
                    self.processed_chunks += 1
                else:
                    self.failed_chunks.append((i, result.get("error", "Unknown error")))
        
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        logger.info(f"Completed processing: {self.processed_chunks}/{self.total_chunks} chunks successful")
        
        return {
            "status": "completed",
            "total_chunks": self.total_chunks,
            "successful_chunks": self.processed_chunks,
            "failed_chunks": len(self.failed_chunks),
            "processing_time_seconds": processing_time,
            "chunks_per_second": self.total_chunks / processing_time if processing_time > 0 else 0,
            "success_rate": self.processed_chunks / self.total_chunks if self.total_chunks > 0 else 0,
            "results": self.processing_results,
            "failures": self.failed_chunks,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }
    
    async def _process_chunk(
        self, 
        chunk_info: Tuple[int, pd.DataFrame], 
        expanded_rules: str,
        csv_reader: CSVReader
    ) -> Dict[str, Any]:
        """
        Process a single chunk through LLM.
        
        Args:
            chunk_info: Tuple of (chunk_index, chunk_dataframe)
            expanded_rules: LLM-expanded cleaning rules
            csv_reader: CSV reader for helper methods
            
        Returns:
            Processing result for the chunk
        """
        chunk_index, chunk_df = chunk_info
        
        try:
            logger.debug(f"Processing chunk {chunk_index} with {len(chunk_df)} records")
            
            # Reset retry count for each chunk
            self.llm_client.reset_retry_count()
            
            # Prepare chunk data for LLM
            chunk_data = csv_reader.get_chunk_with_headers(chunk_df)
            
            # Process through LLM
            cleaning_result = await self.llm_client.clean_data_chunk(chunk_data, expanded_rules)
            
            if cleaning_result.get("status") == "success":
                logger.debug(f"Successfully processed chunk {chunk_index}")
                return {
                    "status": "success",
                    "chunk_index": chunk_index,
                    "original_records": len(chunk_df),
                    "cleaned_data": cleaning_result.get("cleaned_data"),
                    "changes": cleaning_result.get("changes", []),
                    "processing_notes": cleaning_result.get("notes", ""),
                    "chunk_info": {
                        "start_row": chunk_index * settings.chunk_size,
                        "end_row": chunk_index * settings.chunk_size + len(chunk_df) - 1,
                        "record_count": len(chunk_df)
                    }
                }
            else:
                error_msg = cleaning_result.get("error", "Unknown LLM processing error")
                logger.error(f"Failed to process chunk {chunk_index}: {error_msg}")
                return {
                    "status": "error",
                    "chunk_index": chunk_index,
                    "error": error_msg,
                    "original_records": len(chunk_df),
                    "chunk_info": {
                        "start_row": chunk_index * settings.chunk_size,
                        "end_row": chunk_index * settings.chunk_size + len(chunk_df) - 1,
                        "record_count": len(chunk_df)
                    }
                }
                
        except Exception as e:
            error_msg = f"Exception processing chunk {chunk_index}: {str(e)}"
            logger.error(error_msg)
            return {
                "status": "error",
                "chunk_index": chunk_index,
                "error": error_msg,
                "original_records": len(chunk_df) if chunk_df is not None else 0
            }
    
    async def process_geographical_validation(self, chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process geographical validation for all chunks via LLM.
        
        Args:
            chunk_results: Results from chunk processing
            
        Returns:
            Geographical validation results
        """
        logger.info("Starting geographical validation via LLM")
        
        try:
            # Extract geographical data from all chunks
            geo_data = []
            for result in chunk_results:
                if result.get("status") == "success":
                    # Extract potential geographical fields from cleaned data
                    cleaned_data = result.get("cleaned_data", "")
                    # This would be processed by LLM to identify geo fields
                    geo_data.append({
                        "chunk_index": result.get("chunk_index"),
                        "data": cleaned_data
                    })
            
            if not geo_data:
                return {
                    "status": "skipped",
                    "message": "No successful chunks to validate geographically"
                }
            
            # Use LLM for geographical validation
            validation_result = await self.llm_client.validate_geographical_data({
                "chunks": geo_data,
                "validation_type": "geographical_consistency"
            })
            
            return {
                "status": "success",
                "validation_result": validation_result,
                "chunks_validated": len(geo_data)
            }
            
        except Exception as e:
            logger.error(f"Error in geographical validation: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def process_phone_normalization(self, chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process phone number normalization for all chunks via LLM.
        
        Args:
            chunk_results: Results from chunk processing
            
        Returns:
            Phone normalization results
        """
        logger.info("Starting phone number normalization via LLM")
        
        try:
            # Extract phone data from all chunks
            phone_data = []
            for result in chunk_results:
                if result.get("status") == "success":
                    # Extract potential phone fields from cleaned data
                    cleaned_data = result.get("cleaned_data", "")
                    phone_data.append({
                        "chunk_index": result.get("chunk_index"),
                        "data": cleaned_data
                    })
            
            if not phone_data:
                return {
                    "status": "skipped",
                    "message": "No successful chunks to normalize phone numbers"
                }
            
            # Use LLM for phone normalization
            normalization_result = await self.llm_client.normalize_phone_numbers({
                "chunks": phone_data,
                "normalization_type": "phone_standardization"
            })
            
            return {
                "status": "success",
                "normalization_result": normalization_result,
                "chunks_processed": len(phone_data)
            }
            
        except Exception as e:
            logger.error(f"Error in phone normalization: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """
        Get detailed processing statistics.
        
        Returns:
            Processing statistics and metrics
        """
        total_records_processed = sum(
            result.get("original_records", 0) 
            for result in self.processing_results 
            if result.get("status") == "success"
        )
        
        total_changes = sum(
            len(result.get("changes", [])) 
            for result in self.processing_results 
            if result.get("status") == "success"
        )
        
        return {
            "total_chunks": self.total_chunks,
            "successful_chunks": self.processed_chunks,
            "failed_chunks": len(self.failed_chunks),
            "success_rate": self.processed_chunks / self.total_chunks if self.total_chunks > 0 else 0,
            "total_records_processed": total_records_processed,
            "total_changes_made": total_changes,
            "average_changes_per_chunk": total_changes / self.processed_chunks if self.processed_chunks > 0 else 0,
            "failure_details": self.failed_chunks
        }
    
    def get_successful_results(self) -> List[Dict[str, Any]]:
        """
        Get only the successful processing results.
        
        Returns:
            List of successful chunk results
        """
        return [
            result for result in self.processing_results 
            if result.get("status") == "success"
        ]
    
    def clear_results(self) -> None:
        """Clear all processing results and reset counters."""
        self.processing_results.clear()
        self.failed_chunks.clear()
        self.total_chunks = 0
        self.processed_chunks = 0
        logger.info("Cleared all processing results")
