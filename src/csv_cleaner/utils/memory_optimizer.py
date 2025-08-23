"""
Memory Optimizer - Memory management and optimization utilities.
Provides memory monitoring, cleanup, and optimization strategies.
"""

import gc
import psutil
import weakref
from typing import Dict, List, Any, Optional, Callable
from contextlib import contextmanager
from loguru import logger
import pandas as pd


class MemoryOptimizer:
    """
    Memory optimization and management system.
    Provides memory monitoring, cleanup, and optimization strategies.
    """
    
    def __init__(self, max_memory_mb: Optional[float] = None):
        self.max_memory_mb = max_memory_mb or 1000.0  # 1GB default
        self.memory_warnings_enabled = True
        self.auto_cleanup_enabled = True
        self.tracked_objects = weakref.WeakSet()
        
    def get_memory_usage(self) -> Dict[str, float]:
        """Get current memory usage statistics."""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                "rss_mb": memory_info.rss / 1024 / 1024,  # Resident Set Size
                "vms_mb": memory_info.vms / 1024 / 1024,  # Virtual Memory Size
                "percent": process.memory_percent(),
                "available_mb": psutil.virtual_memory().available / 1024 / 1024,
                "total_mb": psutil.virtual_memory().total / 1024 / 1024
            }
        except Exception as e:
            logger.warning(f"Error getting memory usage: {e}")
            return {}
    
    def check_memory_pressure(self) -> Dict[str, Any]:
        """Check if system is under memory pressure."""
        memory_stats = self.get_memory_usage()
        current_usage = memory_stats.get("rss_mb", 0)
        available = memory_stats.get("available_mb", float('inf'))
        
        pressure_level = "low"
        warnings = []
        
        # Check against configured maximum
        if current_usage > self.max_memory_mb:
            pressure_level = "high"
            warnings.append(f"Current usage ({current_usage:.1f}MB) exceeds limit ({self.max_memory_mb:.1f}MB)")
        
        # Check available system memory
        if available < 500:  # Less than 500MB available
            pressure_level = "high"
            warnings.append(f"Low system memory available ({available:.1f}MB)")
        elif available < 1000:  # Less than 1GB available
            if pressure_level == "low":
                pressure_level = "medium"
            warnings.append(f"Medium system memory pressure ({available:.1f}MB available)")
        
        return {
            "pressure_level": pressure_level,
            "current_usage_mb": current_usage,
            "available_mb": available,
            "warnings": warnings,
            "should_cleanup": pressure_level in ["medium", "high"]
        }
    
    @contextmanager
    def memory_limit_context(self, operation_name: str):
        """Context manager that monitors memory usage during operation."""
        initial_memory = self.get_memory_usage()
        initial_usage = initial_memory.get("rss_mb", 0)
        
        logger.debug(f"Starting memory monitoring for: {operation_name} (initial: {initial_usage:.1f}MB)")
        
        try:
            yield
        finally:
            final_memory = self.get_memory_usage()
            final_usage = final_memory.get("rss_mb", 0)
            memory_delta = final_usage - initial_usage
            
            logger.debug(f"Memory usage for {operation_name}: {memory_delta:+.1f}MB (final: {final_usage:.1f}MB)")
            
            # Check for memory leaks or excessive usage
            if memory_delta > 100:  # More than 100MB increase
                logger.warning(f"High memory increase in {operation_name}: {memory_delta:.1f}MB")
                
                if self.auto_cleanup_enabled:
                    self.force_cleanup()
            
            # Check memory pressure
            pressure = self.check_memory_pressure()
            if pressure["should_cleanup"] and self.auto_cleanup_enabled:
                logger.info("Memory pressure detected, performing cleanup")
                self.force_cleanup()
    
    def optimize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimize DataFrame memory usage."""
        if df.empty:
            return df
        
        initial_memory = df.memory_usage(deep=True).sum() / 1024 / 1024  # MB
        
        # Optimize numeric columns
        for col in df.select_dtypes(include=['int64']).columns:
            if df[col].min() >= 0:
                if df[col].max() < 255:
                    df[col] = df[col].astype('uint8')
                elif df[col].max() < 65535:
                    df[col] = df[col].astype('uint16')
                elif df[col].max() < 4294967295:
                    df[col] = df[col].astype('uint32')
            else:
                if df[col].min() > -128 and df[col].max() < 127:
                    df[col] = df[col].astype('int8')
                elif df[col].min() > -32768 and df[col].max() < 32767:
                    df[col] = df[col].astype('int16')
                elif df[col].min() > -2147483648 and df[col].max() < 2147483647:
                    df[col] = df[col].astype('int32')
        
        # Optimize float columns
        for col in df.select_dtypes(include=['float64']).columns:
            df[col] = pd.to_numeric(df[col], downcast='float')
        
        # Optimize object columns (strings)
        for col in df.select_dtypes(include=['object']).columns:
            if df[col].dtype == 'object':
                # Check if it's categorical
                unique_ratio = len(df[col].unique()) / len(df[col])
                if unique_ratio < 0.5:  # Less than 50% unique values
                    df[col] = df[col].astype('category')
        
        final_memory = df.memory_usage(deep=True).sum() / 1024 / 1024  # MB
        memory_saved = initial_memory - final_memory
        
        if memory_saved > 0:
            reduction_percent = (memory_saved / initial_memory) * 100
            logger.debug(f"DataFrame optimized: {memory_saved:.1f}MB saved ({reduction_percent:.1f}% reduction)")
        
        return df
    
    def optimize_chunk_size(self, total_records: int, record_size_estimate: float) -> int:
        """Determine optimal chunk size based on available memory."""
        memory_stats = self.get_memory_usage()
        available_mb = memory_stats.get("available_mb", 1000)
        
        # Use at most 25% of available memory for chunk processing
        max_chunk_memory_mb = available_mb * 0.25
        
        # Estimate records per MB
        record_size_mb = record_size_estimate / 1024 / 1024  # Convert bytes to MB
        if record_size_mb == 0:
            record_size_mb = 0.001  # Fallback estimate
        
        max_records_for_memory = int(max_chunk_memory_mb / record_size_mb)
        
        # Apply reasonable bounds
        min_chunk_size = 10
        max_chunk_size = 1000
        default_chunk_size = 250
        
        # Choose optimal size
        optimal_size = min(max(min_chunk_size, max_records_for_memory), max_chunk_size)
        
        # If memory-based calculation is unrealistic, use default
        if optimal_size < min_chunk_size or optimal_size > total_records:
            optimal_size = min(default_chunk_size, total_records)
        
        logger.debug(f"Optimized chunk size: {optimal_size} records (available memory: {available_mb:.1f}MB)")
        
        return optimal_size
    
    def force_cleanup(self) -> Dict[str, Any]:
        """Force garbage collection and memory cleanup."""
        initial_memory = self.get_memory_usage()
        initial_usage = initial_memory.get("rss_mb", 0)
        
        # Force garbage collection
        collected_objects = gc.collect()
        
        # Clear any cached data
        self._clear_internal_caches()
        
        final_memory = self.get_memory_usage()
        final_usage = final_memory.get("rss_mb", 0)
        memory_freed = initial_usage - final_usage
        
        cleanup_result = {
            "objects_collected": collected_objects,
            "memory_before_mb": initial_usage,
            "memory_after_mb": final_usage,
            "memory_freed_mb": memory_freed,
            "cleanup_effective": memory_freed > 0
        }
        
        logger.info(f"Memory cleanup completed: {memory_freed:.1f}MB freed, {collected_objects} objects collected")
        
        return cleanup_result
    
    def _clear_internal_caches(self):
        """Clear internal caches and temporary data."""
        # Clear pandas caches
        try:
            pd.core.common.clear_cache()
        except AttributeError:
            pass  # Method might not exist in all pandas versions
        
        # Clear any weakref tracked objects
        for obj in list(self.tracked_objects):
            if hasattr(obj, 'clear_cache'):
                try:
                    obj.clear_cache()
                except Exception:
                    pass
    
    def track_object(self, obj: Any):
        """Track an object for memory management."""
        self.tracked_objects.add(obj)
    
    def get_memory_recommendations(self) -> List[str]:
        """Get memory optimization recommendations."""
        recommendations = []
        memory_stats = self.get_memory_usage()
        pressure = self.check_memory_pressure()
        
        current_usage = memory_stats.get("rss_mb", 0)
        available = memory_stats.get("available_mb", 0)
        
        if pressure["pressure_level"] == "high":
            recommendations.extend([
                "Consider reducing chunk size to decrease memory usage",
                "Enable more aggressive garbage collection",
                "Process data in smaller batches"
            ])
        
        if current_usage > 500:  # More than 500MB
            recommendations.extend([
                "Consider using data streaming instead of loading entire dataset",
                "Optimize DataFrame data types to reduce memory footprint",
                "Implement result caching to avoid reprocessing"
            ])
        
        if available < 1000:  # Less than 1GB available
            recommendations.extend([
                "Close other applications to free system memory",
                "Consider processing on a machine with more RAM",
                "Use disk-based processing for very large datasets"
            ])
        
        return recommendations
    
    def create_memory_profile(self, operation_name: str) -> Dict[str, Any]:
        """Create a memory usage profile for an operation."""
        memory_stats = self.get_memory_usage()
        pressure = self.check_memory_pressure()
        
        return {
            "operation": operation_name,
            "timestamp": pd.Timestamp.now().isoformat(),
            "memory_usage": memory_stats,
            "pressure_analysis": pressure,
            "recommendations": self.get_memory_recommendations(),
            "gc_stats": {
                "counts": gc.get_count(),
                "thresholds": gc.get_threshold()
            }
        }
    
    def monitor_memory_growth(self, baseline_mb: float, threshold_mb: float = 100) -> bool:
        """Monitor if memory growth exceeds threshold."""
        current_usage = self.get_memory_usage().get("rss_mb", 0)
        growth = current_usage - baseline_mb
        
        if growth > threshold_mb:
            logger.warning(f"Memory growth detected: {growth:.1f}MB increase from baseline ({baseline_mb:.1f}MB)")
            return True
        
        return False
    
    def suggest_chunk_strategy(self, total_records: int, estimated_record_size: int) -> Dict[str, Any]:
        """Suggest optimal chunking strategy based on memory constraints."""
        memory_stats = self.get_memory_usage()
        available_mb = memory_stats.get("available_mb", 1000)
        
        # Calculate different chunking strategies
        strategies = []
        
        # Memory-optimized strategy
        optimal_chunk_size = self.optimize_chunk_size(total_records, estimated_record_size)
        strategies.append({
            "name": "memory_optimized",
            "chunk_size": optimal_chunk_size,
            "estimated_chunks": (total_records + optimal_chunk_size - 1) // optimal_chunk_size,
            "memory_per_chunk_mb": (optimal_chunk_size * estimated_record_size) / 1024 / 1024,
            "description": "Optimized for current memory availability"
        })
        
        # Conservative strategy (smaller chunks)
        conservative_size = min(100, total_records)
        strategies.append({
            "name": "conservative",
            "chunk_size": conservative_size,
            "estimated_chunks": (total_records + conservative_size - 1) // conservative_size,
            "memory_per_chunk_mb": (conservative_size * estimated_record_size) / 1024 / 1024,
            "description": "Minimal memory usage, slower processing"
        })
        
        # Aggressive strategy (larger chunks)
        aggressive_size = min(500, total_records)
        strategies.append({
            "name": "aggressive",
            "chunk_size": aggressive_size,
            "estimated_chunks": (total_records + aggressive_size - 1) // aggressive_size,
            "memory_per_chunk_mb": (aggressive_size * estimated_record_size) / 1024 / 1024,
            "description": "Faster processing, higher memory usage"
        })
        
        # Recommend best strategy based on available memory
        recommended_strategy = "memory_optimized"
        if available_mb > 2000:  # More than 2GB available
            recommended_strategy = "aggressive"
        elif available_mb < 500:  # Less than 500MB available
            recommended_strategy = "conservative"
        
        return {
            "total_records": total_records,
            "estimated_record_size_bytes": estimated_record_size,
            "available_memory_mb": available_mb,
            "strategies": strategies,
            "recommended": recommended_strategy,
            "recommendation_reason": self._get_strategy_reason(recommended_strategy, available_mb)
        }
    
    def _get_strategy_reason(self, strategy: str, available_mb: float) -> str:
        """Get explanation for strategy recommendation."""
        if strategy == "aggressive":
            return f"Sufficient memory available ({available_mb:.0f}MB) for larger chunks"
        elif strategy == "conservative":
            return f"Limited memory available ({available_mb:.0f}MB), using smaller chunks"
        else:
            return f"Balanced approach based on available memory ({available_mb:.0f}MB)"
