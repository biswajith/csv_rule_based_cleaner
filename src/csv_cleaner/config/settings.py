"""
Configuration management for CSV Rule-Based Cleaner.
All settings for the LLM-driven data cleaning system.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # OpenAI Configuration
    openai_api_key: str = Field(default="", env="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4", env="OPENAI_MODEL")
    openai_max_tokens: int = Field(default=4000, env="OPENAI_MAX_TOKENS")
    openai_temperature: float = Field(default=0.1, env="OPENAI_TEMPERATURE")
    
    # Processing Configuration
    chunk_size: int = Field(default=250, env="CHUNK_SIZE")
    max_retries: int = Field(default=1, env="MAX_RETRIES")
    retry_delay: float = Field(default=1.0, env="RETRY_DELAY")
    enable_parallel_processing: bool = Field(default=True, env="ENABLE_PARALLEL_PROCESSING")
    
    # Logging Configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: Optional[str] = Field(default="logs/csv_cleaner.log", env="LOG_FILE")
    
    # Output Configuration
    output_format: str = Field(default="json", env="OUTPUT_FORMAT")
    include_change_log: bool = Field(default=True, env="INCLUDE_CHANGE_LOG")
    generate_summary: bool = Field(default=True, env="GENERATE_SUMMARY")
    
    # Performance Configuration
    max_concurrent_requests: int = Field(default=5, env="MAX_CONCURRENT_REQUESTS")
    request_timeout: float = Field(default=30.0, env="REQUEST_TIMEOUT")
    rate_limit_requests_per_minute: int = Field(default=60, env="RATE_LIMIT_REQUESTS_PER_MINUTE")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


class LLMPromptSettings:
    """Prompt templates and settings for LLM interactions."""
    
    # Rule Expansion Prompt
    RULE_EXPANSION_PROMPT = """
    You are a data cleaning expert. Given user-provided rules and CSV column headers, 
    expand these rules into comprehensive data cleaning instructions.
    
    User Rules: {user_rules}
    CSV Columns: {column_headers}
    
    Please provide:
    1. Expanded cleaning rules for each relevant column
    2. Field-specific validation criteria
    3. Cross-field validation requirements
    4. Special handling instructions for phone numbers and geographical data
    
    Format your response as structured instructions that can be applied to data chunks.
    """
    
    # Data Cleaning Prompt
    DATA_CLEANING_PROMPT = """
    You are a data cleaning specialist. Clean the following data chunk according to the provided rules.
    
    Data Chunk:
    {data_chunk}
    
    Cleaning Rules:
    {expanded_rules}
    
    Requirements:
    1. Apply all cleaning rules consistently
    2. Normalize phone numbers to standard format
    3. Validate geographical relationships (state/city/country)
    4. Fix obvious errors and inconsistencies
    5. Maintain data integrity
    
    For each change made:
    - Explain what was changed and why
    - Provide the original and corrected value
    - Indicate confidence level in the correction
    
    Return the cleaned data in the same CSV format with a detailed change log.
    """
    
    # Geographical Validation Prompt
    GEOGRAPHICAL_VALIDATION_PROMPT = """
    You are a geographical data expert. Validate and correct geographical information.
    
    Geographical Data:
    {geo_fields}
    
    Tasks:
    1. Validate state/city/country relationships
    2. Correct obvious geographical inconsistencies
    3. Standardize geographical formats
    4. Flag uncertain corrections for manual review
    
    Provide corrections with explanations and confidence scores.
    """
    
    # Phone Number Normalization Prompt
    PHONE_NORMALIZATION_PROMPT = """
    You are a phone number validation expert. Normalize and validate phone numbers.
    
    Phone Data:
    {phone_data}
    
    Tasks:
    1. Detect and normalize phone number formats
    2. Handle international variations
    3. Remove invalid or incomplete numbers
    4. Standardize to consistent format
    
    Return normalized phone numbers with change explanations.
    """


# Create global settings instance
settings = Settings()


def create_directories():
    """Create necessary directories for the application."""
    directories = [
        "logs",
        "output",
        "temp"
    ]
    
    for directory in directories:
        Path(directory).mkdir(exist_ok=True)


def validate_settings():
    """Validate that all required settings are properly configured."""
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required")
    
    if settings.chunk_size < 1:
        raise ValueError("CHUNK_SIZE must be greater than 0")
    
    if settings.max_retries < 0:
        raise ValueError("MAX_RETRIES must be 0 or greater")
    
    return True


# Initialize directories on module import
create_directories()
