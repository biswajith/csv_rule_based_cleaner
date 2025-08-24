"""
Validation Manager - Centralized validation coordination.
Consolidates phone, geography, and cross-field validation in a single LLM-driven component.
"""

from typing import Dict, List, Any, Optional
from loguru import logger
from langchain_core.prompts import ChatPromptTemplate

from .llm_client import LLMClient


class ValidationManager:
    """
    Centralized validation manager that coordinates all validation types.
    Eliminates duplication across separate validator classes.
    """
    
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        
    async def validate_comprehensive(
        self,
        data_records: List[Dict[str, Any]],
        column_headers: List[str],
        user_rules: List[str]
    ) -> Dict[str, Any]:
        """
        Perform comprehensive validation in a single LLM call.
        Replaces separate phone, geography, and cross-field validation.
        
        Args:
            data_records: Records to validate
            column_headers: CSV column headers for context
            user_rules: User-provided rules for validation context
            
        Returns:
            Comprehensive validation results
        """
        try:
            logger.info(f"Starting comprehensive validation for {len(data_records)} records")
            
            # Unified validation prompt that covers all validation types
            validation_prompt = self._build_comprehensive_validation_prompt(
                data_records, column_headers, user_rules
            )
            
            chat_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a comprehensive data validation expert. Analyze data for phone number formats, geographical consistency, cross-field relationships, and business logic violations."),
                ("human", validation_prompt)
            ])
            
            # Use operation-specific client with proper token limits
            operation_client = self.llm_client._get_client_for_operation("validation")  # Use validation-specific limits
            chain = chat_prompt | operation_client | self.llm_client.output_parser
            response = await chain.ainvoke({})
            
            logger.info("Successfully completed comprehensive validation")
            return {
                "status": "success",
                "validation_results": response,
                "records_validated": len(data_records),
                "validation_types": ["phone", "geography", "cross_field", "business_logic"]
            }
            
        except Exception as e:
            logger.error(f"Error in comprehensive validation: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "records_validated": 0
            }
    
    def _build_comprehensive_validation_prompt(
        self,
        data_records: List[Dict[str, Any]],
        column_headers: List[str],
        user_rules: List[str]
    ) -> str:
        """
        Build unified validation prompt covering all validation types.
        """
        # Sample data for validation (limit to first 10 records for prompt efficiency)
        sample_data = data_records[:10]
        
        prompt = f"""
        Perform comprehensive validation on this data:
        
        Column Headers: {', '.join(column_headers)}
        User Rules Context: {'; '.join(user_rules)}
        
        Sample Data Records:
        {self._format_records_for_prompt(sample_data)}
        
        Validation Tasks:
        
        1. PHONE NUMBER VALIDATION:
           - Identify phone number fields
           - Check format consistency, use country code if available, during validation. If no country code is available, use the US format preferred: (XXX) XXX-XXXX
           - Flag invalid or suspicious phone numbers
           - Suggest format standardizations
        
        2. GEOGRAPHICAL VALIDATION:
           - Identify geographical fields (state, city, country, zip)
           - Validate state/city relationships
           - Check country consistency
           - Flag geographical inconsistencies
           - Suggest corrections for invalid geographical data
        
        3. CROSS-FIELD VALIDATION:
           - Identify related field relationships
           - Check data consistency across related fields
           - Validate business logic rules
           - Flag contradictions between related fields
           - Suggest resolutions for conflicts
        
        4. BUSINESS LOGIC VALIDATION:
           - Apply user rules for validation context
           - Check for obvious data quality issues
           - Identify test/dummy data
           - Flag records requiring manual review
        
        For each validation type, provide:
        - validation_summary: overall assessment
        - issues_found: specific problems identified
        - recommendations: suggested fixes and improvements
        - confidence_level: high/medium/low for each finding
        - priority: high/medium/low for addressing each issue
        
        Most important: Don't drop any records, even if they are invalid.

        Return results in structured JSON format with clear categorization.
        """
        
        return prompt
    
    def _format_records_for_prompt(self, records: List[Dict[str, Any]]) -> str:
        """Format records for inclusion in LLM prompt."""
        if not records:
            return "No records provided"
        
        formatted_lines = []
        for i, record in enumerate(records[:5], 1):  # Limit to 5 for prompt efficiency
            record_str = ", ".join([f"{k}: {v}" for k, v in record.items()])
            formatted_lines.append(f"Record {i}: {record_str}")
        
        return "\n".join(formatted_lines)
