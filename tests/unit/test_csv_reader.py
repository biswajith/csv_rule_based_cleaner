"""
Unit tests for CSV reader functionality.
"""

import pytest
import pandas as pd
import tempfile
import os
from unittest.mock import patch, MagicMock

from src.csv_cleaner.core.csv_reader import CSVReader


class TestCSVReader:
    """Test CSV reading and analysis functionality."""
    
    @pytest.fixture
    def sample_csv_data(self):
        """Create sample CSV data for testing."""
        return pd.DataFrame({
            'first_name': ['John', 'Jane', 'Bob'],
            'last_name': ['Doe', 'Smith', 'Johnson'],
            'email': ['john@test.com', 'jane@test.com', 'bob@test.com'],
            'phone': ['555-1234', '555-5678', '555-9999'],
            'city': ['New York', 'Los Angeles', 'Chicago'],
            'state': ['NY', 'CA', 'IL']
        })
    
    @pytest.fixture
    def temp_csv_file(self, sample_csv_data):
        """Create temporary CSV file for testing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            sample_csv_data.to_csv(f.name, index=False)
            yield f.name
        os.unlink(f.name)
    
    def test_init(self):
        """Test CSV reader initialization."""
        reader = CSVReader()
        
        assert reader.df is None
        assert reader.file_path is None
        assert reader.column_headers == []
        assert reader.total_records == 0
    
    def test_read_csv_success(self, temp_csv_file):
        """Test successful CSV reading."""
        reader = CSVReader()
        df = reader.read_csv(temp_csv_file)
        
        assert df is not None
        assert len(df) == 3
        assert reader.total_records == 3
        assert set(reader.column_headers) == {'first_name', 'last_name', 'email', 'phone', 'city', 'state'}
    
    def test_read_csv_with_encoding(self, sample_csv_data):
        """Test CSV reading with specific encoding."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            sample_csv_data.to_csv(f.name, index=False)
            temp_file = f.name
        
        try:
            reader = CSVReader()
            df = reader.read_csv(temp_file, encoding='utf-8')
            
            assert df is not None
            assert len(df) == 3
        finally:
            os.unlink(temp_file)
    
    def test_read_csv_file_not_found(self):
        """Test error handling for missing file."""
        reader = CSVReader()
        
        with pytest.raises(FileNotFoundError):
            reader.read_csv('nonexistent_file.csv')
    
    def test_get_file_info(self, temp_csv_file):
        """Test file information extraction."""
        reader = CSVReader()
        reader.read_csv(temp_csv_file)
        
        file_info = reader.get_file_info()
        
        assert file_info['total_records'] == 3
        assert file_info['column_count'] == 6
        assert set(file_info['column_headers']) == {'first_name', 'last_name', 'email', 'phone', 'city', 'state'}
        assert file_info['file_size_mb'] > 0
        assert file_info['memory_usage_mb'] > 0
    
    def test_get_file_info_no_data(self):
        """Test file info when no data is loaded."""
        reader = CSVReader()
        
        with pytest.raises(ValueError, match="No CSV file loaded"):
            reader.get_file_info()
    
    def test_get_sample_data(self, temp_csv_file):
        """Test sample data extraction."""
        reader = CSVReader()
        reader.read_csv(temp_csv_file)
        
        sample_data = reader.get_sample_data(n_rows=2)
        
        assert len(sample_data['sample_data']) == 2
        assert sample_data['column_headers'] == reader.column_headers
        assert sample_data['total_records'] == 3
        assert 'data_types' in sample_data
        assert 'null_counts' in sample_data
    
    def test_get_sample_data_no_data(self):
        """Test sample data when no data is loaded."""
        reader = CSVReader()
        
        with pytest.raises(ValueError, match="No CSV file loaded"):
            reader.get_sample_data()
    
    def test_create_chunks(self, temp_csv_file):
        """Test data chunking functionality."""
        reader = CSVReader()
        reader.read_csv(temp_csv_file)
        
        chunks = reader.create_chunks(chunk_size=2)
        
        assert len(chunks) == 2  # 3 records / 2 per chunk = 2 chunks
        
        # Check first chunk
        chunk_index, chunk_df = chunks[0]
        assert chunk_index == 0
        assert len(chunk_df) == 2
        
        # Check second chunk
        chunk_index, chunk_df = chunks[1]
        assert chunk_index == 1
        assert len(chunk_df) == 1
    
    def test_create_chunks_default_size(self, temp_csv_file):
        """Test chunking with default size."""
        reader = CSVReader()
        reader.read_csv(temp_csv_file)
        
        with patch('src.csv_cleaner.config.settings.settings.chunk_size', 250):
            chunks = reader.create_chunks()
            
            # With only 3 records and chunk size 250, should be 1 chunk
            assert len(chunks) == 1
            assert len(chunks[0][1]) == 3
    
    def test_create_chunks_no_data(self):
        """Test chunking when no data is loaded."""
        reader = CSVReader()
        
        with pytest.raises(ValueError, match="No CSV file loaded"):
            reader.create_chunks()
    
    def test_get_chunk_with_headers(self, temp_csv_file):
        """Test chunk preparation with headers."""
        reader = CSVReader()
        reader.read_csv(temp_csv_file)
        
        chunks = reader.create_chunks(chunk_size=2)
        chunk_index, chunk_df = chunks[0]
        
        chunk_data = reader.get_chunk_with_headers(chunk_df)
        
        assert chunk_data['headers'] == reader.column_headers
        assert len(chunk_data['data']) == 2
        assert chunk_data['chunk_size'] == 2
        assert 'data_csv' in chunk_data
    
    def test_detect_potential_field_types(self, temp_csv_file):
        """Test field type detection."""
        reader = CSVReader()
        reader.read_csv(temp_csv_file)
        
        suggestions = reader.detect_potential_field_types()
        
        # Check that appropriate fields are suggested
        assert 'first_name' in suggestions['potential_name_fields']
        assert 'last_name' in suggestions['potential_name_fields']
        assert 'email' in suggestions['potential_email_fields']
        assert 'phone' in suggestions['potential_phone_fields']
        assert 'city' in suggestions['potential_geographic_fields']
        assert 'state' in suggestions['potential_geographic_fields']
    
    def test_detect_potential_field_types_no_data(self):
        """Test field type detection when no data is loaded."""
        reader = CSVReader()
        
        with pytest.raises(ValueError, match="No CSV file loaded"):
            reader.detect_potential_field_types()
