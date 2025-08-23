"""
Quick Start Example for CSV Rule-Based Cleaner

This example demonstrates how to use the CSV cleaning system.
Make sure to set your OPENAI_API_KEY environment variable before running.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from csv_cleaner import CSVCleaner, DataProcessor


async def quick_example():
    """Quick example of using the CSV cleaner."""
    
    print("CSV Rule-Based Cleaner - Quick Start Example")
    print("=" * 50)
    
    # Check if API key is set
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  OPENAI_API_KEY environment variable not set!")
        print("Please set your OpenAI API key:")
        print("export OPENAI_API_KEY='your-api-key-here'")
        print("\nFor testing without API key, see the README.md")
        return
    
    try:
        # Initialize the processor
        processor = DataProcessor()
        print("✅ CSV Cleaner initialized successfully")
        
        # Define some example rules
        rules = [
            "Names should be properly capitalized",
            "Email addresses must be valid format", 
            "Phone numbers should be standardized",
            "Remove any test or placeholder data"
        ]
        
        print(f"📋 Defined {len(rules)} cleaning rules")
        
        # Check session info
        session_info = processor.get_session_info()
        print(f"🔍 Session ID: {session_info['session_id']}")
        
        print("\n✨ System ready for CSV processing!")
        print("Use processor.process_file('your_file.csv', rules) to clean a CSV file")
        print("Use processor.preview_file('your_file.csv', rules) to preview changes")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        print("Check your configuration and dependencies")


if __name__ == "__main__":
    asyncio.run(quick_example())
