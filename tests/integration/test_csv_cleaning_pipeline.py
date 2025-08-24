"""
Integration tests for the complete CSV cleaning pipeline.
"""

import pytest
import tempfile
import os
import pandas as pd
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.csv_cleaner import CSVCleaner, DataProcessor


class TestCSVCleaningPipeline:
    """Integration tests for the complete CSV cleaning pipeline."""
    
    @pytest.fixture
    def sample_messy_data(self):
        """Create sample messy data for testing."""
        return pd.DataFrame({
            'first_name': ['john', 'JANE', 'bob', '', 'alice'],
            'last_name': ['DOE', 'smith', 'JOHNSON', 'Brown', ''],
            'email': ['john@test', 'jane@email.com', 'invalid_email', 'bob@test.co.uk', 'alice@company.org'],
            'phone': ['5551234567', '(555) 987-6543', '555.555.5555', '+1-555-123-4567', '123-456-7890'],
            'city': ['new york', 'Los Angeles', 'CHICAGO', 'Miami', 'Boston'],
            'state': ['NY', 'CA', 'IL', 'FL', 'Massachusetts'],
            'country': ['USA', 'United States', 'US', 'United States of America', 'USA']
        })
    
    @pytest.fixture
    def temp_csv_file(self, sample_messy_data):
        """Create temporary CSV file with messy data."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            sample_messy_data.to_csv(f.name, index=False)
            yield f.name
        os.unlink(f.name)
    
    @pytest.fixture
    def mock_llm_responses(self):
        """Mock LLM responses for testing."""
        return {
            "rule_expansion": """
            {
                "expanded_rules": "1. Normalize names to proper case (First Last)\\n2. Validate email formats\\n3. Standardize phone numbers to (XXX) XXX-XXXX\\n4. Use standard state abbreviations\\n5. Standardize country names to 'United States'",
                "field_specific_rules": {
                    "first_name": "Capitalize first letter only",
                    "last_name": "Capitalize first letter only", 
                    "email": "Validate format and lowercase",
                    "phone": "Format as (XXX) XXX-XXXX",
                    "state": "Use 2-letter abbreviations",
                    "country": "Standardize to 'United States'"
                }
            }
            """,
            "data_cleaning": """
            {
                "cleaned_data": [
                    {
                        "first_name": "John",
                        "last_name": "Doe", 
                        "email": "john@test.com",
                        "phone": "(555) 123-4567",
                        "city": "New York",
                        "state": "NY",
                        "country": "United States"
                    }
                ],
                "changes": [
                    {
                        "field": "first_name",
                        "original_value": "john",
                        "new_value": "John",
                        "reason": "Capitalized name",
                        "confidence": "high"
                    }
                ]
            }
            """,
            "quality_analysis": """
            {
                "overall_quality": "fair",
                "issues_found": [
                    "Inconsistent name capitalization",
                    "Invalid email formats",
                    "Mixed phone number formats",
                    "Inconsistent country naming"
                ],
                "recommendations": [
                    "Standardize name formatting",
                    "Validate email addresses",
                    "Normalize phone numbers",
                    "Use consistent country names"
                ]
            }
            """
        }
    
    @pytest.mark.asyncio
    async def test_complete_cleaning_pipeline_mock(self, temp_csv_file, mock_llm_responses):
        """Test the complete cleaning pipeline with mocked LLM responses."""
        # Mock the LLM client to return predefined responses
        with patch('src.csv_cleaner.core.llm_client.LLMClient') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm_class.return_value = mock_llm
            
            # Configure mock responses
            mock_llm.expand_rules.return_value = {
                "status": "success",
                "expanded_rules": mock_llm_responses["rule_expansion"]
            }
            
            mock_llm.clean_data_chunk.return_value = {
                "status": "success",
                "cleaned_data": mock_llm_responses["data_cleaning"],
                "changes": []
            }
            

            
            # Test the pipeline
            cleaner = CSVCleaner()
            
            rules = [
                "Names should be properly capitalized",
                "Email addresses must be valid format",
                "Phone numbers should be in (XXX) XXX-XXXX format",
                "States should use 2-letter abbreviations",
                "Country names should be standardized"
            ]
            
            # Run the cleaning process
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = os.path.join(temp_dir, "cleaned_output")
                
                results = await cleaner.clean_csv(temp_csv_file, rules, output_path)
                
                # Verify results structure
                assert results["status"] in ["completed", "success"]
                assert "session_id" in results
                assert "processing_summary" in results
                assert "output_files" in results
                
                # Verify LLM interactions
                assert mock_llm.expand_rules.called

    
    @pytest.mark.asyncio
    async def test_data_processor_interface(self, temp_csv_file):
        """Test the high-level DataProcessor interface."""
        with patch('src.csv_cleaner.core.llm_client.LLMClient') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm_class.return_value = mock_llm
            
            # Configure basic mock responses
            mock_llm.expand_rules.return_value = {"status": "success", "expanded_rules": "test rules"}

            mock_llm.clean_data_chunk.return_value = {"status": "success", "cleaned_data": [], "changes": []}
            
            processor = DataProcessor()
            
            rules = ["Test rule 1", "Test rule 2"]
            
            # Test processing
            results = await processor.process_file(temp_csv_file, rules)
            
            # Verify basic functionality
            assert "status" in results
            assert "session_id" in results
            
            # Test session info
            session_info = processor.get_session_info()
            assert "session_id" in session_info
            assert "has_loaded_file" in session_info
    
    @pytest.mark.asyncio 
    async def test_preview_functionality(self, temp_csv_file):
        """Test preview functionality before full processing."""
        with patch('src.csv_cleaner.core.llm_client.LLMClient') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm_class.return_value = mock_llm
            
            # Configure mock responses for preview
            mock_llm.expand_rules.return_value = {"status": "success", "expanded_rules": "preview rules"}
            mock_llm.clean_data_chunk.return_value = {
                "status": "success", 
                "cleaned_data": [{"test": "data"}],
                "changes": [{"field": "test", "change": "preview"}]
            }
            
            cleaner = CSVCleaner()
            
            rules = ["Preview test rule"]
            
            # Test preview
            preview_results = await cleaner.preview_cleaning(temp_csv_file, rules, preview_rows=3)
            
            assert preview_results["status"] == "success"
            assert "preview_data" in preview_results
            assert "rule_expansion" in preview_results
            assert "preview_result" in preview_results
            assert "file_info" in preview_results
            
            # Verify preview data structure
            preview_data = preview_results["preview_data"]
            assert "column_headers" in preview_data
            assert "sample_data" in preview_data
            assert len(preview_data["sample_data"]) <= 3  # Should respect preview_rows limit
    
    @pytest.mark.asyncio
    async def test_error_handling_in_pipeline(self, temp_csv_file):
        """Test error handling throughout the pipeline."""
        with patch('src.csv_cleaner.core.llm_client.LLMClient') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm_class.return_value = mock_llm
            
            # Configure LLM to return error for rule expansion
            mock_llm.expand_rules.return_value = {
                "status": "error",
                "error": "LLM API error"
            }
            
            cleaner = CSVCleaner()
            rules = ["Test rule"]
            
            # Test that pipeline handles LLM errors gracefully
            results = await cleaner.clean_csv(temp_csv_file, rules)
            
            # Should return error status but not crash
            assert results["status"] == "error"
            assert "error" in results
    
    @pytest.mark.asyncio
    async def test_chunked_processing(self, temp_csv_file):
        """Test processing with chunked data."""
        # Create larger dataset for chunking
        large_data = pd.DataFrame({
            'name': [f'Person_{i}' for i in range(100)],
            'email': [f'person{i}@test.com' for i in range(100)],
            'value': list(range(100))
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            large_data.to_csv(f.name, index=False)
            large_file = f.name
        
        try:
            with patch('src.csv_cleaner.core.llm_client.LLMClient') as mock_llm_class:
                mock_llm = AsyncMock()
                mock_llm_class.return_value = mock_llm
                
                # Configure mock responses
                mock_llm.expand_rules.return_value = {"status": "success", "expanded_rules": "chunk test rules"}
    
                mock_llm.clean_data_chunk.return_value = {"status": "success", "cleaned_data": [], "changes": []}
                
                # Set small chunk size for testing
                with patch('src.csv_cleaner.config.settings.settings.chunk_size', 10):
                    cleaner = CSVCleaner()
                    rules = ["Test chunking rule"]
                    
                    results = await cleaner.clean_csv(large_file, rules)
                    
                    # Verify processing occurred
                    assert "status" in results
                    assert "processing_summary" in results
                    
                    # Should have processed multiple chunks
                    processing_summary = results.get("processing_summary", {})
                    chunks_processed = processing_summary.get("chunks_processed", 0)
                    
                    # With 100 records and chunk size 10, should process 10 chunks
                    # (exact number depends on success rate)
                    assert chunks_processed > 0
        
        finally:
            os.unlink(large_file)
    
    def test_session_management(self):
        """Test session management and reset functionality."""
        processor = DataProcessor()
        
        # Get initial session info
        initial_session = processor.get_session_info()
        initial_session_id = initial_session["session_id"]
        
        # Reset session
        processor.reset_session()
        
        # Get new session info
        new_session = processor.get_session_info()
        new_session_id = new_session["session_id"]
        
        # Session ID should be different after reset
        assert initial_session_id != new_session_id
        assert new_session["has_loaded_file"] is False
    
    @pytest.mark.asyncio
    async def test_specialized_validators_integration(self, temp_csv_file):
        """Test integration of specialized validators in the pipeline."""
        with patch('src.csv_cleaner.core.llm_client.LLMClient') as mock_llm_class:
            mock_llm = AsyncMock()
            mock_llm_class.return_value = mock_llm
            
            # Configure mock responses for validators
            mock_llm.expand_rules.return_value = {"status": "success", "expanded_rules": "validator rules"}

            mock_llm.clean_data_chunk.return_value = {"status": "success", "cleaned_data": [], "changes": []}
            
            # Mock ValidationManager response
            with patch('src.csv_cleaner.core.validation_manager.ValidationManager') as mock_validation_manager:
                
                # Configure ValidationManager mock
                mock_validation_instance = AsyncMock()
                mock_validation_instance.validate_comprehensive.return_value = {
                    "status": "success", 
                    "validation_results": "comprehensive validation completed",
                    "records_validated": 5,
                    "validation_types": ["phone", "geography", "cross_field", "business_logic"]
                }
                mock_validation_manager.return_value = mock_validation_instance
                
                cleaner = CSVCleaner()
                rules = ["Test specialized validation"]
                
                results = await cleaner.clean_csv(temp_csv_file, rules)
                
                # Verify comprehensive validation was called
                assert mock_validation_instance.validate_comprehensive.called
                
                # Verify results include specialized processing
                assert "specialized_processing" in results
                specialized = results["specialized_processing"]
                assert "comprehensive_validation" in specialized
