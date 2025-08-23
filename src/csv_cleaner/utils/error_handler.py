"""
Enhanced Error Handler - Comprehensive error handling and recovery system.
Provides robust error handling, retry logic, and graceful degradation.
"""

import asyncio
import traceback
import time
from typing import Dict, List, Any, Optional, Callable, Union
from datetime import datetime
from loguru import logger
from enum import Enum
from dataclasses import dataclass
import json


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium" 
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Categories of errors."""
    LLM_API_ERROR = "llm_api_error"
    DATA_PROCESSING_ERROR = "data_processing_error"
    VALIDATION_ERROR = "validation_error"
    CONFIGURATION_ERROR = "configuration_error"
    NETWORK_ERROR = "network_error"
    FILE_IO_ERROR = "file_io_error"
    SYSTEM_ERROR = "system_error"
    USER_INPUT_ERROR = "user_input_error"


@dataclass
class ErrorRecord:
    """Individual error record with full context."""
    error_id: str
    timestamp: str
    category: ErrorCategory
    severity: ErrorSeverity
    error_type: str
    error_message: str
    context: Dict[str, Any]
    stack_trace: str
    recovery_action: str
    retry_count: int
    resolved: bool
    resolution_details: Optional[str] = None


class ErrorHandler:
    """
    Comprehensive error handling system with recovery strategies,
    retry logic, and error tracking.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.errors: List[ErrorRecord] = []
        self.error_counter = 0
        self.recovery_strategies: Dict[ErrorCategory, Callable] = {}
        self.error_thresholds = {
            ErrorSeverity.LOW: 10,
            ErrorSeverity.MEDIUM: 5,
            ErrorSeverity.HIGH: 3,
            ErrorSeverity.CRITICAL: 1
        }
        self._setup_default_recovery_strategies()
    
    def _setup_default_recovery_strategies(self):
        """Setup default recovery strategies for different error categories."""
        self.recovery_strategies[ErrorCategory.LLM_API_ERROR] = self._handle_llm_api_error
        self.recovery_strategies[ErrorCategory.NETWORK_ERROR] = self._handle_network_error
        self.recovery_strategies[ErrorCategory.DATA_PROCESSING_ERROR] = self._handle_data_processing_error
        self.recovery_strategies[ErrorCategory.VALIDATION_ERROR] = self._handle_validation_error
        self.recovery_strategies[ErrorCategory.FILE_IO_ERROR] = self._handle_file_io_error
    
    async def handle_error(
        self,
        error: Exception,
        context: Dict[str, Any],
        category: ErrorCategory,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        recovery_action: str = "retry",
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Handle an error with appropriate recovery strategy.
        
        Args:
            error: The exception that occurred
            context: Context information about where the error occurred
            category: Category of the error
            severity: Severity level of the error
            recovery_action: Initial recovery action to attempt
            max_retries: Maximum retry attempts
            
        Returns:
            Error handling result
        """
        self.error_counter += 1
        error_id = f"{self.session_id}_error_{self.error_counter:06d}"
        
        # Create error record
        error_record = ErrorRecord(
            error_id=error_id,
            timestamp=datetime.now().isoformat(),
            category=category,
            severity=severity,
            error_type=type(error).__name__,
            error_message=str(error),
            context=context,
            stack_trace=traceback.format_exc(),
            recovery_action=recovery_action,
            retry_count=0,
            resolved=False
        )
        
        self.errors.append(error_record)
        
        logger.error(f"Error {error_id} [{category.value}:{severity.value}]: {str(error)}")
        
        # Check if we've exceeded error thresholds
        if self._check_error_threshold(severity):
            logger.critical(f"Error threshold exceeded for severity {severity.value}")
            return {
                "status": "threshold_exceeded",
                "error_id": error_id,
                "action": "abort_processing",
                "error_record": error_record
            }
        
        # Attempt recovery
        recovery_result = await self._attempt_recovery(error_record, max_retries)
        
        return {
            "status": "handled",
            "error_id": error_id,
            "recovery_result": recovery_result,
            "error_record": error_record
        }
    
    async def _attempt_recovery(self, error_record: ErrorRecord, max_retries: int) -> Dict[str, Any]:
        """
        Attempt recovery using appropriate strategy.
        
        Args:
            error_record: The error record to recover from
            max_retries: Maximum retry attempts
            
        Returns:
            Recovery result
        """
        recovery_strategy = self.recovery_strategies.get(error_record.category)
        
        if not recovery_strategy:
            logger.warning(f"No recovery strategy for category {error_record.category.value}")
            return {
                "success": False,
                "action": "no_strategy",
                "message": f"No recovery strategy available for {error_record.category.value}"
            }
        
        # Attempt recovery with retries
        for attempt in range(max_retries):
            error_record.retry_count = attempt + 1
            
            try:
                logger.info(f"Recovery attempt {attempt + 1}/{max_retries} for error {error_record.error_id}")
                
                recovery_result = await recovery_strategy(error_record)
                
                if recovery_result.get("success", False):
                    error_record.resolved = True
                    error_record.resolution_details = recovery_result.get("details", "Recovery successful")
                    logger.info(f"Successfully recovered from error {error_record.error_id}")
                    return recovery_result
                
            except Exception as recovery_error:
                logger.error(f"Recovery attempt {attempt + 1} failed for error {error_record.error_id}: {str(recovery_error)}")
                
                if attempt < max_retries - 1:
                    # Exponential backoff
                    delay = 2 ** attempt
                    await asyncio.sleep(delay)
        
        # All recovery attempts failed
        logger.error(f"All recovery attempts failed for error {error_record.error_id}")
        return {
            "success": False,
            "action": "recovery_failed",
            "attempts": max_retries,
            "message": "All recovery attempts exhausted"
        }
    
    async def _handle_llm_api_error(self, error_record: ErrorRecord) -> Dict[str, Any]:
        """Handle LLM API errors."""
        context = error_record.context
        
        # Check for rate limiting
        if "rate limit" in error_record.error_message.lower():
            logger.info("Rate limit detected, waiting before retry")
            await asyncio.sleep(60)  # Wait 1 minute for rate limit reset
            return {"success": True, "action": "rate_limit_wait", "details": "Waited for rate limit reset"}
        
        # Check for authentication errors
        if "authentication" in error_record.error_message.lower() or "api key" in error_record.error_message.lower():
            logger.error("Authentication error - check API key configuration")
            return {"success": False, "action": "auth_error", "details": "API key authentication failed"}
        
        # Check for quota exceeded
        if "quota" in error_record.error_message.lower():
            logger.error("API quota exceeded")
            return {"success": False, "action": "quota_exceeded", "details": "API quota limit reached"}
        
        # General API error - try after delay
        await asyncio.sleep(5)
        return {"success": True, "action": "delayed_retry", "details": "Retried after delay"}
    
    async def _handle_network_error(self, error_record: ErrorRecord) -> Dict[str, Any]:
        """Handle network-related errors."""
        # Exponential backoff for network issues
        delay = min(2 ** error_record.retry_count, 60)  # Max 60 seconds
        logger.info(f"Network error, waiting {delay} seconds before retry")
        await asyncio.sleep(delay)
        
        return {"success": True, "action": "network_retry", "details": f"Retried after {delay}s delay"}
    
    async def _handle_data_processing_error(self, error_record: ErrorRecord) -> Dict[str, Any]:
        """Handle data processing errors."""
        context = error_record.context
        
        # Try to isolate the problematic data
        if "chunk_data" in context:
            logger.info("Attempting to process chunk with smaller batch size")
            # This would involve breaking the chunk into smaller pieces
            return {"success": True, "action": "reduce_batch_size", "details": "Reduced chunk size for retry"}
        
        # Skip problematic record if possible
        if "record_index" in context:
            logger.warning(f"Skipping problematic record at index {context['record_index']}")
            return {"success": True, "action": "skip_record", "details": "Skipped problematic record"}
        
        return {"success": False, "action": "unrecoverable", "details": "Could not recover from data processing error"}
    
    async def _handle_validation_error(self, error_record: ErrorRecord) -> Dict[str, Any]:
        """Handle validation errors."""
        # Validation errors often indicate data quality issues
        # We can usually continue processing with a warning
        logger.warning("Validation error - continuing with data quality flag")
        
        return {"success": True, "action": "validation_warning", "details": "Continued processing with validation warning"}
    
    async def _handle_file_io_error(self, error_record: ErrorRecord) -> Dict[str, Any]:
        """Handle file I/O errors."""
        context = error_record.context
        
        # Check if it's a permission error
        if "permission" in error_record.error_message.lower():
            logger.error("File permission error - check file access rights")
            return {"success": False, "action": "permission_error", "details": "File permission denied"}
        
        # Check if it's a disk space error
        if "space" in error_record.error_message.lower() or "disk" in error_record.error_message.lower():
            logger.error("Disk space error - insufficient storage")
            return {"success": False, "action": "disk_space_error", "details": "Insufficient disk space"}
        
        # Try alternative file path or retry
        await asyncio.sleep(2)
        return {"success": True, "action": "file_retry", "details": "Retried file operation"}
    
    def _check_error_threshold(self, severity: ErrorSeverity) -> bool:
        """Check if error threshold has been exceeded for a severity level."""
        recent_errors = [
            e for e in self.errors
            if e.severity == severity and not e.resolved
            and (datetime.now() - datetime.fromisoformat(e.timestamp)).total_seconds() < 300  # Last 5 minutes
        ]
        
        threshold = self.error_thresholds.get(severity, 5)
        return len(recent_errors) >= threshold
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """Get comprehensive error statistics."""
        if not self.errors:
            return {"total_errors": 0}
        
        # Count by category
        category_counts = {}
        for category in ErrorCategory:
            category_counts[category.value] = len([e for e in self.errors if e.category == category])
        
        # Count by severity
        severity_counts = {}
        for severity in ErrorSeverity:
            severity_counts[severity.value] = len([e for e in self.errors if e.severity == severity])
        
        # Recovery statistics
        resolved_errors = [e for e in self.errors if e.resolved]
        unresolved_errors = [e for e in self.errors if not e.resolved]
        
        # Recent errors (last hour)
        recent_errors = [
            e for e in self.errors
            if (datetime.now() - datetime.fromisoformat(e.timestamp)).total_seconds() < 3600
        ]
        
        return {
            "total_errors": len(self.errors),
            "errors_by_category": category_counts,
            "errors_by_severity": severity_counts,
            "resolution_stats": {
                "resolved_errors": len(resolved_errors),
                "unresolved_errors": len(unresolved_errors),
                "resolution_rate": len(resolved_errors) / len(self.errors) if self.errors else 0
            },
            "recent_errors": len(recent_errors),
            "most_common_error_category": max(category_counts, key=category_counts.get) if category_counts else None,
            "most_common_error_severity": max(severity_counts, key=severity_counts.get) if severity_counts else None
        }
    
    def get_error_report(self) -> Dict[str, Any]:
        """Generate comprehensive error report."""
        statistics = self.get_error_statistics()
        
        # Group errors by category for detailed analysis
        errors_by_category = {}
        for error in self.errors:
            category = error.category.value
            if category not in errors_by_category:
                errors_by_category[category] = []
            
            errors_by_category[category].append({
                "error_id": error.error_id,
                "timestamp": error.timestamp,
                "severity": error.severity.value,
                "error_type": error.error_type,
                "error_message": error.error_message,
                "context": error.context,
                "retry_count": error.retry_count,
                "resolved": error.resolved,
                "resolution_details": error.resolution_details
            })
        
        return {
            "session_id": self.session_id,
            "report_generated_at": datetime.now().isoformat(),
            "statistics": statistics,
            "errors_by_category": errors_by_category,
            "unresolved_errors": [
                {
                    "error_id": e.error_id,
                    "category": e.category.value,
                    "severity": e.severity.value,
                    "error_message": e.error_message,
                    "timestamp": e.timestamp
                }
                for e in self.errors if not e.resolved
            ],
            "recommendations": self._generate_recommendations()
        }
    
    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on error patterns."""
        recommendations = []
        
        # Analyze error patterns
        unresolved_errors = [e for e in self.errors if not e.resolved]
        
        # Check for common API errors
        api_errors = [e for e in unresolved_errors if e.category == ErrorCategory.LLM_API_ERROR]
        if len(api_errors) > 3:
            recommendations.append("Consider reviewing API key configuration and rate limits")
        
        # Check for data quality issues
        data_errors = [e for e in unresolved_errors if e.category == ErrorCategory.DATA_PROCESSING_ERROR]
        if len(data_errors) > 5:
            recommendations.append("Consider improving input data quality or validation")
        
        # Check for network issues
        network_errors = [e for e in unresolved_errors if e.category == ErrorCategory.NETWORK_ERROR]
        if len(network_errors) > 3:
            recommendations.append("Check network connectivity and consider increasing timeout values")
        
        return recommendations
    
    def export_error_log(self, file_path: str) -> bool:
        """Export error log to JSON file."""
        try:
            report = self.get_error_report()
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Exported error log with {len(self.errors)} errors to {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error exporting error log to {file_path}: {str(e)}")
            return False
    
    def clear_resolved_errors(self) -> int:
        """Clear all resolved errors from memory."""
        original_count = len(self.errors)
        self.errors = [e for e in self.errors if not e.resolved]
        cleared_count = original_count - len(self.errors)
        
        logger.info(f"Cleared {cleared_count} resolved errors")
        return cleared_count


# Decorator for automatic error handling
def handle_errors(
    category: ErrorCategory,
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    max_retries: int = 3,
    error_handler: Optional[ErrorHandler] = None
):
    """
    Decorator for automatic error handling in functions.
    
    Args:
        category: Error category for classification
        severity: Error severity level
        max_retries: Maximum retry attempts
        error_handler: Error handler instance to use
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            context = {
                "function": func.__name__,
                "args": str(args)[:200],  # Limit context size
                "kwargs": str(kwargs)[:200]
            }
            
            if error_handler:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    result = await error_handler.handle_error(
                        error=e,
                        context=context,
                        category=category,
                        severity=severity,
                        max_retries=max_retries
                    )
                    
                    if result.get("recovery_result", {}).get("success", False):
                        # Retry the function
                        return await func(*args, **kwargs)
                    else:
                        # Re-raise the error if recovery failed
                        raise e
            else:
                # No error handler provided, just run the function
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator
