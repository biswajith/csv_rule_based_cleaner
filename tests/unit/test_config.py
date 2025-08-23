"""
Unit tests for configuration management.
"""

import pytest
import os
from unittest.mock import patch, MagicMock

from src.csv_cleaner.config.settings import Settings, validate_settings, create_directories


class TestSettings:
    """Test settings configuration."""
    
    def test_default_settings(self):
        """Test default settings values."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}, clear=True):
            settings = Settings()
            
            assert settings.openai_api_key == "test_key"
            assert settings.openai_model == "gpt-4"
            assert settings.chunk_size == 250
            assert settings.max_retries == 1
            assert settings.log_level == "INFO"
    
    def test_environment_override(self):
        """Test that environment variables override defaults."""
        env_vars = {
            "OPENAI_API_KEY": "env_key",
            "OPENAI_MODEL": "gpt-3.5-turbo",
            "CHUNK_SIZE": "500",
            "MAX_RETRIES": "3",
            "LOG_LEVEL": "DEBUG"
        }
        
        with patch.dict(os.environ, env_vars, clear=True):
            settings = Settings()
            
            assert settings.openai_api_key == "env_key"
            assert settings.openai_model == "gpt-3.5-turbo"
            assert settings.chunk_size == 500
            assert settings.max_retries == 3
            assert settings.log_level == "DEBUG"
    
    def test_validate_settings_success(self):
        """Test successful settings validation."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "valid_key"}, clear=True):
            assert validate_settings() is True
    
    def test_validate_settings_missing_api_key(self):
        """Test validation failure with missing API key."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="OPENAI_API_KEY environment variable is required"):
                validate_settings()
    
    def test_validate_settings_invalid_chunk_size(self):
        """Test validation failure with invalid chunk size."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key", "CHUNK_SIZE": "0"}, clear=True):
            with pytest.raises(ValueError, match="CHUNK_SIZE must be greater than 0"):
                validate_settings()
    
    def test_validate_settings_invalid_retries(self):
        """Test validation failure with invalid retry count."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key", "MAX_RETRIES": "-1"}, clear=True):
            with pytest.raises(ValueError, match="MAX_RETRIES must be 0 or greater"):
                validate_settings()
    
    @patch('pathlib.Path.mkdir')
    def test_create_directories(self, mock_mkdir):
        """Test directory creation."""
        create_directories()
        
        # Should create logs, output, and temp directories
        assert mock_mkdir.call_count == 3
        
        # Check that mkdir was called with exist_ok=True
        for call in mock_mkdir.call_args_list:
            assert call[1]['exist_ok'] is True
