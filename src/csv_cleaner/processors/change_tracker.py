"""
Change Tracker - Comprehensive change tracking and audit trail system.
Tracks all data modifications with detailed explanations and metadata.
"""

import time
import json
from typing import Dict, List, Any, Optional, Union
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, asdict
from enum import Enum


class ChangeType(Enum):
    """Types of changes that can be tracked."""
    MODIFIED = "modified"
    NORMALIZED = "normalized"
    CORRECTED = "corrected"
    STANDARDIZED = "standardized"
    VALIDATED = "validated"
    REMOVED = "removed"
    ADDED = "added"
    MERGED = "merged"


class ConfidenceLevel(Enum):
    """Confidence levels for changes."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


@dataclass
class ChangeRecord:
    """Individual change record with full metadata."""
    change_id: str
    record_index: int
    field_name: str
    original_value: Any
    new_value: Any
    change_type: ChangeType
    confidence: ConfidenceLevel
    reason: str
    timestamp: str
    processing_stage: str
    llm_explanation: str
    validation_status: str
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert change record to dictionary."""
        return {
            "change_id": self.change_id,
            "record_index": self.record_index,
            "field_name": self.field_name,
            "original_value": self.original_value,
            "new_value": self.new_value,
            "change_type": self.change_type.value,
            "confidence": self.confidence.value,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "processing_stage": self.processing_stage,
            "llm_explanation": self.llm_explanation,
            "validation_status": self.validation_status,
            "metadata": self.metadata
        }


