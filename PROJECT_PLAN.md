# CSV Rule-Based Cleaner with LLM Assistance

## Project Overview

A Python-based system that cleans person/lead information from CSV files using Large Language Models (LLM) for intelligent data validation and cleaning. The system will use OpenAI through LangChain for all data processing decisions, with user-provided rules as the foundation for cleaning logic.

## Core Requirements Analysis

### 1. **LLM-First Approach**
- All data cleaning decisions made by OpenAI LLM via LangChain
- No hardcoded rules or assumptions in the codebase
- User-provided rules serve as the foundation for LLM decision-making
- No fallback to regex or Python-based cleaning

### 2. **Dynamic Rule Processing**
- Accept user-provided rules without assumptions about CSV structure
- LLM expands basic rules into comprehensive cleaning principles
- Rules applied contextually based on CSV column headers
- No validation for fields without user-provided rules

### 3. **Scalability & Performance**
- Process large CSV files in chunks of 250 records
- Maintain header context in each chunk for LLM processing
- Retry mechanism (maximum 1 attempts) for LLM failures

### 4. **Special Handling Requirements**
- Phone number format normalization and validation
- Geographical data validation (state, city, country relationships)
- Cross-field validation for related geographical information

### 5. **Output Requirements**
- JSON format output with cleaned person/lead information
- Detailed correction log showing what was changed and why

## System Architecture

### Component Overview

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   CSV Input     │───▶│  Rule Processor  │───▶│ LLM Integration │
│   + User Rules  │    │   & Validator    │    │   (LangChain)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  JSON Output    │◄───│   Data Chunker   │◄───│   OpenAI API    │
│  + Change Log   │    │   & Processor    │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

### Core Components

#### 1. **CSV Reader & Analyzer**
- **Purpose**: Parse CSV files and analyze structure
- **Responsibilities**:
  - Read CSV files with flexible column detection
  - Identify column headers and data types
  - Detect potential geographical fields based on header names
  - Prepare data for chunking

#### 2. **Rule Processor**
- **Purpose**: Process user rules and expand them via LLM
- **Responsibilities**:
  - Accept user-provided rules in natural language
  - Send rules + CSV headers to LLM for rule expansion
  - Generate comprehensive cleaning instructions
  - Create field-specific validation criteria

#### 3. **Data Chunker**
- **Purpose**: Break large CSV files into manageable chunks
- **Responsibilities**:
  - Split CSV into 250-record chunks
  - Include headers in each chunk for LLM context
  - Maintain data integrity across chunks
  - Track chunk processing progress

#### 4. **LLM Integration Layer (LangChain)**
- **Purpose**: Orchestrate all LLM interactions
- **Responsibilities**:
  - Rule expansion and validation
  - Data cleaning decisions
  - Geographical validation
  - Phone number normalization
  - Error detection and correction
  - Generate change explanations

#### 5. **Special Validators**
- **Purpose**: Handle domain-specific validation logic
- **Responsibilities**:
  - **Phone Number Handler**: Detect phone fields, normalize formats
  - **Geography Validator**: Validate state/city/country relationships
  - **Cross-field Validator**: Check related field consistency

#### 6. **Output Generator**
- **Purpose**: Format final results and change logs
- **Responsibilities**:
  - Convert cleaned data to JSON format
  - Generate detailed change logs
  - Include validation results and corrections made
  - Provide summary statistics

## Detailed Implementation Plan

### Phase 1: Core Infrastructure

#### 1.1 Project Setup
- Initialize Python project with proper structure
- Set up dependency management (requirements.txt)
- Configure LangChain and OpenAI integration
- Create configuration management system

#### 1.2 CSV Processing Foundation
- Implement flexible CSV reader
- Create column header analysis system
- Build data type detection logic
- Implement chunking mechanism for large files

#### 1.3 LangChain Integration
- Set up OpenAI client through LangChain
- Create prompt templates for different operations
- Implement retry mechanism with exponential backoff
- Add error handling and logging

### Phase 2: Rule Processing System

#### 2.1 Rule Input Handler
- Create interface for user rule input
- Implement rule parsing and validation
- Store rules in structured format

