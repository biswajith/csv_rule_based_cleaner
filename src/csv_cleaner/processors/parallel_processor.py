"""
Parallel Processor - Enhanced parallel processing engine for large CSV files.
Optimized for concurrent LLM calls with proper rate limiting and error handling.
"""

import asyncio
import time
from typing import Dict, List, Any, Optional, Callable, Tuple
from loguru import logger
from datetime import datetime
import json

from ..core.llm_client import LLMClient
from ..config.settings import settings


class ParallelProcessor:
    """
    Enhanced parallel processing engine with advanced concurrency control,
    rate limiting, and comprehensive error handling.
    """
    
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.processing_stats = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "retry_attempts": 0,
            "start_time": None,
            "end_time": None,
            "rate_limit_hits": 0,
            "errors": []
        }
        
    async def process_chunks_parallel(
        self, 
        chunks: List[Tuple[int, Any]], 
        processing_function: Callable,
        max_concurrent: Optional[int] = None,
        rate_limit_per_minute: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Process chunks in parallel with rate limiting and error handling.
        
        Args:
            chunks: List of (chunk_index, chunk_data) tuples
            processing_function: Async function to process each chunk
            max_concurrent: Maximum concurrent tasks (defaults to settings)
            rate_limit_per_minute: Rate limit for API calls
            
        Returns:
            Parallel processing results
        """
        max_concurrent = max_concurrent or settings.max_concurrent_requests
        rate_limit_per_minute = rate_limit_per_minute or settings.rate_limit_requests_per_minute
        
        # Initialize stats
        self.processing_stats["total_tasks"] = len(chunks)
        self.processing_stats["start_time"] = datetime.now()
        self.processing_stats["completed_tasks"] = 0
        self.processing_stats["failed_tasks"] = 0
        self.processing_stats["retry_attempts"] = 0
        self.processing_stats["errors"] = []
        
        logger.info(f"Starting parallel processing: {len(chunks)} chunks, max_concurrent={max_concurrent}")
        
        # Create rate limiter
        rate_limiter = RateLimiter(rate_limit_per_minute)
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(max_concurrent)
        
        # Process chunks with controlled concurrency
        tasks = []
        for chunk_index, chunk_data in chunks:
            task = self._process_chunk_with_controls(
                chunk_index, 
                chunk_data, 
                processing_function,
                semaphore, 
                rate_limiter
            )
            tasks.append(task)
        
        # Execute all tasks and gather results
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle exceptions
        successful_results = []
        failed_results = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                error_info = {
                    "chunk_index": i,
                    "error": str(result),
                    "error_type": type(result).__name__,
                    "timestamp": datetime.now().isoformat()
                }
                failed_results.append(error_info)
                self.processing_stats["errors"].append(error_info)
                self.processing_stats["failed_tasks"] += 1
            else:
                successful_results.append(result)
                self.processing_stats["completed_tasks"] += 1
        
        self.processing_stats["end_time"] = datetime.now()
        
        # Calculate performance metrics
        processing_time = (self.processing_stats["end_time"] - self.processing_stats["start_time"]).total_seconds()
        success_rate = self.processing_stats["completed_tasks"] / self.processing_stats["total_tasks"] if self.processing_stats["total_tasks"] > 0 else 0
        
        logger.info(f"Parallel processing completed: {self.processing_stats['completed_tasks']}/{self.processing_stats['total_tasks']} successful")
        
        return {
            "status": "completed",
            "results": successful_results,
            "failed_results": failed_results,
            "statistics": {
                **self.processing_stats,
                "processing_time_seconds": processing_time,
                "success_rate": success_rate,
                "chunks_per_second": self.processing_stats["total_tasks"] / processing_time if processing_time > 0 else 0,
                "avg_time_per_chunk": processing_time / self.processing_stats["total_tasks"] if self.processing_stats["total_tasks"] > 0 else 0
            }
        }
    
    async def _process_chunk_with_controls(
        self, 
        chunk_index: int, 
        chunk_data: Any,
        processing_function: Callable,
        semaphore: asyncio.Semaphore,
        rate_limiter: 'RateLimiter'
    ) -> Dict[str, Any]:
        """
        Process a single chunk with concurrency and rate limiting controls.
        
        Args:
            chunk_index: Index of the chunk
            chunk_data: Data to process
            processing_function: Function to call for processing
            semaphore: Concurrency control semaphore
            rate_limiter: Rate limiting controller
            
        Returns:
            Processing result for the chunk
        """
        async with semaphore:
            # Apply rate limiting
            await rate_limiter.acquire()
            
            try:
                # Reset retry count for each chunk
                self.llm_client.reset_retry_count()
                
                # Process the chunk
                result = await processing_function(chunk_index, chunk_data)
                
                logger.debug(f"Successfully processed chunk {chunk_index}")
                return {
                    "chunk_index": chunk_index,
                    "status": "success",
                    "result": result,
                    "processing_time": time.time()
                }
                
            except Exception as e:
                logger.error(f"Error processing chunk {chunk_index}: {str(e)}")
                
                # Try retry if configured
                if settings.max_retries > 0:
                    self.processing_stats["retry_attempts"] += 1
                    try:
                        await asyncio.sleep(settings.retry_delay)
                        result = await processing_function(chunk_index, chunk_data)
                        logger.info(f"Successfully retried chunk {chunk_index}")
                        return {
                            "chunk_index": chunk_index,
                            "status": "success_retry",
                            "result": result,
                            "processing_time": time.time(),
                            "retry_count": 1
                        }
                    except Exception as retry_error:
                        logger.error(f"Retry failed for chunk {chunk_index}: {str(retry_error)}")
                        raise retry_error
                else:
                    raise e
    
    async def process_with_progress_tracking(
        self, 
        chunks: List[Tuple[int, Any]], 
        processing_function: Callable,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, Any]:
        """
        Process chunks with real-time progress tracking.
        
        Args:
            chunks: List of chunks to process
            processing_function: Processing function
            progress_callback: Optional callback for progress updates
            
        Returns:
            Processing results with progress information
        """
        total_chunks = len(chunks)
        completed_chunks = 0
        
        def default_progress_callback(completed: int, total: int):
            percentage = (completed / total) * 100 if total > 0 else 0
            logger.info(f"Progress: {completed}/{total} chunks ({percentage:.1f}%)")
        
        callback = progress_callback or default_progress_callback
        
        # Process chunks and track progress
        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        results = []
        
        async def process_with_callback(chunk_index: int, chunk_data: Any):
            nonlocal completed_chunks
            
            async with semaphore:
                try:
                    result = await processing_function(chunk_index, chunk_data)
                    completed_chunks += 1
                    callback(completed_chunks, total_chunks)
                    return result
                except Exception as e:
                    completed_chunks += 1
                    callback(completed_chunks, total_chunks)
                    raise e
        
        # Execute all chunks
        tasks = [process_with_callback(idx, data) for idx, data in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        successful = [r for r in results if not isinstance(r, Exception)]
        failed = [r for r in results if isinstance(r, Exception)]
        
        return {
            "status": "completed",
            "successful_results": successful,
            "failed_results": failed,
            "progress": {
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "success_count": len(successful),
                "failure_count": len(failed)
            }
        }
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """Get detailed processing statistics."""
        if self.processing_stats["start_time"] and self.processing_stats["end_time"]:
            processing_time = (self.processing_stats["end_time"] - self.processing_stats["start_time"]).total_seconds()
        else:
            processing_time = 0
            
        return {
            **self.processing_stats,
            "processing_time_seconds": processing_time,
            "success_rate": self.processing_stats["completed_tasks"] / self.processing_stats["total_tasks"] if self.processing_stats["total_tasks"] > 0 else 0,
            "failure_rate": self.processing_stats["failed_tasks"] / self.processing_stats["total_tasks"] if self.processing_stats["total_tasks"] > 0 else 0,
            "retry_rate": self.processing_stats["retry_attempts"] / self.processing_stats["total_tasks"] if self.processing_stats["total_tasks"] > 0 else 0
        }


class RateLimiter:
    """
    Rate limiter for API calls to respect service limits.
    """
    
    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.requests_per_second = requests_per_minute / 60.0
        self.last_request_time = 0
        self.request_count = 0
        self.window_start = time.time()
        
    async def acquire(self):
        """Acquire permission to make a request, blocking if necessary."""
        current_time = time.time()
        
        # Reset window if a minute has passed
        if current_time - self.window_start >= 60:
            self.request_count = 0
            self.window_start = current_time
        
        # Check if we're at the limit
        if self.request_count >= self.requests_per_minute:
            # Wait until the next window
            sleep_time = 60 - (current_time - self.window_start)
            if sleep_time > 0:
                logger.debug(f"Rate limit reached, sleeping for {sleep_time:.2f} seconds")
                await asyncio.sleep(sleep_time)
                self.request_count = 0
                self.window_start = time.time()
        
        # Ensure minimum time between requests
        time_since_last = current_time - self.last_request_time
        min_interval = 1.0 / self.requests_per_second
        
        if time_since_last < min_interval:
            sleep_time = min_interval - time_since_last
            await asyncio.sleep(sleep_time)
        
        self.request_count += 1
        self.last_request_time = time.time()


class TaskQueue:
    """
    Advanced task queue for managing chunk processing with priorities and dependencies.
    """
    
    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self.queue = asyncio.Queue()
        self.active_tasks = set()
        self.completed_tasks = []
        self.failed_tasks = []
        
    async def add_task(self, task_func: Callable, priority: int = 0, dependencies: List[str] = None):
        """Add a task to the queue with optional priority and dependencies."""
        task_info = {
            "func": task_func,
            "priority": priority,
            "dependencies": dependencies or [],
            "id": f"task_{len(self.completed_tasks) + len(self.failed_tasks) + len(self.active_tasks)}",
            "added_time": time.time()
        }
        await self.queue.put(task_info)
    
    async def process_queue(self) -> Dict[str, Any]:
        """Process all tasks in the queue with dependency management."""
        workers = [asyncio.create_task(self._worker()) for _ in range(self.max_concurrent)]
        
        # Wait for queue to be empty and all workers to finish
        await self.queue.join()
        
        # Cancel workers
        for worker in workers:
            worker.cancel()
        
        return {
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
            "total_tasks": len(self.completed_tasks) + len(self.failed_tasks)
        }
    
    async def _worker(self):
        """Worker coroutine that processes tasks from the queue."""
        while True:
            try:
                task_info = await self.queue.get()
                
                # Check dependencies
                if self._dependencies_met(task_info["dependencies"]):
                    try:
                        result = await task_info["func"]()
                        self.completed_tasks.append({
                            "id": task_info["id"],
                            "result": result,
                            "completion_time": time.time()
                        })
                    except Exception as e:
                        self.failed_tasks.append({
                            "id": task_info["id"],
                            "error": str(e),
                            "failure_time": time.time()
                        })
                else:
                    # Dependencies not met, put back in queue
                    await self.queue.put(task_info)
                    await asyncio.sleep(0.1)  # Brief delay before retry
                
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
    
    def _dependencies_met(self, dependencies: List[str]) -> bool:
        """Check if all dependencies for a task have been completed."""
        completed_ids = {task["id"] for task in self.completed_tasks}
        return all(dep in completed_ids for dep in dependencies)
