# CSV Rule-Based Cleaner with LLM Assistance

A Python-based system that cleans person/lead information from CSV files using Large Language Models (OpenAI GPT) for intelligent data validation and cleaning. All data cleaning decisions are made by the LLM based on user-provided rules.

## Features

- **LLM-First Approach**: All data cleaning decisions made by OpenAI LLM via LangChain
- **Dynamic Rule Processing**: User-provided rules expanded by LLM into comprehensive cleaning instructions
- **Scalable Processing**: Handles large CSV files through chunked processing
- **Intelligent Validation**: Phone number normalization and geographical data validation via LLM
- **Detailed Logging**: Complete audit trail of all changes made
- **JSON Output**: Structured output with cleaned data and change logs

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd csv_rule_based_cleaner
```

2. Create and activate virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp env.example .env
# Edit .env file with your OpenAI API key and preferences
```

## Configuration

Create a `.env` file in the project root with the following variables:

```bash
# OpenAI API Configuration
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4
OPENAI_MAX_TOKENS=4000
OPENAI_TEMPERATURE=0.1

# Processing Configuration
CHUNK_SIZE=250
MAX_RETRIES=1
RETRY_DELAY=1.0
ENABLE_PARALLEL_PROCESSING=true

# Parallel Processing Control
# Set to false for sequential processing (useful for debugging, 
# rate limiting concerns, or when experiencing API rate limits)

# Logging Configuration
LOG_LEVEL=INFO
LOG_FILE=logs/csv_cleaner.log

# Output Configuration
OUTPUT_FORMAT=json
INCLUDE_CHANGE_LOG=true
GENERATE_SUMMARY=true

# Performance Configuration
MAX_CONCURRENT_REQUESTS=5
REQUEST_TIMEOUT=30.0
RATE_LIMIT_REQUESTS_PER_MINUTE=60
```

## Usage

### Basic Usage

```python
import asyncio
from csv_cleaner import CSVCleaner

async def clean_csv_file():
    cleaner = CSVCleaner()
    
    # Define your cleaning rules
    rules = [
        "Names should be properly capitalized",
        "Email addresses must be valid format",
        "Phone numbers should be in (XXX) XXX-XXXX format",
        "States must be valid US state abbreviations",
        "Remove any test or invalid data"
    ]
    
    # Clean the CSV file
    results = await cleaner.clean_csv("input.csv", rules)
    
    if results["status"] == "completed":
        print(f"Cleaning completed! Output files: {results['output_files']}")
    else:
        print(f"Cleaning failed: {results['error']}")

# Run the cleaning
asyncio.run(clean_csv_file())
```

### Command Line Usage

Run the example script:
```bash
# Basic usage with sample data
python main.py

# With custom CSV file
python main.py path/to/your/data.csv

# With custom CSV file and rules file
python main.py path/to/your/data.csv path/to/your/rules.txt
```

If no file is provided, it will create a sample CSV for demonstration.

### Rule Definition Options

You can provide cleaning rules in multiple ways:

#### 1. Inline Rules (List)
```python
rules = [
    "Names should be properly capitalized",
    "Email addresses must be valid format",
    "Phone numbers should be in (XXX) XXX-XXXX format"
]
```

#### 2. Rules from File
```python
# Using file path string
rules = "path/to/rules.txt"

# Using Path object
from pathlib import Path
rules = Path("path/to/rules.txt")
```

#### 3. Rules File Format
Create a text file with one rule per line:
```txt
# This is a comment - lines starting with # are ignored
Names should be properly capitalized
Email addresses must be valid format
Phone numbers should be in (XXX) XXX-XXXX format

# Empty lines are also ignored
Remove any test or invalid data
```

### Preview Before Cleaning

```python
import asyncio
from csv_cleaner import DataProcessor

async def preview_cleaning():
    processor = DataProcessor()
    
    # Can use list of rules OR path to rules file
    rules = "examples/cleaning_rules.txt"  # or use a list
    
    # Preview on first 10 rows
    preview = await processor.preview_file("input.csv", rules, preview_rows=10)
    
    if preview["status"] == "success":
        print("Preview successful!")
        print(f"Sample data: {preview['preview_data']}")

asyncio.run(preview_cleaning())
```

## System Architecture

The system follows a modular, LLM-first architecture:

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

- **CSV Reader**: Flexible CSV parsing and structure analysis
- **LLM Client**: OpenAI integration via LangChain for all decisions
- **Rule Processor**: Expands user rules into comprehensive instructions
- **Chunk Processor**: Handles large files through parallel chunk processing
- **Output Generator**: Creates JSON output and detailed change logs

## Rule Definition

Define rules in natural language - the LLM will interpret and apply them:

```python
rules = [
    "Names should be properly capitalized (first letter uppercase, rest lowercase)",
    "Email addresses must be valid format with @ symbol and domain",
    "Phone numbers should be normalized to (XXX) XXX-XXXX format",
    "States must be valid US state abbreviations (2 letters)",
    "Cities must be consistent with the provided state",
    "Remove any obviously fake or test data (like 'test@test.com')",
    "Standardize address formats for consistency",
    "Ensure geographical data relationships are correct"
]
```

## Output Files

The system generates several output files:

1. **cleaned_data.json**: Main output with cleaned records
2. **changelog.json**: Detailed log of all changes made
3. **summary.json**: Processing summary and statistics
4. **cleaned_data.csv**: CSV format output (optional)

### Sample Output Structure

```json
{
  "metadata": {
    "generated_at": "2024-01-15T10:30:00",
    "session_id": "20240115_103000",
    "total_records": 1000,
    "total_changes_made": 150,
    "success_rate": 0.98
  },
  "cleaning_rules": {
    "original_rules": ["Names should be capitalized", "..."],
    "expanded_rules": "Comprehensive LLM-generated instructions..."
  },
  "cleaned_data": [
    {
      "first_name": "John",
      "last_name": "Doe",
      "email": "john.doe@email.com",
      "phone": "(555) 123-4567"
    }
  ]
}
```

## Performance

- **Chunked Processing**: Handles large files by processing in chunks (default: 250 records)
- **Configurable Processing**: Parallel or sequential chunk processing
- **Rate Limiting**: Respects OpenAI API limits  
- **Retry Logic**: Automatic retry for failed LLM calls

### Processing Modes

**Parallel Processing (Default)**: `ENABLE_PARALLEL_PROCESSING=true`
- ✅ **Faster processing** for large files
- ✅ **Efficient resource usage** through concurrent chunk processing
- ⚠️ **Higher API usage** - may hit rate limits with large files

**Sequential Processing**: `ENABLE_PARALLEL_PROCESSING=false`  
- ✅ **Better for debugging** - easier to trace issues
- ✅ **Avoids rate limits** - processes chunks one at a time
- ✅ **Consistent progress** - predictable processing order
- ⚠️ **Slower processing** - takes longer for large files

## Logging

Comprehensive logging throughout the system:

```bash
# View logs
tail -f logs/csv_cleaner.log

# Adjust log level in .env file
LOG_LEVEL=DEBUG  # DEBUG, INFO, WARNING, ERROR
```

## Error Handling

The system includes robust error handling:

- LLM API failures with retry logic
- Malformed CSV files
- Invalid user rules
- Large file processing errors
- Network connectivity issues

## Testing

Run the test suite:

```bash
# Install test dependencies (already in requirements.txt)
pip install pytest pytest-asyncio

# Run tests
pytest tests/

# Run with coverage
pytest --cov=src tests/
```

## Development

### Code Quality

The project uses several code quality tools:

```bash
# Format code
black src/ tests/

# Lint code
flake8 src/ tests/

# Type checking
mypy src/

# Pre-commit hooks
pre-commit install
```

### Project Structure

```
csv_rule_based_cleaner/
├── src/csv_cleaner/           # Main package
│   ├── core/                  # Core components (LLM, Rules, Validation)
│   │   ├── cleaner.py         # Main CSV cleaning orchestrator
│   │   ├── processor.py       # High-level data processing interface
│   │   ├── rule_manager.py    # Centralized rule processing
│   │   ├── validation_manager.py # Unified validation coordination
│   │   ├── llm_client.py      # OpenAI LLM integration
│   │   └── csv_reader.py      # CSV file reading and parsing
│   ├── processors/            # Data processing modules
│   │   ├── chunk_processor.py # Chunk-based processing
│   │   ├── parallel_processor.py # Parallel processing
│   │   └── change_tracker.py  # Change tracking and audit
│   ├── utils/                 # Utility modules
│   │   ├── output_generator.py # Output file generation
│   │   ├── error_handler.py   # Error handling and recovery
│   │   ├── performance_monitor.py # Performance tracking
│   │   └── validation_reporter.py # Validation reporting
│   └── config/                # Configuration management
│       └── settings.py        # Application settings
├── tests/                     # Test suite
├── examples/                  # Example files and rules
├── docs/                      # Documentation
├── logs/                      # Log files
├── output/                    # Generated outputs
└── main.py                    # Example entry point
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## License

[License information here]

## Support

For questions or issues:
1. Check the documentation
2. Review existing issues
3. Create a new issue with detailed information

## Roadmap

- [ ] Web interface for rule definition
- [ ] Support for additional file formats
- [ ] Real-time processing capabilities
- [ ] Integration with data pipeline tools
- [ ] Custom LLM model support
- [ ] Confidence scoring for cleaning decisions
