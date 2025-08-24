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
    openai_max_tokens: int = Field(default=8000, env="OPENAI_MAX_TOKENS")
    openai_temperature: float = Field(default=0.1, env="OPENAI_TEMPERATURE")
    
    # Operation-specific token limits
    rule_expansion_max_tokens: int = Field(default=8000, env="RULE_EXPANSION_MAX_TOKENS")
    data_cleaning_max_tokens: int = Field(default=30000, env="DATA_CLEANING_MAX_TOKENS")  # Increased for complex change logs
    validation_max_tokens: int = Field(default=10000, env="VALIDATION_MAX_TOKENS")  # For validation operations
    
    # Processing Configuration
    chunk_size: int = Field(default=50, env="CHUNK_SIZE")  # Reduced from 250 to 50 for better reliability
    max_retries: int = Field(default=1, env="MAX_RETRIES")
    retry_delay: float = Field(default=1.0, env="RETRY_DELAY")
    enable_parallel_processing: bool = Field(default=True, env="ENABLE_PARALLEL_PROCESSING")
    
    # Logging Configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: Optional[str] = Field(default="logs/csv_cleaner.log", env="LOG_FILE")
    enable_detailed_llm_logging: bool = Field(default=True, env="ENABLE_DETAILED_LLM_LOGGING")
    
    # Output Configuration
    output_format: str = Field(default="json", env="OUTPUT_FORMAT")
    include_change_log: bool = Field(default=True, env="INCLUDE_CHANGE_LOG")
    generate_summary: bool = Field(default=True, env="GENERATE_SUMMARY")
    
    # Performance Configuration
    max_concurrent_requests: int = Field(default=5, env="MAX_CONCURRENT_REQUESTS")
    request_timeout: float = Field(default=600.0, env="REQUEST_TIMEOUT")
    rate_limit_requests_per_minute: int = Field(default=60, env="RATE_LIMIT_REQUESTS_PER_MINUTE")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


class LLMPromptSettings:
    """Prompt templates and settings for LLM interactions."""
    
    # Rule Expansion Prompt - With comprehensive validation requirements
    RULE_EXPANSION_PROMPT = """
    Expand these cleaning rules for CSV columns:
    
    Rules: {user_rules}
    Columns: {column_headers}
    
    🚨 CRITICAL REQUIREMENTS TO INCLUDE IN ALL EXPANDED RULES:
    1. PRESERVE ALL RECORDS - Never remove, discard, or delete any records
    2. Document all issues and fixes in detailed change tracking
    3. Validate geographic data against known city-state combinations
    4. Convert phone number letters using exact keypad mapping
    5. For email, if the error is simple like have a double @@, then you can fix it. But if the error is more complex like the email is invalid, then you should not fix it.
    
    MANDATORY GEOGRAPHIC VALIDATION (MUST INCLUDE IN EXPANDED RULES):
    🚨 For the following specific cities, ALWAYS validate and correct the state:
    🚨 IF city="Miami" and state≠"FL", CORRECT state to "FL" AND add change_log with field="state" and issue="Geographic correction: Miami is in FL"
    🚨 IF city="Austin" and state≠"TX", CORRECT state to "TX" AND add change_log with field="state" and issue="Geographic correction: Austin is in TX"
    🚨 IF city="Denver" and state≠"CO", CORRECT state to "CO" AND add change_log with field="state" and issue="Geographic correction: Denver is in CO"
    🚨 IF city="Seattle" and state≠"WA", CORRECT state to "WA" AND add change_log with field="state" and issue="Geographic correction: Seattle is in WA"
    🚨 IF city="Portland" and state≠"OR", CORRECT state to "OR" AND add change_log with field="state" and issue="Geographic correction: Portland is in OR"
    🚨 IF city="Boston" and state≠"MA", CORRECT state to "MA" AND add change_log with field="state" and issue="Geographic correction: Boston is in MA"
    
    CRITICAL: These IF-THEN rules are NON-NEGOTIABLE - always check and correct these specific cities!
    CRITICAL: When you make geographic corrections, you MUST log them in change_log!
    
    PHONE NUMBER CONVERSION MAPPING:
    A=2, B=2, C=2, D=3, E=3, F=3, G=4, H=4, I=4, J=5, K=5, L=5, M=6, N=6, O=6, P=7, Q=7, R=7, S=7, T=8, U=8, V=8, W=9, X=9, Y=9, Z=9
    - ABCD = 2223 (A=2, B=2, C=2, D=3) - ALWAYS this conversion
    - Example: "(555) 123-ABCD" MUST become "(555) 123-2223"
    
    Return comprehensive cleaning instructions that include:
    1. Column-specific validations with change tracking
    2. Cross-field checks (especially city-state validation)
    3. Phone/geo normalization with exact conversion rules
    4. Clear instructions to NEVER remove records, only flag issues
    
    Keep response under 3000 tokens.
    """
    
        # Data Cleaning Prompt - Hybrid approach with explicit validation checklist
    DATA_CLEANING_PROMPT = """Clean the following data according to the provided rules. You are an expert at data cleaning and you are going to be very meticulous about it.

    🚨 MANDATORY VALIDATION CHECKLIST (CHECK EVERY SINGLE RECORD):
    For EACH record, you MUST check these exact conditions and add change_log entries if found:

    Data to clean:
    {data_chunk}

    Rules to apply:
    {expanded_rules}

🚨 GEOGRAPHIC VALIDATION MANDATORY:
For EVERY record, you MUST check if city and state match. If you make ANY geographic corrections, you MUST add an entry to change_log documenting what was changed and why.

🚨 CRITICAL OUTPUT REQUIREMENTS:
1. PRESERVE ALL RECORDS - Output exactly the same number of records as input
2. TRACK ALL VALIDATIONS - Every check above must be performed on every record  
3. USE CHANGE_LOG - Document all issues found, even if you can't fix them
4. NEVER DROP RECORDS - Even if data is invalid, corrupt, or unfixable, keep the record
5. LOG ALL CHANGES - If you correct geographic data, phone numbers, emails, etc., DOCUMENT IT in change_log

⚠️ RECORD PRESERVATION OVERRIDE:
- If a record has invalid data → KEEP IT, add to change_log
- If a record has missing data → KEEP IT, add to change_log  
- If a record has corrupt data → KEEP IT, add to change_log
- If a record cannot be fixed → KEEP IT, add to change_log
- NO EXCEPTIONS - Every input record MUST appear in output

🔢 MANDATORY RECORD COUNT CHECK:
Count input records in {data_chunk} → Your output MUST have exactly the same count
INPUT: {data_chunk} contains [COUNT] records
OUTPUT: Your "cleaned_data" array MUST contain [COUNT] records

OUTPUT FORMAT:
Return valid JSON: {{"cleaned_data": [exactly same number as input], "status": "success"}}
Each record MUST include "change_log" array (empty if no issues)
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
