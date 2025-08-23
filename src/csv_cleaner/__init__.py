"""
CSV Rule-Based Cleaner with LLM Assistance

A Python-based system that cleans person/lead information from CSV files using 
Large Language Models (LLM) for intelligent data validation and cleaning.
"""

__version__ = "0.1.0"
__author__ = "CSV Cleaner Team"
__description__ = "LLM-driven CSV data cleaning system"

from .core.cleaner import CSVCleaner
from .core.processor import DataProcessor
from .core.rule_manager import RuleManager
from .core.validation_manager import ValidationManager

from .processors.chunk_processor import ChunkProcessor

__all__ = [
    "CSVCleaner",
    "DataProcessor",
    "RuleManager",
    "ValidationManager",
    "ChunkProcessor"
]
