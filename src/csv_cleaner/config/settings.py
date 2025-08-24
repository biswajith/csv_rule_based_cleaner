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
    rule_expansion_max_tokens: int = Field(default=5000, env="RULE_EXPANSION_MAX_TOKENS")
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
    5. For email, if the error is simple like have a double @@, then you can fix it. Use best effort to fix the email.
    
    MANDATORY GEOGRAPHIC VALIDATION (MUST INCLUDE IN EXPANDED RULES):
    🚨 For the following specific cities, ALWAYS validate and correct the state:
    🚨 IF city="Miami" and state≠"FL", CORRECT state to "FL" AND add change_log with field="state" and issue="Geographic correction: Miami is in FL"
    🚨 IF city="Austin" and state≠"TX", CORRECT state to "TX" AND add change_log with field="state" and issue="Geographic correction: Austin is in TX"
    🚨 IF city="Denver" and state≠"CO", CORRECT state to "CO" AND add change_log with field="state" and issue="Geographic correction: Denver is in CO"
    🚨 IF city="Seattle" and state≠"WA", CORRECT state to "WA" AND add change_log with field="state" and issue="Geographic correction: Seattle is in WA"
    🚨 IF city="Portland" and state≠"OR", CORRECT state to "OR" AND add change_log with field="state" and issue="Geographic correction: Portland is in OR"
    🚨 IF city="Boston" and state≠"MA", CORRECT state to "MA" AND add change_log with field="state" and issue="Geographic correction: Boston is in MA"
     🚨 In case of zip code not matching the city, correct the zip code to match the city. For example if city is Portland and zip code is 26505, then flag the issue, so that user can review and correct it.

    
    CRITICAL: These IF-THEN rules are NON-NEGOTIABLE - always check and correct these specific cities!
    CRITICAL: When you make geographic corrections, you MUST log them in change_log!Any change that is not logged or documented can be considered as a bug.
    
    PHONE NUMBER CONVERSION MAPPING:
    A=2, B=2, C=2, D=3, E=3, F=3, G=4, H=4, I=4, J=5, K=5, L=5, M=6, N=6, O=6, P=7, Q=7, R=7, S=7, T=8, U=8, V=8, W=9, X=9, Y=9, Z=9
    - ABCD = 2223 (A=2, B=2, C=2, D=3) - ALWAYS this conversion
    - Example: "(555) 123-ABCD" MUST become "(555) 123-2223"
    
    Return comprehensive cleaning instructions that include:
    1. Column-specific validations with change tracking
    2. Cross-field checks (especially city-state, city-zip code, state-zip code, state-country validation)
    3. Phone/geo normalization with exact conversion rules
    4. Clear instructions to NEVER remove records, only flag issues
    
    Keep response under 3000 tokens.
    """
    
        # Data Cleaning Prompt - Hybrid approach with explicit validation checklist
    DATA_CLEANING_PROMPT = """Clean the following data according to the provided rules. You are an expert at data cleaning and you are going to be very meticulous about it.

    🚨 CRITICAL: CONSISTENCY BETWEEN DATA AND CHANGE_LOG IS MANDATORY
    - If you log a change, the actual field value MUST reflect that change
    - If you don't change a field, don't log it as changed
    - Your change_log must be 100% accurate to the actual data changes made

    🚨 MANDATORY VALIDATION CHECKLIST & CHANGE LOGGING (CHECK EVERY SINGLE RECORD):
    1. For EACH record, you MUST check these all these conditions.
    2. For EVERY field modification you make (not just issues):
       - Original value → New value = MUST be logged
       - The actual field value MUST match the "corrected_value" in change_log
    3. If you are not able to fix it, add the issue to the change_log with just the original value and call it an issue.
    4. Don't skip these steps for any record.

    🚨 MANDATORY PHONE NUMBER LOGGING:
    ANY phone number change (formatting, letter conversion, etc.) MUST be logged:
    ✅ 555-1234 → (555) 123-4 = MUST LOG with original and corrected values
    ✅ 555-ABCD-1234 → (555) 223-1234 = MUST LOG the letter conversion
    ✅ 1234567890 → (123) 456-7890 = MUST LOG the formatting change
    ❌ NEVER skip phone number change logging

    🚨 CHANGE LOGGING EXAMPLES:
    ✅ State change: Miami,CA → Miami,FL = LOG IT (data must show FL)
    ✅ Phone format: 555-1234 → (555) 123-4 = LOG IT (data must show (555) 123-4)
    ✅ Email flag: invalid@domain → keep as-is but LOG IT (data unchanged, issue logged)
    ❌ NEVER log changes you didn't actually make to the data

    🚨 MANDATORY GEOGRAPHIC CORRECTIONS (CHECK EVERY RECORD):
    - IF city="Miami" and state≠"FL" → CORRECT state to "FL" AND log change
    - IF city="Austin" and state≠"TX" → CORRECT state to "TX" AND log change  
    CRITICAL: Every geographic correction MUST be applied to data AND logged!

    Data to clean:
    {data_chunk}

    Rules to apply:
    {expanded_rules}

🚨 CRITICAL OUTPUT REQUIREMENTS:
1. PRESERVE ALL RECORDS - Output exactly the same number of records as 
2. NEVER DROP RECORDS - Even if data is invalid, corrupt, or unfixable, keep the record
3. Return the records in the same order as the input.

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

🚨 FINAL CONSISTENCY CHECK BEFORE RETURNING:
- Verify each change_log entry matches the actual field value in the cleaned data
- If change_log says state was corrected to "FL", make sure state field actually shows "FL"
- If change_log says phone was formatted to "(555) 123-4", make sure phone field shows "(555) 123-4"
- NO EXCEPTIONS: change_log must be 100% accurate to actual data

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