#### 2.2 LLM Rule Expansion
- Design prompts for rule expansion
- Implement rule enhancement logic
- Create context-aware rule application
- Generate field-specific cleaning instructions

#### 2.3 Dynamic Validation Framework
- Build LLM-based validation system
- Implement context-aware field validation
- Create validation result tracking

### Phase 3: Specialized Handlers

#### 3.1 Phone Number Processing
- Implement phone field detection via LLM
- Create phone number normalization prompts
- Handle international format variations
- Validate phone number authenticity

#### 3.2 Geographical Data Validation
- Build geographical field detection
- Implement cross-field geographical validation
- Create state/city/country relationship checking
- Handle geographical inconsistencies

#### 3.3 Cross-Field Validation
- Implement related field identification
- Create consistency checking logic
- Handle field relationship validation

### Phase 4: Processing Engine

#### 4.1 Chunk Processing Pipeline
- Implement parallel chunk processing
- Create progress tracking system
- Handle inter-chunk dependencies
- Implement result aggregation

#### 4.2 Change Tracking System
- Track all data modifications
- Record reasoning for each change
- Maintain audit trail
- Generate detailed change reports

#### 4.3 Error Handling & Retry Logic
- Implement robust error handling
- Create retry mechanisms for LLM failures
- Handle partial processing scenarios
- Provide graceful degradation

### Phase 5: Output & Reporting

#### 5.1 JSON Output Generator
- Convert cleaned data to structured JSON
- Include metadata and processing information
- Implement data serialization

#### 5.2 Change Log Generator
- Create detailed change reports
- Include before/after comparisons
- Provide reasoning for each modification
- Generate summary statistics

#### 5.3 Validation Reporting
- Create validation result summaries
- Include confidence scores
- Provide recommendations for manual review

## Technical Specifications

### Technology Stack
- **Python 3.9+**
- **LangChain**: LLM orchestration and prompt management
- **OpenAI API**: GPT-4 for intelligent decision making
- **Pandas**: CSV processing and data manipulation
- **Pydantic**: Data validation and serialization
- **asyncio**: Asynchronous processing for performance
- **logging**: Comprehensive logging and monitoring

### Data Flow

1. **Input Processing**
   ```
   CSV File → Column Analysis → Rule Application → Chunk Creation
   ```

2. **LLM Processing**
   ```
   Chunk + Rules → LLM Analysis → Cleaning Instructions → Data Modification
   ```

3. **Output Generation**
   ```
   Cleaned Data → JSON Conversion → Change Log → Final Output
   ```

### Prompt Engineering Strategy

#### Rule Expansion Prompt Template
```
Given these user rules: {user_rules}
And these CSV columns: {column_headers}
Please expand these rules into comprehensive data cleaning instructions...
```

#### Data Cleaning Prompt Template
```
Given this data chunk: {data_chunk}
And these cleaning rules: {expanded_rules}
Please clean the data according to the rules and explain all changes...
```

#### Geographical Validation Prompt Template
```
Given these geographical fields: {geo_fields}
Please validate the consistency of state/city/country relationships...
```

## Configuration & Customization

### User Configuration Options
- OpenAI API key and model selection
- Chunk size (default 250 records, make this configurable via .env)
- Retry attempts (default 1)
- Output format preferences
- Logging levels
- Custom field type hints

### Rule Definition Format
Users can provide rules in natural language:
```
- Names should be properly capitalized
- Email addresses must be valid format
- Phone numbers should be in (XXX) XXX-XXXX format
- States must be valid US state abbreviations
- Cities must match the provided state
```

## Quality Assurance & Testing

### Testing Strategy
- Unit tests for each component
- Integration tests for LLM interactions
- Performance tests with large CSV files
- Validation accuracy testing
- Error handling scenario testing

### Monitoring & Logging
- Comprehensive logging throughout the pipeline
- Performance metrics tracking
- LLM interaction monitoring
- Error rate tracking
- Data quality metrics

## Deployment Considerations

### Performance Optimization
- Asynchronous processing for LLM calls
- Caching for repeated rule expansions
- Batch processing optimization
- Memory management for large files

### Scalability
- Configurable chunk sizes
- Parallel processing support
- Rate limiting for API calls
- Resource usage monitoring

