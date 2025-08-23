"""
Unit tests for change tracking functionality.
"""

import pytest
import json
import tempfile
import os
from datetime import datetime
from unittest.mock import patch, MagicMock

from src.csv_cleaner.processors.change_tracker import (
    ChangeTracker, ChangeRecord, ChangeType, ConfidenceLevel
)


class TestChangeTracker:
    """Test change tracking functionality."""
    
    @pytest.fixture
    def tracker(self):
        """Create a change tracker for testing."""
        return ChangeTracker("test_session_123")
    
    def test_init(self, tracker):
        """Test change tracker initialization."""
        assert tracker.session_id == "test_session_123"
        assert len(tracker.changes) == 0
        assert tracker.change_counter == 0
        assert tracker.session_start_time is not None
    
    def test_track_change(self, tracker):
        """Test tracking a single change."""
        change_id = tracker.track_change(
            record_index=0,
            field_name="first_name",
            original_value="john",
            new_value="John",
            change_type=ChangeType.NORMALIZED,
            confidence=ConfidenceLevel.HIGH,
            reason="Capitalize first letter",
            processing_stage="name_normalization",
            llm_explanation="Applied proper capitalization",
            metadata={"test": "data"}
        )
        
        assert change_id == "test_session_123_change_000001"
        assert len(tracker.changes) == 1
        assert tracker.change_counter == 1
        
        change = tracker.changes[0]
        assert change.record_index == 0
        assert change.field_name == "first_name"
        assert change.original_value == "john"
        assert change.new_value == "John"
        assert change.change_type == ChangeType.NORMALIZED
        assert change.confidence == ConfidenceLevel.HIGH
        assert change.reason == "Capitalize first letter"
        assert change.metadata["test"] == "data"
    
    def test_track_multiple_changes(self, tracker):
        """Test tracking multiple changes for a record."""
        changes_data = [
            {
                "field_name": "first_name",
                "original_value": "john",
                "new_value": "John",
                "change_type": "normalized",
                "confidence": "high",
                "reason": "Capitalize name"
            },
            {
                "field_name": "email",
                "original_value": "JOHN@TEST.COM",
                "new_value": "john@test.com",
                "change_type": "normalized",
                "confidence": "high",
                "reason": "Lowercase email"
            }
        ]
        
        change_ids = tracker.track_multiple_changes(
            record_index=0,
            changes_data=changes_data,
            processing_stage="normalization"
        )
        
        assert len(change_ids) == 2
        assert len(tracker.changes) == 2
        assert all(cid.startswith("test_session_123_change_") for cid in change_ids)
    
    def test_track_chunk_processing(self, tracker):
        """Test tracking changes from chunk processing."""
        chunk_changes = [
            {
                "record_index": 0,  # Relative to chunk
                "field_name": "phone",
                "original_value": "5551234567",
                "new_value": "(555) 123-4567",
                "change_type": "normalized",
                "confidence": "high",
                "reason": "Format phone number"
            },
            {
                "record_index": 1,
                "field_name": "state",
                "original_value": "New York",
                "new_value": "NY",
                "change_type": "standardized",
                "confidence": "high",
                "reason": "Use state abbreviation"
            }
        ]
        
        chunk_metadata = {
            "start_row": 100,
            "chunk_size": 50
        }
        
        result = tracker.track_chunk_processing(
            chunk_index=2,
            chunk_changes=chunk_changes,
            processing_stage="chunk_processing",
            chunk_metadata=chunk_metadata
        )
        
        assert result["chunk_index"] == 2
        assert result["changes_tracked"] == 2
        assert len(tracker.changes) == 2
        
        # Check that absolute record indices are calculated correctly
        assert tracker.changes[0].record_index == 100  # 100 + 0
        assert tracker.changes[1].record_index == 101  # 100 + 1
        
        # Check metadata includes chunk information
        assert tracker.changes[0].metadata["chunk_index"] == 2
        assert tracker.changes[0].metadata["chunk_metadata"]["start_row"] == 100
    
    def test_get_changes_by_field(self, tracker):
        """Test retrieving changes by field name."""
        # Add some test changes
        tracker.track_change(0, "first_name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(1, "first_name", "jane", "Jane", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(0, "email", "test@test.com", "test@example.com", ChangeType.CORRECTED, ConfidenceLevel.MEDIUM, "test", "stage1")
        
        first_name_changes = tracker.get_changes_by_field("first_name")
        email_changes = tracker.get_changes_by_field("email")
        
        assert len(first_name_changes) == 2
        assert len(email_changes) == 1
        assert all(c.field_name == "first_name" for c in first_name_changes)
        assert email_changes[0].field_name == "email"
    
    def test_get_changes_by_record(self, tracker):
        """Test retrieving changes by record index."""
        tracker.track_change(0, "first_name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(0, "email", "test@test.com", "test@example.com", ChangeType.CORRECTED, ConfidenceLevel.MEDIUM, "test", "stage1")
        tracker.track_change(1, "first_name", "jane", "Jane", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        
        record_0_changes = tracker.get_changes_by_record(0)
        record_1_changes = tracker.get_changes_by_record(1)
        
        assert len(record_0_changes) == 2
        assert len(record_1_changes) == 1
        assert all(c.record_index == 0 for c in record_0_changes)
        assert record_1_changes[0].record_index == 1
    
    def test_get_change_statistics(self, tracker):
        """Test change statistics calculation."""
        # Add various types of changes
        tracker.track_change(0, "name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(1, "name", "jane", "Jane", ChangeType.NORMALIZED, ConfidenceLevel.MEDIUM, "test", "stage1")
        tracker.track_change(0, "email", "bad", "good@test.com", ChangeType.CORRECTED, ConfidenceLevel.LOW, "test", "stage2")
        
        stats = tracker.get_change_statistics()
        
        assert stats["total_changes"] == 3
        assert stats["unique_records_affected"] == 2
        assert stats["unique_fields_affected"] == 2
        assert stats["changes_by_type"][ChangeType.NORMALIZED.value] == 2
        assert stats["changes_by_type"][ChangeType.CORRECTED.value] == 1
        assert stats["changes_by_confidence"][ConfidenceLevel.HIGH.value] == 1
        assert stats["changes_by_confidence"][ConfidenceLevel.MEDIUM.value] == 1
        assert stats["changes_by_confidence"][ConfidenceLevel.LOW.value] == 1
        assert stats["most_changed_field"] == "name"
        assert stats["most_common_change_type"] == ChangeType.NORMALIZED.value
    
    def test_get_change_statistics_empty(self, tracker):
        """Test statistics when no changes exist."""
        stats = tracker.get_change_statistics()
        
        assert stats["total_changes"] == 0
    
    def test_generate_change_report(self, tracker):
        """Test comprehensive change report generation."""
        tracker.track_change(0, "name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(0, "email", "bad", "good@test.com", ChangeType.CORRECTED, ConfidenceLevel.MEDIUM, "test", "stage1")
        
        report = tracker.generate_change_report()
        
        assert report["session_id"] == "test_session_123"
        assert "report_generated_at" in report
        assert "statistics" in report
        assert "all_changes" in report
        assert "changes_by_record" in report
        assert "most_changed_records" in report
        assert "processing_summary" in report
        
        assert len(report["all_changes"]) == 2
        assert 0 in report["changes_by_record"]
        assert len(report["changes_by_record"][0]) == 2
    
    def test_export_and_import_changes(self, tracker):
        """Test exporting and importing changes to/from JSON."""
        # Add some test changes
        tracker.track_change(0, "name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(1, "email", "bad", "good@test.com", ChangeType.CORRECTED, ConfidenceLevel.MEDIUM, "test", "stage1")
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        
        try:
            # Export changes
            success = tracker.export_changes_to_json(temp_file)
            assert success is True
            
            # Verify file was created and contains data
            assert os.path.exists(temp_file)
            with open(temp_file, 'r') as f:
                data = json.load(f)
            assert len(data["all_changes"]) == 2
            
            # Create new tracker and import
            new_tracker = ChangeTracker("imported_session")
            import_success = new_tracker.import_changes_from_json(temp_file)
            assert import_success is True
            
            # Verify imported data
            assert len(new_tracker.changes) == 2
            assert new_tracker.session_id == "test_session_123"  # Should update from imported data
            assert new_tracker.change_counter == 2
            
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)
    
    def test_validate_change_integrity(self, tracker):
        """Test change integrity validation."""
        # Add valid changes
        tracker.track_change(0, "name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(1, "email", "bad", "good@test.com", ChangeType.CORRECTED, ConfidenceLevel.MEDIUM, "test", "stage1")
        
        validation = tracker.validate_change_integrity()
        
        assert validation["is_valid"] is True
        assert len(validation["issues_found"]) == 0
        assert validation["total_changes_validated"] == 2
    
    def test_validate_change_integrity_with_issues(self, tracker):
        """Test integrity validation with problematic data."""
        # Add a change with missing field name
        change = ChangeRecord(
            change_id="test_001",
            record_index=0,
            field_name="",  # Missing field name
            original_value="test",
            new_value="Test",
            change_type=ChangeType.NORMALIZED,
            confidence=ConfidenceLevel.HIGH,
            reason="test",
            timestamp="invalid_timestamp",  # Invalid timestamp
            processing_stage="test",
            llm_explanation="test",
            validation_status="pending",
            metadata={}
        )
        tracker.changes.append(change)
        
        validation = tracker.validate_change_integrity()
        
        assert validation["is_valid"] is False
        assert len(validation["issues_found"]) > 0
    
    def test_clear_changes(self, tracker):
        """Test clearing all changes."""
        # Add some changes
        tracker.track_change(0, "name", "john", "John", ChangeType.NORMALIZED, ConfidenceLevel.HIGH, "test", "stage1")
        tracker.track_change(1, "email", "bad", "good@test.com", ChangeType.CORRECTED, ConfidenceLevel.MEDIUM, "test", "stage1")
        
        assert len(tracker.changes) == 2
        assert tracker.change_counter == 2
        
        tracker.clear_changes()
        
        assert len(tracker.changes) == 0
        assert tracker.change_counter == 0
