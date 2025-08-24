"""
Main entry point for CSV Rule-Based Cleaner.
Example usage of the LLM-driven CSV cleaning system.
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from csv_cleaner import CSVCleaner, DataProcessor
from csv_cleaner.config.settings import settings, validate_settings


async def main():
    """Main example function demonstrating CSV cleaning."""
    
    # Configure logging
    logger.add("logs/csv_cleaner.log", rotation="10 MB", level=settings.log_level)
    logger.info("Starting CSV Cleaner example")
    
    try:
        # Validate settings
        validate_settings()
        logger.info("Settings validated successfully")
        
        # Example usage
        processor = DataProcessor()
        
        # Example rules (these would come from user input)
        example_rules = [
            "Names should be properly capitalized (first letter uppercase, rest lowercase)",
            "Email addresses must be valid format with @ symbol",
            "Phone numbers should be in (XXX) XXX-XXXX format",
            "States must be valid US state abbreviations",
            "Cities must match the provided state",
            "Remove any obviously invalid or test data",
            "Standardize address formats",
            "Ensure data consistency across related fields"
        ]
        
        # Check for command line arguments
        csv_file = "examples/sample_data.csv"
        rules_source = example_rules  # Default to hardcoded rules
        
        if len(sys.argv) > 1:
            csv_file = sys.argv[1]
            if not Path(csv_file).exists():
                logger.error(f"CSV file does not exist: {csv_file}")
                return
                
        if len(sys.argv) > 2:
            # Rules file provided as second argument
            rules_file = sys.argv[2]
            if Path(rules_file).exists():
                logger.info(f"Using rules from file: {rules_file}")
                rules_source = rules_file
            else:
                logger.error(f"Rules file does not exist: {rules_file}")
                return
        
        # Check if CSV file exists, create sample if needed
        if not Path(csv_file).exists() and csv_file == "examples/sample_data.csv":
            logger.info("Creating a sample CSV for demonstration...")
            create_sample_csv(csv_file)
        
        example_file = csv_file
        
        # Ask user if they want to proceed with cleaning (only in interactive mode)
        if len(sys.argv) <= 1:  # Only ask in interactive mode
            print(f"\nReady to clean CSV file: {example_file}")
            rules_count = len(rules_source) if isinstance(rules_source, list) else "from file"
            print(f"Rules to apply: {rules_count}")
            proceed = input("\nProceed with CSV cleaning? (y/n): ").lower().strip()
            if proceed != 'y':
                logger.info("Cleaning cancelled by user")
                return
        
        # Full cleaning
        logger.info("Starting full CSV cleaning...")
        results = await processor.process_file(example_file, rules_source)
        
        if results.get("status") == "completed":
            logger.info("CSV cleaning completed successfully")
            print("\n" + "="*50)
            print("CLEANING COMPLETED")
            print("="*50)
            
            summary = results.get("processing_summary", {})
            print(f"Records processed: {summary.get('records_processed', 0)}")
            print(f"Chunks processed: {summary.get('chunks_processed', 0)}")
            print(f"Success rate: {summary.get('success_rate', 0):.2%}")
            print(f"Total changes: {summary.get('total_changes', 0)}")
            print(f"Processing time: {summary.get('total_time_seconds', 0):.2f} seconds")
            
            output_files = results.get("output_files", {})
            print(f"\nOutput files generated:")
            for file_type, file_path in output_files.items():
                print(f"  {file_type}: {file_path}")
            
            print("="*50)
            
        else:
            logger.error(f"Cleaning failed: {results.get('error')}")
            print(f"\nCleaning failed: {results.get('error')}")
    
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        print(f"Error: {str(e)}")


def create_sample_csv(file_path: str):
    """Create a sample CSV file for demonstration."""
    import pandas as pd
    
    # Create sample data with intentional issues for cleaning
    sample_data = [
        {
            "first_name": "john",
            "last_name": "DOE",
            "email": "john.doe@email",
            "phone": "5551234567",
            "city": "new york",
            "state": "NY",
            "country": "USA"
        },
        {
            "first_name": "JANE",
            "last_name": "smith",
            "email": "jane@email.com",
            "phone": "(555) 987-6543",
            "city": "Los Angeles",
            "state": "CA",
            "country": "United States"
        },
        {
            "first_name": "Bob",
            "last_name": "Johnson",
            "email": "bob.johnson@test.com",
            "phone": "555.555.5555",
            "city": "chicago",
            "state": "IL",
            "country": "US"
        },
        {
            "first_name": "alice",
            "last_name": "WILLIAMS",
            "email": "alice.williams@company.co.uk",
            "phone": "+1-555-123-4567",
            "city": "Houston",
            "state": "TX",
            "country": "USA"
        },
        {
            "first_name": "Charlie",
            "last_name": "brown",
            "email": "charlie@invalid",
            "phone": "123-456-7890",
            "city": "Miami",
            "state": "FL",
            "country": "United States of America"
        }
    ]
    
    # Create examples directory
    Path("examples").mkdir(exist_ok=True)
    
    # Save to CSV
    df = pd.DataFrame(sample_data)
    df.to_csv(file_path, index=False)
    
    logger.info(f"Created sample CSV file: {file_path}")
    print(f"Created sample CSV file: {file_path}")


if __name__ == "__main__":
    # Run the main function
    asyncio.run(main())
