# Important Patterns and Fixes

This document captures critical patterns and common mistakes to avoid in this codebase.

## ⚠️ CRITICAL: ChatPromptTemplate with JSON Data

### **The Problem**
When using `ChatPromptTemplate.from_messages()` with data that contains JSON (or any content with curly braces `{}`), LangChain interprets the curly braces as template variables, causing errors like:

```
Input to ChatPromptTemplate is missing variables {"'first_name'", '\n    "first_name"'}.  
Expected: ['\n    "first_name"', "'first_name'"] Received: []
```

### **❌ WRONG - This Will ALWAYS Fail:**
```python
# DON'T DO THIS - JSON data in f-strings within ChatPromptTemplate
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are an expert..."),
    ("human", f"""
        Data: {json.dumps(some_data, indent=2)}  # ❌ CURLY BRACES = TEMPLATE VARIABLES!
        Please analyze this data...
    """)
])
```

### **✅ CORRECT - Use Direct Message Objects:**
```python
# DO THIS - Create messages directly to avoid template conflicts
system_message = SystemMessage(content="You are an expert...")

human_content = f"""
    Data: {json.dumps(some_data, indent=2)}
    Please analyze this data...
"""
human_message = HumanMessage(content=human_content)

# Invoke directly with messages
messages = [system_message, human_message]
response = await client.ainvoke(messages)
response = output_parser.parse(response.content)
```

### **🔧 Fixed Methods in This Codebase:**
1. `validate_geographical_data()` - Fixed ✅  
2. `normalize_phone_numbers()` - Fixed ✅
3. `analyze_data_quality()` - **REMOVED** ✅ (Redundant - cleaning process provides better quality insights)

### **✅ Safe Methods (Use .format() with templates):**
Methods that use `LLMPromptSettings.PROMPT_NAME.format()` are safe because:
- The template variables are properly defined in settings
- The `.format()` method handles curly braces correctly
- Examples: `expand_rules()`, `clean_data_chunk()`

### **🔍 How to Identify This Issue:**
- Look for `ChatPromptTemplate.from_messages()` combined with:
  - `json.dumps()` calls
  - F-strings containing data with potential `{}` characters
  - Dynamic data insertion directly into prompt strings

### **💡 Prevention Rules:**
1. **NEVER** use f-strings directly in `ChatPromptTemplate.from_messages()`
2. **ALWAYS** use direct message objects when dealing with JSON data
3. **PREFER** predefined templates in `LLMPromptSettings` with `.format()`
4. **TEST** all LLM methods with real data that contains curly braces

---

## 🚀 Standard Priority Processing (OpenAI)

### **Current Implementation Status:**
- ✅ Using standard `ChatOpenAI` client (default standard priority)
- ✅ Proper rate limiting implemented (60 requests/minute default)
- ✅ Concurrency control with semaphores (5 concurrent requests default)
- ✅ Retry logic with delays for rate limit handling

### **Cost Optimization Opportunities:**
- 🔄 Consider OpenAI Batch API for non-urgent large datasets (50% cost savings)
- 🔄 Implement request batching for multiple small operations
- 🔄 Add request queuing for optimal throughput

---

## 📝 Testing Patterns

### **Always Test LLM Methods With:**
```python
# Real data that could cause template conflicts
sample_data = {
    'records': [{'name': 'John', 'email': 'test@example.com'}],  # JSON with {}
    'types': {'name': 'string'},  # More {} characters
}
```

---

## 📊 LLM Input/Output Logging

### **Comprehensive Logging Added:**
All LLM interactions are now logged with detailed input and output tracking for debugging and monitoring:

### **Log Types:**
1. **`LLM_INTERACTION_SUCCESS`** (INFO level):
   ```json
   {
     "operation": "clean_data_chunk",
     "model": "gpt-4",
     "timestamp": "2025-08-23T16:31:46.870135",
     "input": ["SystemMessage: You are a data quality expert...", "HumanMessage: Please analyze..."],
     "output": "Here's a focused data quality assessment..."
   }
   ```

2. **`LLM_INTERACTION_ERROR`** (ERROR level):
   ```json
   {
     "operation": "clean_data_chunk",
     "model": "gpt-4", 
     "timestamp": "2025-08-23T16:31:46.870135",
     "input": ["SystemMessage: ...", "HumanMessage: ..."],
     "error": "Rate limit exceeded"
   }
   ```

3. **`LLM_DETAILED_INPUT/OUTPUT`** (DEBUG level):
   - Full, untruncated input and output data
   - Only logged when `ENABLE_DETAILED_LLM_LOGGING=true`

### **Features:**
- ✅ **Smart Truncation**: Input/output truncated to 500 chars for readability in INFO logs
- ✅ **Structured JSON**: Easy to parse for monitoring and analytics
- ✅ **Operation Tracking**: Each LLM method tagged with operation name
- ✅ **Model Tracking**: Know which model was used for each call
- ✅ **Timestamp Tracking**: ISO format timestamps for all interactions
- ✅ **Configurable**: Control detailed logging with `ENABLE_DETAILED_LLM_LOGGING`

### **Configuration:**
```bash
# Control LLM logging verbosity
ENABLE_DETAILED_LLM_LOGGING=true  # Shows full input/output at DEBUG level
LOG_LEVEL=DEBUG                   # Enable DEBUG logs to see detailed logs
```

### **Logged Methods:**
- `expand_rules()` ✅
- `clean_data_chunk()` ✅  
- `validate_geographical_data()` ✅
- `normalize_phone_numbers()` ✅
- `analyze_data_quality()` ✅ **REMOVED** (Redundant operation)

This document should be updated whenever critical patterns are discovered.
