"""
LLM Client for OpenAI integration via LangChain.
All data cleaning decisions are made through this interface.
"""

import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
import json
import tiktoken

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
        # Initialize tokenizer for token estimation
        try:
            self.tokenizer = tiktoken.encoding_for_model(settings.openai_model)
        except KeyError:
            # Fallback to cl100k_base for newer models
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
    
    def _log_llm_interaction(self, operation: str, input_data: Any, output_data: Any = None, error: str = None):
        """
        Log LLM input and output for debugging and monitoring.
        
        Args:
            operation: Name of the LLM operation (e.g., 'expand_rules', 'clean_data_chunk')
            input_data: Input data sent to LLM (messages, prompts, etc.)
            output_data: Response received from LLM
            error: Error message if operation failed
        """
        log_entry = {
            "operation": operation,
            "model": settings.openai_model,
            "timestamp": datetime.now().isoformat()
        }
        
        # Log input data (truncate if too long)
        if isinstance(input_data, list):  # Messages list
            input_summary = []
            for msg in input_data:
                if hasattr(msg, 'content'):
                    content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
                    input_summary.append(f"{type(msg).__name__}: {content}")
                else:
                    input_summary.append(str(msg)[:500])
            log_entry["input"] = input_summary
        else:
            input_str = str(input_data)
            log_entry["input"] = input_str[:500] + "..." if len(input_str) > 500 else input_str
        
        # Log output data (truncate if too long)
        if output_data:
            output_str = str(output_data)
            log_entry["output"] = output_str[:500] + "..." if len(output_str) > 500 else output_str
        
        # Log error if present
        if error:
            log_entry["error"] = error
            logger.error(f"LLM_INTERACTION_ERROR: {json.dumps(log_entry, indent=2)}")
        else:
            logger.info(f"LLM_INTERACTION_SUCCESS: {json.dumps(log_entry, indent=2)}")
        
        # Also log detailed interaction for debugging (if enabled)
        if settings.enable_detailed_llm_logging:
            logger.debug(f"LLM_DETAILED_INPUT [{operation}]: {input_data}")
            if output_data:
                logger.debug(f"LLM_DETAILED_OUTPUT [{operation}]: {output_data}")
            if error:
                logger.debug(f"LLM_ERROR_DETAILS [{operation}]: {error}")
    
    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for a given text.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Estimated token count
        """
        try:
            return len(self.tokenizer.encode(text))
        except Exception as e:
            logger.warning(f"Token estimation failed: {e}. Using character-based approximation.")
            # Fallback: rough approximation of 4 characters per token
            return len(text) // 4
    
    def _check_token_limits(self, prompt_text: str, operation: str, estimated_response_tokens: int = 1000) -> Dict[str, Any]:
        """
        Check if prompt + estimated response will exceed operation-specific token limits.
        
        Args:
            prompt_text: The complete prompt text
            operation: The LLM operation type (e.g., 'expand_rules', 'clean_data_chunk')
            estimated_response_tokens: Expected response token count
            
        Returns:
            Dictionary with token analysis and recommendation
        """
        prompt_tokens = self._estimate_tokens(prompt_text)
        total_estimated = prompt_tokens + estimated_response_tokens
        
        # Get operation-specific token limit
        operation_limits = {
            "expand_rules": settings.rule_expansion_max_tokens,
            "clean_data_chunk": settings.data_cleaning_max_tokens,
            "validation": settings.validation_max_tokens
        }
        
        max_tokens = operation_limits.get(operation, settings.openai_max_tokens)
        
        result = {
            "operation": operation,
            "prompt_tokens": prompt_tokens,
            "estimated_response_tokens": estimated_response_tokens,
            "total_estimated_tokens": total_estimated,
            "max_tokens": max_tokens,
            "operation_specific_limit": True if operation in operation_limits else False,
            "within_limits": total_estimated <= max_tokens,
            "utilization_percent": (total_estimated / max_tokens) * 100
        }
        
        if not result["within_limits"]:
            logger.warning(f"Token limit exceeded for {operation}: {total_estimated} > {max_tokens}")
            result["recommendation"] = f"Reduce prompt size for {operation} or increase {operation}_max_tokens"
        
        return result
    
    def _get_client_for_operation(self, operation: str) -> ChatOpenAI:
        """
        Get a ChatOpenAI client configured for the specific operation's token limits and timeout.
        
        Args:
            operation: The LLM operation type
            
        Returns:
            Configured ChatOpenAI client
        """
        operation_limits = {
            "expand_rules": settings.rule_expansion_max_tokens,
            "clean_data_chunk": settings.data_cleaning_max_tokens,
            "validation": settings.validation_max_tokens
        }
        
        # Operation-specific timeouts (longer for larger operations)
        operation_timeouts = {
            "expand_rules": 180.0,  # Quick rule expansion
            "clean_data_chunk": 1800.0,  # Longer for data processing
            "validation": 180.0  # Medium for validation
        }
        
        max_tokens = operation_limits.get(operation, settings.openai_max_tokens)
        timeout = operation_timeouts.get(operation, settings.request_timeout)
        
        return ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_tokens=max_tokens,
            temperature=settings.openai_temperature,
            timeout=timeout
        )
    
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
            # Prepare prompt content
            formatted_rules = "\n".join(f"- {rule}" for rule in user_rules)
            formatted_columns = ", ".join(column_headers)
            
            # Create full prompt text for token estimation
            human_prompt = LLMPromptSettings.RULE_EXPANSION_PROMPT.format(
                user_rules=formatted_rules,
                column_headers=formatted_columns
            )
            system_prompt = "You are a data cleaning expert. Expand user rules into comprehensive cleaning instructions. Use column headers to help you understand the data. But don't generate rules for columns that don't have any rules associated with them. Explicity add instructions to never delete or drop any record even if the record has invalid data in critical fields. Pay keen attention to any column that may have geographic data and ensure that the geographic data is valid. Not just the individual geographic field, but group together. For example if the record has city, state and zip code make sure that the combination is valid, not just the individual fields. If a record has Portland as city and WA as the state, the record has to be corrected to have OR as the state. Zip code has to match the city, if record has 26505 for City Portland, then it should get converted to Morgantown and state to WV."

            full_prompt = f"{system_prompt}\n{human_prompt}"
            
            # Check token limits before making request
            token_check = self._check_token_limits(full_prompt, operation="expand_rules", estimated_response_tokens=500)
            logger.info(f"Token analysis for {token_check['operation']}: {token_check['prompt_tokens']} prompt + {token_check['estimated_response_tokens']} response = {token_check['total_estimated_tokens']} total ({token_check['utilization_percent']:.1f}% of {token_check['max_tokens']})")
            
            if not token_check["within_limits"]:
                # Try to reduce the rules if too large
                if len(formatted_rules) > 500:
                    # Truncate rules if too long
                    truncated_rules = formatted_rules[:400] + "... [truncated for token limits]"
                    human_prompt = LLMPromptSettings.RULE_EXPANSION_PROMPT.format(
                        user_rules=truncated_rules,
                        column_headers=formatted_columns
                    )
                    logger.warning("Truncated rules due to token limits")
                else:
                    # If still too large, return error
                    logger.error(f"Prompt too large even after optimization: {token_check['total_estimated_tokens']} tokens")
                    return {
                        "status": "error",
                        "error": f"Prompt exceeds token limit: {token_check['total_estimated_tokens']} > {token_check['max_tokens']}",
                        "token_analysis": token_check,
                        "original_rules": user_rules,
                        "expanded_rules": ""
                    }
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("human", human_prompt)
            ])
            
            # Log LLM input
            input_data = {
                "user_rules": user_rules,
                "column_headers": column_headers,
                "token_analysis": token_check
            }
            
            # Use operation-specific client
            operation_client = self._get_client_for_operation("expand_rules")
            chain = prompt | operation_client | self.output_parser
            response = await chain.ainvoke({})
            
            # Log successful LLM interaction
            self._log_llm_interaction("expand_rules", input_data, response)
            
            logger.info("Successfully expanded user rules via LLM")
            return {
                "expanded_rules": response,
                "original_rules": user_rules,
                "column_headers": column_headers,
                "token_analysis": token_check,
                "status": "success"
            }
            
        except Exception as e:
            # Log failed LLM interaction
            input_data = {
                "user_rules": user_rules,
                "column_headers": column_headers
            }
            
            # Check if it's a timeout error and provide specific guidance
            error_message = str(e).lower()
            if "timeout" in error_message or "timed out" in error_message:
                logger.error(f"Timeout error in rule expansion: {str(e)}")
                logger.info("Timeout troubleshooting tips:")
                logger.info("1. Verify your OpenAI model is available in your account tier")
                logger.info("2. Ensure your API key is valid and has quota")
                logger.info("3. Consider reducing rule complexity")
                logger.info(f"4. Current timeout: 60s for rule expansion")
            else:
                logger.error(f"Error expanding rules: {str(e)}")
            
            self._log_llm_interaction("expand_rules", input_data, error=str(e))
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
            # Debug: Log what we're about to send to LLM
            data_csv = chunk_data.get('data_csv', '')
            logger.debug(f"Data CSV length: {len(data_csv)}")
            logger.debug(f"Data CSV preview: {data_csv[:200]}...")
            logger.debug(f"Expanded rules length: {len(expanded_rules)}")
            logger.debug(f"Expanded rules preview: {expanded_rules[:200]}...")
            
            # Create the complete prompt text without LangChain template parsing
            system_message = "You are a data cleaning specialist. Clean data according to provided rules and return results in JSON format."
            human_message = LLMPromptSettings.DATA_CLEANING_PROMPT.format(
                data_chunk=data_csv,
                expanded_rules=expanded_rules
            )
            
            # Use a simple approach that bypasses LangChain's variable parsing
            from langchain_core.messages import SystemMessage, HumanMessage
            messages = [
                SystemMessage(content=system_message),
                HumanMessage(content=human_message)
            ]
            
            # Check token limits for data cleaning operation
            prompt_text = f"{system_message}\n{human_message}"
            token_check = self._check_token_limits(prompt_text, operation="clean_data_chunk", estimated_response_tokens=1500)
            logger.info(f"Token analysis for {token_check['operation']}: {token_check['prompt_tokens']} prompt + {token_check['estimated_response_tokens']} response = {token_check['total_estimated_tokens']} total ({token_check['utilization_percent']:.1f}% of {token_check['max_tokens']})")
            
            # Log LLM input
            input_data = {
                "chunk_data": {
                    "chunk_size": chunk_data.get('chunk_size', 0),
                    "data_preview": str(chunk_data.get('data_csv', ''))[:200] + "..." if len(str(chunk_data.get('data_csv', ''))) > 200 else str(chunk_data.get('data_csv', ''))
                },
                "expanded_rules": expanded_rules[:200] + "..." if len(expanded_rules) > 200 else expanded_rules,
                "token_analysis": token_check
            }
            
            if not token_check["within_limits"]:
                logger.warning(f"Data cleaning chunk exceeds token limits: {token_check['total_estimated_tokens']} > {token_check['max_tokens']}")
                # For data cleaning, we might need to split the chunk further
                return {
                    "status": "error",
                    "error": f"Data chunk too large for cleaning: {token_check['total_estimated_tokens']} tokens > {token_check['max_tokens']} limit",
                    "token_analysis": token_check,
                    "original_data": chunk_data
                }
            
            # Use operation-specific client directly with messages
            operation_client = self._get_client_for_operation("clean_data_chunk")
            llm_response = await operation_client.ainvoke(messages)
            
            # Extract content from the response
            response = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
            
            # Log the raw response for debugging
            logger.debug(f"Raw LLM response: {response[:500]}..." if len(response) > 500 else f"Raw LLM response: {response}")
            
            # Try to parse JSON response
            try:
                # Check if response looks like JSON
                if not response.strip().startswith('{') and not response.strip().startswith('['):
                    logger.warning(f"Response doesn't look like JSON: {response[:100]}...")
                    raise json.JSONDecodeError("Response doesn't start with JSON marker", response, 0)
                
                parsed_response = json.loads(response)
                
                # Normalize response format - handle different LLM response formats
                if "cleaned_data" in parsed_response and isinstance(parsed_response["cleaned_data"], list):
                    # New format with embedded change logs - extract changes
                    cleaned_records = parsed_response["cleaned_data"]
                    all_changes = []
                    
                    # Extract change logs from each record and flatten
                    for record_idx, record in enumerate(cleaned_records):
                        if "change_log" in record:
                            change_log = record["change_log"]  # Keep in record data for audit trail
                            # Add record index to each change and normalize confidence levels
                            for change in change_log:
                                # Ensure change is a dictionary before calling copy()
                                if isinstance(change, dict):
                                    change_copy = change.copy()  # Don't modify original
                                    change_copy["record_index"] = record_idx
                                    # Normalize confidence level to lowercase
                                    if "confidence" in change_copy:
                                        change_copy["confidence"] = change_copy["confidence"].lower()
                                    all_changes.append(change_copy)
                                else:
                                    # Handle case where change is not a dict (e.g., string)
                                    logger.warning(f"Invalid change format in record {record_idx}: {change}")
                                    change_dict = {
                                        "field": "unknown",
                                        "original": str(change),
                                        "corrected": str(change),
                                        "reason": "Invalid change log format",
                                        "confidence": "low",
                                        "record_index": record_idx
                                    }
                                    all_changes.append(change_dict)
                    
                    # Create normalized format expected by the system
                    normalized_response = {
                        "cleaned_data": cleaned_records,
                        "changes": all_changes,
                        "status": "success"
                    }
                    parsed_response = normalized_response
                    
                elif "cleaned_csv" in parsed_response:
                    # Legacy CSV format - convert to expected format
                    normalized_response = {
                        "cleaned_data": parsed_response.get("cleaned_csv", ""),
                        "changes": parsed_response.get("change_log", []),
                        "status": "success"
                    }
                    parsed_response = normalized_response
                elif "cleaned_data" not in parsed_response:
                    # Ensure required fields exist
                    parsed_response["cleaned_data"] = response
                    parsed_response["changes"] = parsed_response.get("changes", [])
                    
                # Ensure status is set
                if "status" not in parsed_response:
                    parsed_response["status"] = "success"
                    
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing failed: {e}")
                logger.error(f"Raw response that failed: {repr(response)}")
                
                # If not valid JSON, return error instead of trying to wrap
                return {
                    "status": "error",
                    "error": f"LLM returned invalid JSON: {str(e)}. Response: {response[:200]}...",
                    "raw_response": response[:500] if len(response) > 500 else response,
                    "changes": []
                }
            
            # Validate that we have the same number of records
            original_record_count = chunk_data.get('chunk_size', 0)
            cleaned_data = parsed_response.get("cleaned_data", "")
            
            # Count records in cleaned data - handle different formats
            cleaned_record_count = 0
            if isinstance(cleaned_data, list):
                # New format: list of record dictionaries
                cleaned_record_count = len(cleaned_data)
            elif isinstance(cleaned_data, str) and cleaned_data.strip():
                # Legacy string format - CSV or JSON string
                if cleaned_data.strip().startswith('{') or cleaned_data.strip().startswith('['):
                    try:
                        json_data = json.loads(cleaned_data)
                        if isinstance(json_data, dict) and "records" in json_data:
                            cleaned_record_count = len(json_data["records"])
                        elif isinstance(json_data, list):
                            cleaned_record_count = len(json_data)
                        else:
                            # Fallback to line counting for JSON
                            lines = cleaned_data.strip().split('\n')
                            cleaned_record_count = len([line for line in lines if line.strip() and not line.strip().startswith('{') and not line.strip().startswith('}') and not line.strip().startswith('[') and not line.strip().startswith(']')])
                    except json.JSONDecodeError:
                        # Not valid JSON, treat as CSV
                        lines = cleaned_data.strip().split('\n')
                        cleaned_record_count = len(lines) - 1 if len(lines) > 1 else 0  # Subtract header
                else:
                    # Treat as CSV format
                    lines = cleaned_data.strip().split('\n')
                    cleaned_record_count = len(lines) - 1 if len(lines) > 1 else 0  # Subtract header
                
            if cleaned_record_count != original_record_count:
                logger.warning(f"RECORD COUNT MISMATCH: Expected {original_record_count} records, got {cleaned_record_count} records")
                logger.warning("This indicates some records may have been dropped during cleaning")
                logger.warning("The LLM should preserve ALL records even if they cannot be fixed")
                
                # Add a note to the response about the mismatch
                if "notes" not in parsed_response:
                    parsed_response["notes"] = ""
                parsed_response["notes"] += f" WARNING: Record count mismatch - expected {original_record_count}, got {cleaned_record_count}. "
            
            logger.info(f"Successfully cleaned chunk with {chunk_data.get('chunk_size', 0)} records")
            return parsed_response
            
        except Exception as e:
            # Log failed LLM interaction
            input_data = {
                "chunk_data": {
                    "chunk_size": chunk_data.get('chunk_size', 0)
                },
                "expanded_rules": expanded_rules[:200] + "..." if len(expanded_rules) > 200 else expanded_rules
            }
            
            # Check if it's a timeout error and provide specific guidance
            error_message = str(e).lower()
            if "timeout" in error_message or "timed out" in error_message:
                logger.error(f"Timeout error in data cleaning: {str(e)}")
                logger.info("Data cleaning timeout troubleshooting:")
                logger.info("1. Verify OpenAI model access in your account tier")
                logger.info("2. Verify expanded_rules are not empty")
                logger.info(f"3. Current chunk size: {chunk_data.get('chunk_size', 0)} records")
                logger.info("4. Current timeout: 180s for data cleaning")
                logger.info("5. Try reducing CHUNK_SIZE in settings")
                logger.info(f"6. Rules preview: {expanded_rules[:100]}..." if expanded_rules else "6. EMPTY RULES - This will cause issues!")
            else:
                logger.error(f"Error cleaning data chunk: {str(e)}")
            
            self._log_llm_interaction("clean_data_chunk", input_data, error=str(e))
            return await self._handle_retry("clean_data_chunk", self.clean_data_chunk, chunk_data, expanded_rules)
    
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