class ChangeTracker:
    """
    Comprehensive change tracking system for data cleaning operations.
    Maintains detailed audit trail of all modifications.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.changes: List[ChangeRecord] = []
        self.change_counter = 0
        self.session_start_time = datetime.now()
        self.processing_stages: List[str] = []
        
    def track_change(
        self,
        record_index: int,
        field_name: str,
        original_value: Any,
        new_value: Any,
        change_type: ChangeType,
        confidence: ConfidenceLevel,
        reason: str,
        processing_stage: str,
        llm_explanation: str = "",
        validation_status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Track a single change with full metadata.
        
        Args:
            record_index: Index of the record being changed
            field_name: Name of the field being changed
            original_value: Original value before change
            new_value: New value after change
            change_type: Type of change being made
            confidence: Confidence level in the change
            reason: Human-readable reason for the change
            processing_stage: Stage of processing where change occurred
            llm_explanation: Detailed explanation from LLM
            validation_status: Status of change validation
            metadata: Additional metadata about the change
            
        Returns:
            Unique change ID
        """
        self.change_counter += 1
        change_id = f"{self.session_id}_change_{self.change_counter:06d}"
        
        change_record = ChangeRecord(
            change_id=change_id,
            record_index=record_index,
            field_name=field_name,
            original_value=original_value,
            new_value=new_value,
            change_type=change_type,
            confidence=confidence,
            reason=reason,
            timestamp=datetime.now().isoformat(),
            processing_stage=processing_stage,
            llm_explanation=llm_explanation,
            validation_status=validation_status,
            metadata=metadata or {}
        )
        
        self.changes.append(change_record)
        
        logger.debug(f"Tracked change {change_id}: {field_name} '{original_value}' -> '{new_value}'")
        
        return change_id
    
    def track_multiple_changes(
        self,
        record_index: int,
        changes_data: List[Dict[str, Any]],
        processing_stage: str
    ) -> List[str]:
        """
        Track multiple changes for a single record.
        
        Args:
            record_index: Index of the record
            changes_data: List of change dictionaries
            processing_stage: Processing stage name
            
        Returns:
            List of change IDs
        """
        change_ids = []
        
        for change_data in changes_data:
            change_id = self.track_change(
                record_index=record_index,
                field_name=change_data.get("field_name", "unknown"),
                original_value=change_data.get("original_value"),
                new_value=change_data.get("new_value"),
                change_type=ChangeType(change_data.get("change_type", "modified")),
                confidence=ConfidenceLevel(change_data.get("confidence", "medium")),
                reason=change_data.get("reason", "No reason provided"),
                processing_stage=processing_stage,
                llm_explanation=change_data.get("llm_explanation", ""),
                validation_status=change_data.get("validation_status", "pending"),
                metadata=change_data.get("metadata", {})
            )
            change_ids.append(change_id)
        
        return change_ids
    
    def track_chunk_processing(
        self,
        chunk_index: int,
        chunk_changes: List[Dict[str, Any]],
        processing_stage: str,
        chunk_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Track all changes from processing a data chunk.
        
        Args:
            chunk_index: Index of the processed chunk
            chunk_changes: List of changes from the chunk
            processing_stage: Name of the processing stage
            chunk_metadata: Metadata about the chunk processing
            
        Returns:
            Summary of tracked changes
        """
        change_ids = []
        
        for change_data in chunk_changes:
            # Calculate absolute record index
            record_index = chunk_metadata.get("start_row", 0) + change_data.get("record_index", 0)
            
            change_id = self.track_change(
                record_index=record_index,
                field_name=change_data.get("field_name", "unknown"),
                original_value=change_data.get("original_value"),
                new_value=change_data.get("new_value"),
                change_type=ChangeType(change_data.get("change_type", "modified")),
                confidence=ConfidenceLevel(change_data.get("confidence", "medium")),
                reason=change_data.get("reason", "No reason provided"),
                processing_stage=processing_stage,
                llm_explanation=change_data.get("llm_explanation", ""),
                validation_status=change_data.get("validation_status", "pending"),
                metadata={
                    **change_data.get("metadata", {}),
                    "chunk_index": chunk_index,
                    "chunk_metadata": chunk_metadata
                }
            )
            change_ids.append(change_id)
        
        logger.info(f"Tracked {len(change_ids)} changes from chunk {chunk_index}")
        
        return {
            "chunk_index": chunk_index,
            "changes_tracked": len(change_ids),
            "change_ids": change_ids,
            "processing_stage": processing_stage
        }
    
    def get_changes_by_field(self, field_name: str) -> List[ChangeRecord]:
        """Get all changes for a specific field."""
        return [change for change in self.changes if change.field_name == field_name]
    
    def get_changes_by_record(self, record_index: int) -> List[ChangeRecord]:
        """Get all changes for a specific record."""
        return [change for change in self.changes if change.record_index == record_index]
    
    def get_changes_by_stage(self, processing_stage: str) -> List[ChangeRecord]:
        """Get all changes from a specific processing stage."""
        return [change for change in self.changes if change.processing_stage == processing_stage]
    
    def get_changes_by_type(self, change_type: ChangeType) -> List[ChangeRecord]:
        """Get all changes of a specific type."""
        return [change for change in self.changes if change.change_type == change_type]
    
    def get_change_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about tracked changes."""
        if not self.changes:
            return {"total_changes": 0}
        
        # Count by type
        type_counts = {}
        for change_type in ChangeType:
            type_counts[change_type.value] = len(self.get_changes_by_type(change_type))
        
        # Count by confidence
        confidence_counts = {}
        for confidence in ConfidenceLevel:
            confidence_counts[confidence.value] = len([
                c for c in self.changes if c.confidence == confidence
            ])
        
        # Count by field
        field_counts = {}
        for change in self.changes:
            field_counts[change.field_name] = field_counts.get(change.field_name, 0) + 1
        
        # Count by stage
        stage_counts = {}
        for change in self.changes:
            stage_counts[change.processing_stage] = stage_counts.get(change.processing_stage, 0) + 1
        
        # Calculate time range
        timestamps = [datetime.fromisoformat(c.timestamp) for c in self.changes]
        time_range = (max(timestamps) - min(timestamps)).total_seconds() if len(timestamps) > 1 else 0
        
        return {
            "total_changes": len(self.changes),
            "changes_by_type": type_counts,
            "changes_by_confidence": confidence_counts,
            "changes_by_field": field_counts,
            "changes_by_stage": stage_counts,
            "unique_records_affected": len(set(c.record_index for c in self.changes)),
            "unique_fields_affected": len(set(c.field_name for c in self.changes)),
            "time_range_seconds": time_range,
            "session_duration_seconds": (datetime.now() - self.session_start_time).total_seconds(),
            "most_changed_field": max(field_counts, key=field_counts.get) if field_counts else None,
            "most_common_change_type": max(type_counts, key=type_counts.get) if type_counts else None
        }
    
    def generate_change_report(self) -> Dict[str, Any]:
        """Generate a comprehensive change report."""
        statistics = self.get_change_statistics()
        
        # Group changes by record for detailed analysis
        record_changes = {}
        for change in self.changes:
            if change.record_index not in record_changes:
                record_changes[change.record_index] = []
            record_changes[change.record_index].append(change.to_dict())
        
        # Find records with most changes
        records_by_change_count = sorted(
            record_changes.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )[:10]  # Top 10 most changed records
        
        return {
            "session_id": self.session_id,
            "report_generated_at": datetime.now().isoformat(),
            "session_start_time": self.session_start_time.isoformat(),
            "statistics": statistics,
            "all_changes": [change.to_dict() for change in self.changes],
            "changes_by_record": record_changes,
            "most_changed_records": records_by_change_count,
            "processing_summary": {
                "total_processing_stages": len(set(c.processing_stage for c in self.changes)),
                "stages_used": list(set(c.processing_stage for c in self.changes))
            }
        }
    
    def export_changes_to_json(self, file_path: str) -> bool:
        """Export all changes to a JSON file."""
        try:
            report = self.generate_change_report()
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Exported {len(self.changes)} changes to {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error exporting changes to {file_path}: {str(e)}")
            return False
    
    def import_changes_from_json(self, file_path: str) -> bool:
        """Import changes from a JSON file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Clear existing changes
            self.changes.clear()
            
            # Import changes
            for change_data in data.get("all_changes", []):
                change_record = ChangeRecord(
                    change_id=change_data["change_id"],
                    record_index=change_data["record_index"],
                    field_name=change_data["field_name"],
                    original_value=change_data["original_value"],
                    new_value=change_data["new_value"],
                    change_type=ChangeType(change_data["change_type"]),
                    confidence=ConfidenceLevel(change_data["confidence"]),
                    reason=change_data["reason"],
                    timestamp=change_data["timestamp"],
                    processing_stage=change_data["processing_stage"],
                    llm_explanation=change_data["llm_explanation"],
                    validation_status=change_data["validation_status"],
                    metadata=change_data["metadata"]
                )
                self.changes.append(change_record)
            
            # Update session info
            self.session_id = data.get("session_id", self.session_id)
            self.change_counter = len(self.changes)
            
            logger.info(f"Imported {len(self.changes)} changes from {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error importing changes from {file_path}: {str(e)}")
            return False
    
    def validate_change_integrity(self) -> Dict[str, Any]:
        """Validate the integrity of tracked changes."""
        issues = []
        
        # Check for duplicate change IDs
        change_ids = [c.change_id for c in self.changes]
        duplicate_ids = set([x for x in change_ids if change_ids.count(x) > 1])
        if duplicate_ids:
            issues.append(f"Duplicate change IDs found: {duplicate_ids}")
        
        # Check for chronological consistency
        for change in self.changes:
            try:
                datetime.fromisoformat(change.timestamp)
            except ValueError:
                issues.append(f"Invalid timestamp in change {change.change_id}: {change.timestamp}")
        
        # Check for missing required fields
        for change in self.changes:
            if not change.field_name:
                issues.append(f"Missing field name in change {change.change_id}")
            if change.original_value is None and change.new_value is None:
                issues.append(f"Both original and new values are None in change {change.change_id}")
        
        return {
            "is_valid": len(issues) == 0,
            "issues_found": issues,
            "total_changes_validated": len(self.changes)
        }
    
    def clear_changes(self) -> None:
        """Clear all tracked changes."""
        self.changes.clear()
        self.change_counter = 0
        logger.info(f"Cleared all changes for session {self.session_id}")
