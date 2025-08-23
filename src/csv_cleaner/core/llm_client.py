"""
LLM Client for OpenAI integration via LangChain.
All data cleaning decisions are made through this interface.
"""

import asyncio
from typing import Dict, List, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
import json

from ..config.settings import settings, LLMPromptSettings


class LLMClient:
    """
    OpenAI LLM client using LangChain for all data cleaning decisions.
    """
    
    def __init__(self):
        self.client = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_tokens=settings.openai_max_tokens,
            temperature=settings.openai_temperature,
            timeout=settings.request_timeout
        )
        self.output_parser = StrOutputParser()
        self.retry_count = 0
    
    async def expand_rules(self, user_rules: List[str], column_headers: List[str]) -> Dict[str, Any]:
        """
        Expand user-provided rules into comprehensive cleaning instructions.
        
        Args:
            user_rules: List of user-provided cleaning rules
            column_headers: CSV column headers
            
        Returns:
            Expanded rules and cleaning instructions
        """
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a data cleaning expert. Expand user rules into comprehensive cleaning instructions."),
                ("human", LLMPromptSettings.RULE_EXPANSION_PROMPT.format(
                    user_rules="\n".join(f"- {rule}" for rule in user_rules),
                    column_headers=", ".join(column_headers)
                ))
            ])
            
            chain = prompt | self.client | self.output_parser
            response = await chain.ainvoke({})
            
            logger.info("Successfully expanded user rules via LLM")
            return {
                "expanded_rules": response,
                "original_rules": user_rules,
                "column_headers": column_headers,
                "status": "success"
            }
            
        except Exception as e:
            logger.error(f"Error expanding rules: {str(e)}")
            return await self._handle_retry("expand_rules", self.expand_rules, user_rules, column_headers)
    
    async def clean_data_chunk(self, chunk_data: Dict[str, Any], expanded_rules: str) -> Dict[str, Any]:
        """
        Clean a data chunk using LLM decisions.
        
        Args:
            chunk_data: Data chunk with headers and records
            expanded_rules: Expanded cleaning rules from LLM
            
        Returns:
            Cleaned data with change log
        """
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a data cleaning specialist. Clean data according to provided rules and return results in JSON format."),
                ("human", LLMPromptSettings.DATA_CLEANING_PROMPT.format(
                    data_chunk=chunk_data.get('data_csv', ''),
                    expanded_rules=expanded_rules
                ))
            ])
            
            chain = prompt | self.client | self.output_parser
            response = await chain.ainvoke({})
            
            # Try to parse JSON response
            try:
                parsed_response = json.loads(response)
            except json.JSONDecodeError:
                # If not JSON, wrap in structure
                parsed_response = {
                    "cleaned_data": response,
                    "changes": [],
                    "status": "success"
                }
            
            logger.info(f"Successfully cleaned chunk with {chunk_data.get('chunk_size', 0)} records")
            return parsed_response
            
        except Exception as e:
            logger.error(f"Error cleaning data chunk: {str(e)}")
            return await self._handle_retry("clean_data_chunk", self.clean_data_chunk, chunk_data, expanded_rules)
    
    async def validate_geographical_data(self, geo_fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate geographical data using LLM.
        
        Args:
            geo_fields: Geographical field data
            
        Returns:
            Validation results and corrections
        """
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a geographical data expert. Validate and correct geographical information."),
                ("human", LLMPromptSettings.GEOGRAPHICAL_VALIDATION_PROMPT.format(
                    geo_fields=json.dumps(geo_fields, indent=2)
                ))
            ])
            
            chain = prompt | self.client | self.output_parser
            response = await chain.ainvoke({})
            
            logger.info("Successfully validated geographical data via LLM")
            return {
                "validation_results": response,
                "original_data": geo_fields,
                "status": "success"
            }
            
        except Exception as e:
            logger.error(f"Error validating geographical data: {str(e)}")
            return await self._handle_retry("validate_geographical_data", self.validate_geographical_data, geo_fields)
    
    async def normalize_phone_numbers(self, phone_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize phone numbers using LLM.
        
        Args:
            phone_data: Phone number field data
            
        Returns:
            Normalized phone numbers with explanations
        """
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a phone number validation expert. Normalize phone numbers to standard format."),
                ("human", LLMPromptSettings.PHONE_NORMALIZATION_PROMPT.format(
                    phone_data=json.dumps(phone_data, indent=2)
                ))
            ])
            
            chain = prompt | self.client | self.output_parser
            response = await chain.ainvoke({})
            
            logger.info("Successfully normalized phone numbers via LLM")
            return {
                "normalized_phones": response,
                "original_data": phone_data,
                "status": "success"
            }
            
        except Exception as e:
            logger.error(f"Error normalizing phone numbers: {str(e)}")
            return await self._handle_retry("normalize_phone_numbers", self.normalize_phone_numbers, phone_data)
    
    async def analyze_data_quality(self, sample_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze data quality and suggest cleaning strategies.
        
        Args:
            sample_data: Sample data for analysis
            
        Returns:
            Quality analysis and recommendations
        """
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a data quality expert. Analyze the provided data sample and recommend cleaning strategies."),
                ("human", f"""
                Please analyze this data sample and provide recommendations:
                
                Column Headers: {sample_data.get('column_headers', [])}
                Sample Records: {json.dumps(sample_data.get('sample_data', []), indent=2)}
                Data Types: {sample_data.get('data_types', {})}
                Null Counts: {sample_data.get('null_counts', {})}
                Total Records: {sample_data.get('total_records', 0)}
                
                Please provide:
                1. Data quality assessment
                2. Potential issues identified
                3. Recommended cleaning strategies
                4. Priority areas for cleaning
                """)
            ])
            
            chain = prompt | self.client | self.output_parser
            response = await chain.ainvoke({})
            
            logger.info("Successfully analyzed data quality via LLM")
            return {
                "quality_analysis": response,
                "sample_data": sample_data,
                "status": "success"
            }
            
        except Exception as e:
            logger.error(f"Error analyzing data quality: {str(e)}")
            return await self._handle_retry("analyze_data_quality", self.analyze_data_quality, sample_data)
    
    async def _handle_retry(self, operation_name: str, func, *args, **kwargs) -> Dict[str, Any]:
        """
        Handle retry logic for failed LLM calls.
        
        Args:
            operation_name: Name of the operation for logging
            func: Function to retry
            *args, **kwargs: Function arguments
            
        Returns:
            Result or error information
        """
        if self.retry_count < settings.max_retries:
            self.retry_count += 1
            logger.warning(f"Retrying {operation_name} (attempt {self.retry_count}/{settings.max_retries})")
            
            await asyncio.sleep(settings.retry_delay)
            return await func(*args, **kwargs)
        else:
            self.retry_count = 0
            logger.error(f"Max retries exceeded for {operation_name}")
            return {
                "status": "error",
                "error": f"Max retries exceeded for {operation_name}",
                "retry_count": settings.max_retries
            }
    
    def reset_retry_count(self):
        """Reset retry counter for new operations."""
        self.retry_count = 0
