"""
Rule Manager - Centralized rule processing and management.
Handles all rule loading, parsing, and expansion in one place.
"""

from typing import List, Dict, Any, Optional, Union
from pathlib import Path
from loguru import logger

from .llm_client import LLMClient


class RuleManager:
    """
    Centralized manager for all rule processing operations.
    Loads, parses, and expands rules in one place to avoid duplication.
    """
    
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.original_rules: List[str] = []
        self.expanded_rules: str = ""
        self.column_headers: List[str] = []
        self.is_expanded: bool = False
    
    async def initialize_rules(
        self, 
        rules_source: Union[List[str], str, Path],
        column_headers: List[str]
    ) -> Dict[str, Any]:
        """
        Initialize and expand rules for the given CSV structure.
        This is the single entry point for all rule processing.
        
        Args:
            rules_source: Rules as list, file path, or Path object
            column_headers: CSV column headers for context
            
        Returns:
            Initialization result with expanded rules
        """
        try:
            # Step 1: Load/parse rules
            self.original_rules = self._load_rules(rules_source)
            self.column_headers = column_headers
            
            if not self.original_rules:
                logger.warning("No rules provided for processing")
                return {
                    "status": "warning",
                    "message": "No rules to process",
                    "original_rules": [],
                    "expanded_rules": "",
                    "rule_count": 0
                }
            
            # Step 2: Expand rules via LLM
            logger.info(f"Expanding {len(self.original_rules)} rules for {len(column_headers)} columns")
            expansion_result = await self.llm_client.expand_rules(
                self.original_rules, 
                column_headers
            )
            
            if expansion_result.get("status") == "success":
                self.expanded_rules = expansion_result.get("expanded_rules", "")
                self.is_expanded = True
                
                logger.info("Rules successfully expanded and cached")
                return {
                    "status": "success",
                    "original_rules": self.original_rules,
                    "expanded_rules": self.expanded_rules,
                    "rule_count": len(self.original_rules),
                    "column_headers": column_headers
                }
            else:
                logger.error("Failed to expand rules via LLM")
                return {
                    "status": "error",
                    "error": "Failed to expand rules via LLM",
                    "original_rules": self.original_rules,
                    "expanded_rules": "",
                    "rule_count": len(self.original_rules)
                }
                
        except Exception as e:
            logger.error(f"Error initializing rules: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "original_rules": [],
                "expanded_rules": "",
                "rule_count": 0
            }
    
    def _load_rules(self, rules_source: Union[List[str], str, Path]) -> List[str]:
        """
        Load rules from various sources.
        
        Args:
            rules_source: Rules as list, file path, or Path object
            
        Returns:
            List of parsed rules
        """
        if isinstance(rules_source, list):
            logger.info(f"Using {len(rules_source)} rules from list")
            return rules_source
            
        elif isinstance(rules_source, (str, Path)):
            return self._load_rules_from_file(rules_source)
            
        else:
            raise ValueError(f"Invalid rules source type: {type(rules_source)}")
    
    def _load_rules_from_file(self, file_path: Union[str, Path]) -> List[str]:
        """
        Load cleaning rules from a text file.
        
        Args:
            file_path: Path to the rules file
            
        Returns:
            List of rules loaded from file
        """
        try:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"Rules file not found: {file_path}")
            
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # Parse rules - each line is a rule, ignore empty lines and comments
            rules = []
            for line_num, line in enumerate(content.split('\n'), 1):
                line = line.strip()
                # Skip empty lines and comments (lines starting with #)
                if line and not line.startswith('#'):
                    rules.append(line)
            
            logger.info(f"Loaded {len(rules)} rules from file: {file_path}")
            return rules
            
        except Exception as e:
            logger.error(f"Error loading rules from file {file_path}: {str(e)}")
            raise
    
    def get_expanded_rules(self) -> str:
        """
        Get the expanded rules (must be initialized first).
        
        Returns:
            Expanded rules string
        """
        if not self.is_expanded:
            logger.warning("Rules not yet expanded. Call initialize_rules() first.")
            return ""
        return self.expanded_rules
    
    def get_original_rules(self) -> List[str]:
        """
        Get the original rules.
        
        Returns:
            List of original rules
        """
        return self.original_rules.copy()
    
    def get_rules_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive summary of rule state.
        
        Returns:
            Dictionary with rule information
        """
        return {
            "original_rules": self.original_rules.copy(),
            "rule_count": len(self.original_rules),
            "is_expanded": self.is_expanded,
            "has_expanded_rules": bool(self.expanded_rules),
            "target_columns": self.column_headers.copy(),
            "expanded_rules_preview": self.expanded_rules[:200] + "..." if len(self.expanded_rules) > 200 else self.expanded_rules
        }
    
    def reset(self) -> None:
        """Reset the rule manager to initial state."""
        self.original_rules = []
        self.expanded_rules = ""
        self.column_headers = []
        self.is_expanded = False
        logger.info("Rule manager reset")
