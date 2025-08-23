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
            
            chain = chat_prompt | self.llm_client.client | self.llm_client.output_parser
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
           - Check format consistency (US format preferred: (XXX) XXX-XXXX)
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
    
    async def validate_phone_numbers(
        self,
        data_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Focused phone number validation.
        Lightweight method for phone-specific validation needs.
        """
        try:
            phone_fields = self._identify_phone_fields(data_context.get("headers", []))
            
            if not phone_fields:
                return {
                    "status": "success",
                    "message": "No phone fields identified",
                    "phone_fields": [],
                    "validation_results": {}
                }
            
            # Use comprehensive validation focused on phone data
            phone_data = self._extract_phone_data(data_context, phone_fields)
            
            prompt = f"""
            Validate and normalize phone number data:
            
            Phone Fields Identified: {', '.join(phone_fields)}
            Sample Phone Data: {phone_data}
            
            Tasks:
            1. Validate phone number formats
            2. Standardize to (XXX) XXX-XXXX format
            3. Identify invalid/suspicious numbers
            4. Suggest corrections
            
            Return JSON with validation results and standardization recommendations.
            """
            
            chat_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a phone number validation specialist."),
                ("human", prompt)
            ])
            
            chain = chat_prompt | self.llm_client.client | self.llm_client.output_parser
            response = await chain.ainvoke({})
            
            return {
                "status": "success",
                "phone_fields": phone_fields,
                "validation_results": response
            }
            
        except Exception as e:
            logger.error(f"Error in phone validation: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def validate_geographical_data(
        self,
        data_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Focused geographical validation.
        Lightweight method for geography-specific validation needs.
        """
        try:
            geo_fields = self._identify_geographical_fields(data_context.get("headers", []))
            
            if not geo_fields:
                return {
                    "status": "success",
                    "message": "No geographical fields identified",
                    "geo_fields": [],
                    "validation_results": {}
                }
            
            geo_data = self._extract_geographical_data(data_context, geo_fields)
            
            prompt = f"""
            Validate geographical data consistency:
            
            Geographical Fields: {', '.join(geo_fields)}
            Sample Geographical Data: {geo_data}
            
            Tasks:
            1. Validate state/city/country relationships
            2. Check for geographical inconsistencies
            3. Standardize geographical formats
            4. Flag invalid geographical combinations
            
            Return JSON with validation results and correction suggestions.
            """
            
            chat_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a geographical data validation expert."),
                ("human", prompt)
            ])
            
            chain = chat_prompt | self.llm_client.client | self.llm_client.output_parser
            response = await chain.ainvoke({})
            
            return {
                "status": "success",
                "geo_fields": geo_fields,
                "validation_results": response
            }
            
        except Exception as e:
            logger.error(f"Error in geographical validation: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _identify_phone_fields(self, headers: List[str]) -> List[str]:
        """Identify phone number fields from headers."""
        phone_keywords = ['phone', 'tel', 'mobile', 'cell', 'number']
        return [h for h in headers if any(keyword in h.lower() for keyword in phone_keywords)]
    
    def _identify_geographical_fields(self, headers: List[str]) -> List[str]:
        """Identify geographical fields from headers."""
        geo_keywords = ['state', 'city', 'country', 'zip', 'postal', 'address', 'location']
        return [h for h in headers if any(keyword in h.lower() for keyword in geo_keywords)]
    
    def _extract_phone_data(self, data_context: Dict[str, Any], phone_fields: List[str]) -> str:
        """Extract phone data for validation."""
        sample_data = data_context.get("data", [])[:5]  # Limit sample
        phone_samples = []
        
        for i, record in enumerate(sample_data, 1):
            phone_values = {field: record.get(field, "") for field in phone_fields}
            phone_samples.append(f"Record {i}: {phone_values}")
        
        return "\n".join(phone_samples)
    
    def _extract_geographical_data(self, data_context: Dict[str, Any], geo_fields: List[str]) -> str:
        """Extract geographical data for validation."""
        sample_data = data_context.get("data", [])[:5]  # Limit sample
        geo_samples = []
        
        for i, record in enumerate(sample_data, 1):
            geo_values = {field: record.get(field, "") for field in geo_fields}
            geo_samples.append(f"Record {i}: {geo_values}")
        
        return "\n".join(geo_samples)
    
    # Comprehensive methods to match old validator interfaces
    async def process_phone_data_comprehensive(self, data_chunk: Dict[str, Any]) -> Dict[str, Any]:
        """
        Comprehensive phone number processing: detection, normalization, and validation.
        Maintains compatibility with old PhoneValidator interface.
        
        Args:
            data_chunk: Data chunk containing potential phone fields
            
        Returns:
            Complete phone processing results
        """
        try:
            logger.info("Starting comprehensive phone data processing")
            
            headers = data_chunk.get("headers", [])
            sample_data = data_chunk.get("data", [])[:10]  # First 10 records for analysis
            
            # Use focused phone validation with comprehensive analysis
            result = await self.validate_phone_numbers({
                "headers": headers,
                "data": sample_data
            })
            
            # Enhance with comprehensive processing details
            if result.get("status") == "success":
                phone_fields = result.get("phone_fields", [])
                
                # Build comprehensive prompt for phone processing
                prompt = f"""
                Perform comprehensive phone number processing on this data:
                
                Headers: {', '.join(headers)}
                Phone Fields Detected: {', '.join(phone_fields)}
                Sample Data: {self._format_records_for_prompt(sample_data)}
                
                Comprehensive Tasks:
                1. DETECTION: Identify all phone number fields and patterns
                2. NORMALIZATION: Convert to standard (XXX) XXX-XXXX format
                3. VALIDATION: Verify phone number authenticity and validity
                4. QUALITY CHECK: Flag suspicious, invalid, or test numbers
                5. RECOMMENDATIONS: Suggest improvements and corrections
                
                For each phone field provide:
                - field_analysis: detailed assessment of the field
                - normalization_rules: specific formatting rules applied
                - validation_results: authenticity and validity checks
                - quality_issues: problems found and severity
                - recommendations: suggested fixes and improvements
                
                Return comprehensive JSON results with detailed analysis.
                """
                
                chat_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are a comprehensive phone number processing expert. Provide detailed analysis for detection, normalization, validation, and quality assessment."),
                    ("human", prompt)
                ])
                
                chain = chat_prompt | self.llm_client.client | self.llm_client.output_parser
                comprehensive_response = await chain.ainvoke({})
                
                return {
                    "status": "success",
                    "detection": {
                        "phone_fields": phone_fields,
                        "detection_confidence": "high" if phone_fields else "low"
                    },
                    "normalization": {
                        "target_format": "(XXX) XXX-XXXX",
                        "normalization_results": comprehensive_response
                    },
                    "validation": {
                        "validation_results": comprehensive_response
                    },
                    "summary": {
                        "total_records": len(sample_data),
                        "phone_fields_detected": phone_fields,
                        "processing_status": "completed"
                    }
                }
            else:
                return result
                
        except Exception as e:
            logger.error(f"Error in comprehensive phone processing: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def process_geographical_data_comprehensive(self, data_chunk: Dict[str, Any]) -> Dict[str, Any]:
        """
        Comprehensive geographical data processing: detection, validation, and correction.
        Maintains compatibility with old GeographyValidator interface.
        
        Args:
            data_chunk: Data chunk containing potential geographical fields
            
        Returns:
            Complete geographical processing results
        """
        try:
            logger.info("Starting comprehensive geographical data processing")
            
            headers = data_chunk.get("headers", [])
            sample_data = data_chunk.get("data", [])[:10]
            
            # Use focused geographical validation with comprehensive analysis
            result = await self.validate_geographical_data({
                "headers": headers,
                "data": sample_data
            })
            
            # Enhance with comprehensive processing details
            if result.get("status") == "success":
                geo_fields = result.get("geo_fields", [])
                
                prompt = f"""
                Perform comprehensive geographical data processing:
                
                Headers: {', '.join(headers)}
                Geographical Fields: {', '.join(geo_fields)}
                Sample Data: {self._format_records_for_prompt(sample_data)}
                
                Comprehensive Tasks:
                1. FIELD DETECTION: Identify all geographical fields (state, city, country, zip, address)
                2. RELATIONSHIP VALIDATION: Verify state/city/country relationships and consistency
                3. FORMAT STANDARDIZATION: Standardize geographical data formats
                4. CONSISTENCY CHECKS: Cross-validate geographical relationships
                5. CORRECTION SUGGESTIONS: Propose fixes for invalid geographical data
                
                For each geographical aspect provide:
                - field_analysis: detailed assessment of geographical fields
                - relationship_validation: state/city/country consistency checks
                - format_issues: standardization problems and solutions
                - consistency_problems: contradictions found between fields
                - correction_recommendations: specific fixes suggested
                
                Return comprehensive JSON results with detailed geographical analysis.
                """
                
                chat_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are a comprehensive geographical data expert. Analyze geographical relationships, validate consistency, and provide detailed recommendations."),
                    ("human", prompt)
                ])
                
                chain = chat_prompt | self.llm_client.client | self.llm_client.output_parser
                comprehensive_response = await chain.ainvoke({})
                
                return {
                    "status": "success",
                    "detection": {
                        "geographical_fields": geo_fields,
                        "field_types": ["state", "city", "country", "zip", "address"]
                    },
                    "validation": {
                        "relationship_validation": comprehensive_response,
                        "consistency_checks": comprehensive_response
                    },
                    "standardization": {
                        "format_recommendations": comprehensive_response
                    },
                    "summary": {
                        "total_records": len(sample_data),
                        "geo_fields_detected": geo_fields,
                        "processing_status": "completed"
                    }
                }
            else:
                return result
                
        except Exception as e:
            logger.error(f"Error in comprehensive geographical processing: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def process_cross_field_validation_comprehensive(self, data_chunk: Dict[str, Any], user_rules: List[str]) -> Dict[str, Any]:
        """
        Comprehensive cross-field validation: relationship identification and consistency checking.
        Maintains compatibility with old CrossFieldValidator interface.
        
        Args:
            data_chunk: Data chunk for cross-field analysis
            user_rules: User-provided rules for business logic validation
            
        Returns:
            Complete cross-field validation results
        """
        try:
            logger.info("Starting comprehensive cross-field validation")
            
            headers = data_chunk.get("headers", [])
            sample_data = data_chunk.get("data", [])[:10]
            
            prompt = f"""
            Perform comprehensive cross-field validation and relationship analysis:
            
            Headers: {', '.join(headers)}
            User Rules: {'; '.join(user_rules)}
            Sample Data: {self._format_records_for_prompt(sample_data)}
            
            Comprehensive Tasks:
            1. RELATIONSHIP IDENTIFICATION: Identify related fields and their relationships
            2. CONSISTENCY VALIDATION: Check data consistency across related fields
            3. BUSINESS LOGIC VALIDATION: Apply user rules for cross-field validation
            4. CONFLICT DETECTION: Find contradictions between related fields
            5. RESOLUTION RECOMMENDATIONS: Suggest ways to resolve conflicts
            
            Analysis areas:
            - Name field relationships (first_name, last_name, full_name)
            - Address component relationships (street, city, state, zip)
            - Contact information consistency (phone, email, address)
            - Business rules compliance (as per user rules)
            - Data quality across related fields
            
            For each relationship type provide:
            - relationship_type: type of field relationship identified
            - consistency_status: whether fields are consistent
            - conflicts_found: specific contradictions detected
            - business_rule_compliance: adherence to user rules
            - resolution_strategy: recommended approach to fix issues
            - confidence_level: confidence in the analysis
            
            Return comprehensive JSON results with detailed cross-field analysis.
            """
            
            chat_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a comprehensive cross-field validation expert. Analyze field relationships, detect inconsistencies, and provide detailed resolution strategies."),
                ("human", prompt)
            ])
            
            chain = chat_prompt | self.llm_client.client | self.llm_client.output_parser
            comprehensive_response = await chain.ainvoke({})
            
            return {
                "status": "success",
                "relationship_analysis": {
                    "field_relationships": comprehensive_response,
                    "relationship_types": ["name_fields", "address_fields", "contact_fields", "business_fields"]
                },
                "consistency_validation": {
                    "validation_results": comprehensive_response,
                    "conflicts_detected": comprehensive_response
                },
                "business_logic": {
                    "rule_compliance": comprehensive_response,
                    "user_rules_applied": user_rules
                },
                "summary": {
                    "total_records": len(sample_data),
                    "relationships_analyzed": len(headers),
                    "processing_status": "completed"
                }
            }
            
        except Exception as e:
            logger.error(f"Error in comprehensive cross-field validation: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