### Security
- Secure API key management
- Data privacy considerations
- Input validation and sanitization
- Output data protection

## Success Metrics

### Accuracy Metrics
- Data cleaning accuracy rate
- Geographical validation success rate
- Phone number normalization accuracy
- Rule application consistency

### Performance Metrics
- Processing speed (records per minute)
- LLM response times
- Memory usage efficiency
- Error rates and retry success

### User Experience Metrics
- Rule definition ease of use
- Output quality and completeness
- Change log clarity and usefulness
- System reliability and uptime

## Future Enhancements

### Potential Extensions
- Support for additional data types (dates, addresses)
- Machine learning model integration for pattern detection
- Real-time processing capabilities
- Web interface for rule definition
- Integration with data pipeline tools
- Support for multiple output formats

### Advanced Features
- Confidence scoring for cleaning decisions
- A/B testing for different cleaning approaches
- Custom prompt template creation
- Integration with external validation APIs
- Automated rule learning from examples

## Risk Mitigation

### Technical Risks
- **LLM API failures**: Implement robust retry mechanisms and error handling
- **Large file processing**: Optimize chunking and memory management
- **Data quality issues**: Comprehensive validation and logging
- **Performance bottlenecks**: Asynchronous processing and optimization

### Business Risks
- **Cost management**: Monitor API usage and implement cost controls
- **Data privacy**: Ensure secure handling of sensitive information
- **Scalability limits**: Design for horizontal scaling
- **User adoption**: Focus on ease of use and clear documentation

This comprehensive plan provides a roadmap for building a sophisticated, LLM-driven CSV cleaning system that meets all specified requirements while maintaining flexibility and scalability.

## Architectural Lessons Learned

### Critical Design Flaw: Distributed Rule Processing

**Problem Identified**: The initial implementation scattered rule processing across multiple components, violating the single responsibility principle and creating unnecessary complexity:

1. **Multiple Parsing Points**: Rules were being parsed in `RuleProcessor`, `CSVCleaner`, `DataProcessor`, and `LLMClient`
2. **Redundant Processing**: Rule expansion was happening in multiple locations
3. **Tight Coupling**: Components were tightly coupled through rule processing logic
4. **Maintenance Burden**: Changes to rule handling required updates in multiple files

**Root Cause**: Lack of centralized rule management led to:
```
❌ BEFORE (Distributed Processing):
DataProcessor → CSVCleaner → RuleProcessor → LLMClient
     ↓              ↓             ↓           ↓
Rule parsing → Rule setting → Rule expansion → Rule application
```

**Solution Implemented**: Centralized Rule Management with `RuleManager`:
```
✅ AFTER (Centralized Processing):
RuleManager (Single Entry Point)
     ↓
Complete rule processing (parse → expand → cache)
     ↓
Components receive only expanded rules
```

**Benefits Achieved**:
- **Single Responsibility**: Each component has one clear purpose
- **Reduced Complexity**: Rule processing happens once, upfront
- **Better Maintainability**: Changes isolated to one component
- **Cleaner Interfaces**: Components receive simple, processed inputs
- **Performance**: No redundant rule processing

### Architectural Principles Reinforced

1. **Single Source of Truth**: Rule processing centralized in `RuleManager`
2. **Separation of Concerns**: Rule loading vs. rule application are distinct
3. **Dependency Inversion**: Components depend on processed rules, not rule sources
4. **Open/Closed Principle**: Easy to extend rule sources without changing processors

### Similar Inefficiencies Identified

#### 1. **Output File Generation Patterns**
**Issue**: Previously had 8 separate output files with redundant metadata
**Solution**: Consolidated to 2 files with centralized output generation
**Lesson**: Avoid creating multiple files when one consolidated file serves better

#### 2. **Validation Component Duplication**
**Issue Confirmed**: Multiple validator classes with significant overlapping responsibilities and patterns:

```python
# CURRENT DUPLICATION FOUND:
PhoneValidator(llm_client)       → 5 LLM interaction methods
GeographyValidator(llm_client)   → 5 LLM interaction methods  
CrossFieldValidator(llm_client)  → 5 LLM interaction methods
```

