"""
Performance Monitor - Comprehensive performance monitoring and optimization system.
Tracks performance metrics, identifies bottlenecks, and provides optimization recommendations.
"""

import time
import psutil
import asyncio
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from loguru import logger
import json


@dataclass
class PerformanceMetric:
    """Individual performance metric."""
    name: str
    value: float
    unit: str
    timestamp: datetime
    context: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context
        }


@dataclass
class PerformanceProfile:
    """Performance profile for a specific operation."""
    operation_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration: Optional[float] = None
    memory_usage: Dict[str, float] = field(default_factory=dict)
    cpu_usage: float = 0.0
    metrics: List[PerformanceMetric] = field(default_factory=list)
    sub_operations: List['PerformanceProfile'] = field(default_factory=list)
    
    def finish(self):
        """Mark profile as finished and calculate duration."""
        self.end_time = datetime.now()
        if self.start_time:
            self.duration = (self.end_time - self.start_time).total_seconds()
    
    def add_metric(self, name: str, value: float, unit: str, context: Dict[str, Any] = None):
        """Add a performance metric."""
        metric = PerformanceMetric(
            name=name,
            value=value,
            unit=unit,
            timestamp=datetime.now(),
            context=context or {}
        )
        self.metrics.append(metric)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "operation_name": self.operation_name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration": self.duration,
            "memory_usage": self.memory_usage,
            "cpu_usage": self.cpu_usage,
            "metrics": [m.to_dict() for m in self.metrics],
            "sub_operations": [s.to_dict() for s in self.sub_operations]
        }


