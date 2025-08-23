"""
Shared pytest configuration and fixtures.
"""

import pytest
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Test configuration
@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Setup test environment."""
    # Set test environment variables
    os.environ["OPENAI_API_KEY"] = "test_api_key"
    os.environ["LOG_LEVEL"] = "ERROR"  # Reduce log noise during tests
    os.environ["CHUNK_SIZE"] = "10"  # Small chunks for testing
    
    yield
    
    # Cleanup
    test_vars = ["OPENAI_API_KEY", "LOG_LEVEL", "CHUNK_SIZE"]
    for var in test_vars:
        if var in os.environ:
            del os.environ[var]


@pytest.fixture
def mock_openai_api_key():
    """Provide mock API key for tests."""
    return "test_openai_api_key_12345"


@pytest.fixture
def sample_user_rules():
    """Standard set of user rules for testing."""
    return [
        "Names should be properly capitalized",
        "Email addresses must be valid format",
        "Phone numbers should be in (XXX) XXX-XXXX format", 
        "States must be valid US state abbreviations",
        "Remove any test or invalid data"
    ]


@pytest.fixture
def sample_csv_headers():
    """Standard CSV headers for testing."""
    return [
        "first_name", "last_name", "email", "phone", 
        "city", "state", "country", "zip_code"
    ]


@pytest.fixture
def sample_csv_data():
    """Sample CSV data for testing."""
    return [
        {
            "first_name": "john",
            "last_name": "DOE", 
            "email": "john@test.com",
            "phone": "5551234567",
            "city": "new york",
            "state": "NY",
            "country": "USA",
            "zip_code": "10001"
        },
        {
            "first_name": "JANE",
            "last_name": "smith",
            "email": "jane.smith@email.com", 
            "phone": "(555) 987-6543",
            "city": "Los Angeles",
            "state": "CA", 
            "country": "United States",
            "zip_code": "90210"
        },
        {
            "first_name": "Bob",
            "last_name": "Johnson",
            "email": "invalid_email",
            "phone": "555.555.5555",
            "city": "Chicago",
            "state": "IL",
            "country": "US", 
            "zip_code": "60601"
        }
    ]