**Specific Duplications Identified**:
- **LLM Chain Pattern**: Each validator recreates the same `chat_prompt | self.llm_client.client | self.llm_client.output_parser` pattern
- **Error Handling**: Identical try/catch patterns in each validator
- **Response Parsing**: Similar JSON parsing and response formatting
- **Prompt Structure**: Nearly identical prompt template patterns

**Performance Impact**: 
- Each validator makes separate LLM calls for related data
- Potential for redundant processing of the same records
- No coordination between validators

**Recommendation**: Create a `ValidationManager` that:
- Coordinates all validation in a single LLM call
- Shares validation context between validators
- Eliminates redundant prompt patterns

#### 3. **LLM Client Instantiation Anti-Pattern**
**Issue Confirmed**: Multiple components creating their own LLM client instances:

```python
# FOUND IN ANALYSIS:
CSVCleaner.__init__():        self.llm_client = LLMClient()
RuleManager.__init__():       self.llm_client = llm_client or LLMClient()  
ChunkProcessor.__init__():    self.llm_client = llm_client or LLMClient()
ParallelProcessor.__init__():  self.llm_client = llm_client or LLMClient()
PhoneValidator.__init__():    self.llm_client = llm_client or LLMClient()
GeographyValidator.__init__(): self.llm_client = llm_client or LLMClient()
CrossFieldValidator.__init__(): self.llm_client = llm_client or LLMClient()
```

**Problems Identified**:
- **Resource Waste**: Multiple OpenAI client connections
- **Configuration Drift**: Each client might have different settings
- **Rate Limiting Issues**: Separate rate limiting per client
- **Cost Tracking**: Difficult to monitor total API usage

**Current Mitigation**: Most components accept injected `llm_client`, but still fall back to creating new instances

**Recommendation**: 
- Single LLM client instance shared across all components
- Dependency injection pattern enforced everywhere
- Remove all fallback `LLMClient()` instantiations

#### 4. **Redundant Old Component Still Present**
**Issue Found**: `RuleProcessor` class still exists alongside new `RuleManager`:

```python
# ARCHITECTURAL DEBT:
src/csv_cleaner/processors/rule_processor.py  ← OLD (duplicates RuleManager functionality)
src/csv_cleaner/core/rule_manager.py         ← NEW (centralized approach)
```

**Impact**: 
- Code confusion and maintenance burden
- Risk of using wrong component
- Duplicated logic and potential inconsistencies

**Action Required**: Remove deprecated `RuleProcessor` after ensuring complete migration

#### 5. **Performance Monitoring Distribution** 
**Issue Confirmed**: Performance monitoring calls scattered across 15+ locations:

```python
# EXAMPLES FOUND:
self.performance_monitor.profile_operation("csv_reading")
self.performance_monitor.profile_operation("quality_analysis") 
self.performance_monitor.profile_operation("rule_expansion")
self.performance_monitor.record_data_volume_metrics()
self.performance_monitor.record_chunk_processing_metrics()
```

**Problems**:
- Manual instrumentation required in every component
- Easy to miss performance tracking in new code
- Inconsistent metric collection patterns
- No automatic correlation between related operations

**Recommendation**: 
- Decorator-based performance monitoring
- Automatic instrumentation for LLM calls
- Centralized performance reporting

### Design Guidelines for Future Development

1. **Centralize Cross-Cutting Concerns**: Rule processing, error handling, performance monitoring
2. **Single Entry Point Pattern**: For any complex processing that multiple components need
3. **Avoid Redundant Processing**: Cache and reuse expensive operations (LLM calls)
4. **Clear Component Boundaries**: Each component should have a single, well-defined responsibility
5. **Dependency Flow**: High-level components should process and pass simple data to lower-level components

### Refactoring Checklist for Future Reviews

- [ ] Are any operations being performed in multiple places?
- [ ] Could any complex processing be centralized?
- [ ] Are component interfaces as simple as possible?
- [ ] Is there clear separation between data loading and data processing?
- [ ] Are cross-cutting concerns properly centralized?

This architectural lesson demonstrates the importance of regular code review and refactoring to maintain clean, maintainable architecture as systems grow in complexity.