class PerformanceMonitor:
    """
    Comprehensive performance monitoring system for the CSV cleaning pipeline.
    Tracks timing, memory usage, CPU usage, and other performance metrics.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.profiles: List[PerformanceProfile] = []
        self.current_profile: Optional[PerformanceProfile] = None
        self.profile_stack: List[PerformanceProfile] = []
        self.system_metrics: List[PerformanceMetric] = []
        self.monitoring_active = True
        
        # Performance thresholds
        self.thresholds = {
            "operation_duration": 300.0,  # seconds
            "memory_usage": 1000.0,  # MB
            "cpu_usage": 80.0,  # percentage
            "chunk_processing_rate": 0.1  # chunks per second (minimum)
        }
    
    @asynccontextmanager
    async def profile_operation(self, operation_name: str, context: Dict[str, Any] = None):
        """
        Context manager for profiling operations.
        
        Args:
            operation_name: Name of the operation being profiled
            context: Additional context information
        """
        profile = PerformanceProfile(
            operation_name=operation_name,
            start_time=datetime.now()
        )
        
        # Add context as initial metric
        if context:
            profile.add_metric("context", 0, "info", context)
        
        # Push to stack and set as current
        self.profile_stack.append(profile)
        previous_profile = self.current_profile
        self.current_profile = profile
        
        # Capture initial system metrics
        initial_memory = self._get_memory_usage()
        initial_cpu = self._get_cpu_usage()
        
        try:
            logger.debug(f"Starting performance profile for: {operation_name}")
            yield profile
            
        finally:
            # Capture final metrics
            final_memory = self._get_memory_usage()
            final_cpu = self._get_cpu_usage()
            
            profile.memory_usage = {
                "initial_mb": initial_memory,
                "final_mb": final_memory,
                "delta_mb": final_memory - initial_memory
            }
            profile.cpu_usage = (initial_cpu + final_cpu) / 2  # Average CPU usage
            
            # Finish profile
            profile.finish()
            
            # Pop from stack
            self.profile_stack.pop()
            self.current_profile = previous_profile
            
            # Add to parent if exists, otherwise to main profiles
            if self.current_profile:
                self.current_profile.sub_operations.append(profile)
            else:
                self.profiles.append(profile)
            
            logger.debug(f"Completed performance profile for: {operation_name} ({profile.duration:.2f}s)")
            
            # Check thresholds and log warnings
            self._check_performance_thresholds(profile)
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # Convert to MB
        except Exception:
            return 0.0
    
    def _get_cpu_usage(self) -> float:
        """Get current CPU usage percentage."""
        try:
            return psutil.cpu_percent(interval=None)
        except Exception:
            return 0.0
    
    def _get_system_metrics(self) -> Dict[str, float]:
        """Get comprehensive system metrics."""
        try:
            return {
                "memory_usage_mb": self._get_memory_usage(),
                "cpu_usage_percent": self._get_cpu_usage(),
                "memory_available_mb": psutil.virtual_memory().available / 1024 / 1024,
                "disk_usage_percent": psutil.disk_usage('/').percent if hasattr(psutil, 'disk_usage') else 0
            }
        except Exception as e:
            logger.warning(f"Error getting system metrics: {e}")
            return {}
    
    def record_metric(self, name: str, value: float, unit: str, context: Dict[str, Any] = None):
        """
        Record a custom performance metric.
        
        Args:
            name: Metric name
            value: Metric value
            unit: Unit of measurement
            context: Additional context
        """
        metric = PerformanceMetric(
            name=name,
            value=value,
            unit=unit,
            timestamp=datetime.now(),
            context=context or {}
        )
        
        if self.current_profile:
            self.current_profile.add_metric(name, value, unit, context)
        else:
            self.system_metrics.append(metric)
    
    def record_llm_call_metrics(self, duration: float, tokens_used: int, cost: float = None):
        """
        Record LLM API call metrics.
        
        Args:
            duration: Call duration in seconds
            tokens_used: Number of tokens consumed
            cost: Estimated cost (if available)
        """
        context = {
            "tokens": tokens_used,
            "cost": cost,
            "type": "llm_api_call"
        }
        
        self.record_metric("llm_call_duration", duration, "seconds", context)
        self.record_metric("llm_tokens_used", tokens_used, "tokens", context)
        
        if cost is not None:
            self.record_metric("llm_call_cost", cost, "usd", context)
    
    def record_chunk_processing_metrics(self, chunk_size: int, processing_time: float, success: bool):
        """
        Record chunk processing metrics.
        
        Args:
            chunk_size: Number of records in chunk
            processing_time: Time to process chunk
            success: Whether processing was successful
        """
        context = {
            "chunk_size": chunk_size,
            "success": success,
            "type": "chunk_processing"
        }
        
        processing_rate = chunk_size / processing_time if processing_time > 0 else 0
        
        self.record_metric("chunk_processing_time", processing_time, "seconds", context)
        self.record_metric("chunk_processing_rate", processing_rate, "records_per_second", context)
        self.record_metric("chunk_success", 1.0 if success else 0.0, "boolean", context)
    
    def record_data_volume_metrics(self, records_processed: int, file_size_mb: float):
        """
        Record data volume metrics.
        
        Args:
            records_processed: Number of records processed
            file_size_mb: File size in megabytes
        """
        context = {"type": "data_volume"}
        
        self.record_metric("records_processed", records_processed, "records", context)
        self.record_metric("file_size", file_size_mb, "mb", context)
        
        if file_size_mb > 0:
            records_per_mb = records_processed / file_size_mb
            self.record_metric("records_per_mb", records_per_mb, "records_per_mb", context)
    
    def _check_performance_thresholds(self, profile: PerformanceProfile):
        """Check if performance profile exceeds thresholds."""
        if profile.duration and profile.duration > self.thresholds["operation_duration"]:
            logger.warning(f"Operation '{profile.operation_name}' took {profile.duration:.2f}s (threshold: {self.thresholds['operation_duration']}s)")
        
        memory_delta = profile.memory_usage.get("delta_mb", 0)
        if memory_delta > self.thresholds["memory_usage"]:
            logger.warning(f"Operation '{profile.operation_name}' used {memory_delta:.2f}MB memory (threshold: {self.thresholds['memory_usage']}MB)")
        
        if profile.cpu_usage > self.thresholds["cpu_usage"]:
            logger.warning(f"Operation '{profile.operation_name}' used {profile.cpu_usage:.1f}% CPU (threshold: {self.thresholds['cpu_usage']}%)")
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary."""
        if not self.profiles:
            return {"status": "no_data", "message": "No performance data available"}
        
        # Calculate overall metrics
        total_duration = sum(p.duration for p in self.profiles if p.duration)
        total_memory_used = sum(p.memory_usage.get("delta_mb", 0) for p in self.profiles)
        avg_cpu_usage = sum(p.cpu_usage for p in self.profiles) / len(self.profiles)
        
        # Find bottlenecks
        slowest_operations = sorted(
            [p for p in self.profiles if p.duration],
            key=lambda x: x.duration,
            reverse=True
        )[:5]
        
        memory_intensive_operations = sorted(
            self.profiles,
            key=lambda x: x.memory_usage.get("delta_mb", 0),
            reverse=True
        )[:5]
        
        # Calculate throughput metrics
        llm_calls = [m for m in self.system_metrics if m.context.get("type") == "llm_api_call"]
        chunk_processing = [m for m in self.system_metrics if m.context.get("type") == "chunk_processing"]
        
        return {
            "session_id": self.session_id,
            "summary_generated_at": datetime.now().isoformat(),
            "overall_metrics": {
                "total_duration": total_duration,
                "total_memory_used_mb": total_memory_used,
                "average_cpu_usage": avg_cpu_usage,
                "operations_profiled": len(self.profiles)
            },
            "throughput_metrics": {
                "llm_calls_made": len(llm_calls),
                "chunks_processed": len(chunk_processing),
                "avg_chunk_processing_time": sum(m.value for m in chunk_processing if m.name == "chunk_processing_time") / max(len(chunk_processing), 1)
            },
            "bottlenecks": {
                "slowest_operations": [
                    {
                        "name": op.operation_name,
                        "duration": op.duration,
                        "memory_delta": op.memory_usage.get("delta_mb", 0)
                    }
                    for op in slowest_operations
                ],
                "memory_intensive_operations": [
                    {
                        "name": op.operation_name,
                        "memory_delta": op.memory_usage.get("delta_mb", 0),
                        "duration": op.duration
                    }
                    for op in memory_intensive_operations
                ]
            },
            "system_metrics": self._get_system_metrics(),
            "recommendations": self._generate_performance_recommendations()
        }
    
    def _generate_performance_recommendations(self) -> List[str]:
        """Generate performance optimization recommendations."""
        recommendations = []
        
        if not self.profiles:
            return recommendations
        
        # Analyze operation durations
        avg_duration = sum(p.duration for p in self.profiles if p.duration) / len([p for p in self.profiles if p.duration])
        if avg_duration > self.thresholds["operation_duration"] / 2:
            recommendations.append("Consider reducing chunk size or optimizing LLM prompts to improve processing speed")
        
        # Analyze memory usage
        high_memory_ops = [p for p in self.profiles if p.memory_usage.get("delta_mb", 0) > self.thresholds["memory_usage"] / 2]
        if high_memory_ops:
            recommendations.append("High memory usage detected - consider processing smaller chunks or implementing data streaming")
        
        # Analyze LLM call patterns
        llm_metrics = [m for m in self.system_metrics if m.context.get("type") == "llm_api_call"]
        if len(llm_metrics) > 100:  # Many LLM calls
            recommendations.append("High number of LLM calls detected - consider batch processing or caching frequent results")
        
        # Check for retry patterns
        chunk_failures = [m for m in self.system_metrics if m.name == "chunk_success" and m.value == 0.0]
        if len(chunk_failures) > len(self.system_metrics) * 0.1:  # >10% failure rate
            recommendations.append("High chunk processing failure rate - review error handling and data quality")
        
        return recommendations
    
    def generate_performance_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report."""
        summary = self.get_performance_summary()
        
        # Detailed operation breakdown
        operation_details = []
        for profile in self.profiles:
            operation_details.append({
                "operation": profile.operation_name,
                "duration": profile.duration,
                "memory_usage": profile.memory_usage,
                "cpu_usage": profile.cpu_usage,
                "metrics_count": len(profile.metrics),
                "sub_operations_count": len(profile.sub_operations),
                "sub_operations": [
                    {
                        "name": sub.operation_name,
                        "duration": sub.duration,
                        "memory_delta": sub.memory_usage.get("delta_mb", 0)
                    }
                    for sub in profile.sub_operations
                ]
            })
        
        # Metric analysis
        metric_analysis = self._analyze_metrics()
        
        return {
            "report_metadata": {
                "session_id": self.session_id,
                "generated_at": datetime.now().isoformat(),
                "monitoring_duration": (datetime.now() - min(p.start_time for p in self.profiles)).total_seconds() if self.profiles else 0
            },
            "performance_summary": summary,
            "operation_details": operation_details,
            "metric_analysis": metric_analysis,
            "threshold_violations": self._find_threshold_violations(),
            "optimization_opportunities": self._identify_optimization_opportunities()
        }
    
    def _analyze_metrics(self) -> Dict[str, Any]:
        """Analyze collected metrics for patterns."""
        all_metrics = self.system_metrics + [m for p in self.profiles for m in p.metrics]
        
        if not all_metrics:
            return {"status": "no_metrics"}
        
        # Group metrics by name
        metrics_by_name = {}
        for metric in all_metrics:
            if metric.name not in metrics_by_name:
                metrics_by_name[metric.name] = []
            metrics_by_name[metric.name].append(metric.value)
        
        # Calculate statistics for each metric
        metric_stats = {}
        for name, values in metrics_by_name.items():
            if values:
                metric_stats[name] = {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "avg": sum(values) / len(values),
                    "total": sum(values)
                }
        
        return {
            "metrics_collected": len(all_metrics),
            "unique_metric_types": len(metrics_by_name),
            "metric_statistics": metric_stats
        }
    
    def _find_threshold_violations(self) -> List[Dict[str, Any]]:
        """Find operations that violated performance thresholds."""
        violations = []
        
        for profile in self.profiles:
            profile_violations = []
            
            if profile.duration and profile.duration > self.thresholds["operation_duration"]:
                profile_violations.append({
                    "type": "duration",
                    "threshold": self.thresholds["operation_duration"],
                    "actual": profile.duration,
                    "severity": "high" if profile.duration > self.thresholds["operation_duration"] * 2 else "medium"
                })
            
            memory_delta = profile.memory_usage.get("delta_mb", 0)
            if memory_delta > self.thresholds["memory_usage"]:
                profile_violations.append({
                    "type": "memory",
                    "threshold": self.thresholds["memory_usage"],
                    "actual": memory_delta,
                    "severity": "high" if memory_delta > self.thresholds["memory_usage"] * 2 else "medium"
                })
            
            if profile.cpu_usage > self.thresholds["cpu_usage"]:
                profile_violations.append({
                    "type": "cpu",
                    "threshold": self.thresholds["cpu_usage"],
                    "actual": profile.cpu_usage,
                    "severity": "medium"
                })
            
            if profile_violations:
                violations.append({
                    "operation": profile.operation_name,
                    "violations": profile_violations,
                    "start_time": profile.start_time.isoformat()
                })
        
        return violations
    
    def _identify_optimization_opportunities(self) -> List[Dict[str, Any]]:
        """Identify specific optimization opportunities."""
        opportunities = []
        
        # Long-running operations
        long_ops = [p for p in self.profiles if p.duration and p.duration > 60]  # > 1 minute
        if long_ops:
            opportunities.append({
                "type": "duration_optimization",
                "description": f"{len(long_ops)} operations took longer than 1 minute",
                "impact": "high",
                "suggestion": "Consider parallelization or algorithm optimization"
            })
        
        # Memory-intensive operations
        memory_ops = [p for p in self.profiles if p.memory_usage.get("delta_mb", 0) > 500]
        if memory_ops:
            opportunities.append({
                "type": "memory_optimization", 
                "description": f"{len(memory_ops)} operations used more than 500MB",
                "impact": "medium",
                "suggestion": "Implement data streaming or reduce chunk sizes"
            })
        
        # High LLM token usage
        llm_metrics = [m for m in self.system_metrics if m.name == "llm_tokens_used"]
        total_tokens = sum(m.value for m in llm_metrics)
        if total_tokens > 100000:  # > 100k tokens
            opportunities.append({
                "type": "llm_optimization",
                "description": f"High LLM token usage: {total_tokens:,.0f} tokens",
                "impact": "high",
                "suggestion": "Optimize prompts or implement response caching"
            })
        
        return opportunities
    
    def export_performance_report(self, file_path: str) -> bool:
        """Export performance report to JSON file."""
        try:
            report = self.generate_performance_report()
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Exported performance report to {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error exporting performance report: {e}")
            return False
    
    def start_monitoring(self):
        """Start performance monitoring."""
        self.monitoring_active = True
        logger.info(f"Performance monitoring started for session {self.session_id}")
    
    def stop_monitoring(self):
        """Stop performance monitoring."""
        self.monitoring_active = False
        logger.info(f"Performance monitoring stopped for session {self.session_id}")
    
    def clear_data(self):
        """Clear all collected performance data."""
        self.profiles.clear()
        self.system_metrics.clear()
        self.current_profile = None
        self.profile_stack.clear()
        logger.info(f"Performance data cleared for session {self.session_id}")
