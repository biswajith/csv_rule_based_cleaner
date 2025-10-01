#!/usr/bin/env python3
"""
Benchmark script to compare hybrid (vector + LLM) vs pure LLM processing.
"""

import asyncio
import time
import pandas as pd
from pathlib import Path
import sys
import os

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from csv_cleaner.core.csv_reader import CSVReader
from csv_cleaner.core.llm_client import LLMClient
from csv_cleaner.processors.hybrid_processor import HybridProcessor
from csv_cleaner.processors.vector_processor import VectorProcessor
from csv_cleaner.config.settings import settings


async def benchmark_vector_only(csv_file: str):
    """Benchmark pure vectorized processing."""
    print("\n" + "="*60)
    print("BENCHMARK: Vector-Only Processing")
    print("="*60)
    
    # Load data
    csv_reader = CSVReader()
    csv_reader.read_csv(csv_file)
    df = csv_reader.df.copy()
    
    print(f"Processing {len(df)} records with vectorized operations only...")
    
    # Run vector processing
    start_time = time.time()
    vector_processor = VectorProcessor()
    cleaned_df, change_logs = vector_processor.process_dataframe(df)
    total_time = time.time() - start_time
    
    # Results
    stats = vector_processor.get_stats()
    print(f"\n📊 VECTOR-ONLY RESULTS:")
    print(f"⏱️  Total time: {total_time:.3f} seconds")
    print(f"📧 Emails fixed: {stats['emails_fixed']}")
    print(f"🚩 Emails flagged: {stats['emails_flagged']}")
    print(f"📞 Phones normalized: {stats['phones_normalized']}")
    print(f"🚩 Phones flagged: {stats['phones_flagged']}")
    print(f"📝 Total changes logged: {len(change_logs)}")
    print(f"⚡ Records/second: {len(df)/total_time:.0f}")
    
    return {
        'approach': 'vector_only',
        'total_time': total_time,
        'records_processed': len(df),
        'changes_logged': len(change_logs),
        'records_per_second': len(df)/total_time,
        'stats': stats
    }


async def benchmark_hybrid(csv_file: str, rules_file: str):
    """Benchmark hybrid processing (vectors + LLM)."""
    print("\n" + "="*60)
    print("BENCHMARK: Hybrid Processing (Vectors + LLM)")
    print("="*60)
    
    # Initialize components
    csv_reader = CSVReader()
    csv_reader.read_csv(csv_file)
    
    llm_client = LLMClient()
    hybrid_processor = HybridProcessor(llm_client)
    
    print(f"Processing {len(csv_reader.df)} records with hybrid approach...")
    
    # Run hybrid processing
    start_time = time.time()
    results = await hybrid_processor.process_file(csv_file, rules_file, csv_reader)
    total_time = time.time() - start_time
    
    # Results
    perf_stats = hybrid_processor.get_performance_stats()
    print(f"\n📊 HYBRID RESULTS:")
    print(f"⏱️  Total time: {total_time:.3f} seconds")
    print(f"⚡ Vector processing: {perf_stats['vector_processing_time']:.3f}s")
    print(f"🤖 LLM processing: {perf_stats['llm_processing_time']:.3f}s")
    print(f"📊 Records needing LLM: {perf_stats['records_needing_llm']}/{perf_stats['total_records']} ({perf_stats['records_needing_llm']/perf_stats['total_records']*100:.1f}%)")
    print(f"📝 Vector changes: {perf_stats['vector_changes']}")
    print(f"📝 LLM changes: {perf_stats['llm_changes']}")
    print(f"📝 Total changes: {perf_stats['vector_changes'] + perf_stats['llm_changes']}")
    print(f"⚡ Overall records/second: {perf_stats['total_records']/total_time:.0f}")
    print(f"🔥 Efficiency ratio: {perf_stats['efficiency_ratio']*100:.1f}% vectorized")
    
    return {
        'approach': 'hybrid',
        'total_time': total_time,
        'vector_time': perf_stats['vector_processing_time'],
        'llm_time': perf_stats['llm_processing_time'],
        'records_processed': perf_stats['total_records'],
        'records_needing_llm': perf_stats['records_needing_llm'],
        'llm_percentage': perf_stats['records_needing_llm']/perf_stats['total_records']*100,
        'total_changes': perf_stats['vector_changes'] + perf_stats['llm_changes'],
        'records_per_second': perf_stats['total_records']/total_time,
        'efficiency_ratio': perf_stats['efficiency_ratio']
    }


async def benchmark_current_approach(csv_file: str, rules_file: str):
    """Benchmark current LLM-only approach for comparison."""
    print("\n" + "="*60)
    print("BENCHMARK: Current LLM-Only Approach")
    print("="*60)
    
    # This would run your current main.py approach
    # For now, we'll simulate based on your previous results
    
    csv_reader = CSVReader()
    csv_reader.read_csv(csv_file)
    record_count = len(csv_reader.df)
    
    # Based on your previous results: 50 records in 48-63 seconds
    estimated_time_per_record = 1.0  # Conservative estimate
    estimated_total_time = record_count * estimated_time_per_record
    
    print(f"📊 ESTIMATED LLM-ONLY RESULTS (based on previous runs):")
    print(f"📊 Records to process: {record_count}")
    print(f"⏱️  Estimated time: {estimated_total_time:.1f} seconds ({estimated_total_time/60:.1f} minutes)")
    print(f"💰 Estimated API calls: {record_count // settings.chunk_size}")
    print(f"⚡ Estimated records/second: {1/estimated_time_per_record:.1f}")
    
    return {
        'approach': 'llm_only_estimated',
        'total_time': estimated_total_time,
        'records_processed': record_count,
        'records_per_second': 1/estimated_time_per_record,
        'api_calls_estimated': record_count // settings.chunk_size
    }


def compare_results(results_list):
    """Compare and summarize benchmark results."""
    print("\n" + "="*80)
    print("📊 PERFORMANCE COMPARISON")
    print("="*80)
    
    for result in results_list:
        approach = result['approach']
        time_taken = result['total_time']
        rps = result['records_per_second']
        
        print(f"\n{approach.upper().replace('_', ' ')}:")
        print(f"  ⏱️  Time: {time_taken:.1f}s")
        print(f"  ⚡ Speed: {rps:.0f} records/second")
        
        if 'llm_percentage' in result:
            print(f"  🤖 LLM usage: {result['llm_percentage']:.1f}% of records")
        
        if 'efficiency_ratio' in result:
            print(f"  🔥 Vectorized: {result['efficiency_ratio']*100:.1f}% of processing time")
    
    # Calculate speedup
    if len(results_list) >= 2:
        baseline = next(r for r in results_list if 'estimated' in r['approach'])
        hybrid = next(r for r in results_list if r['approach'] == 'hybrid')
        vector = next(r for r in results_list if r['approach'] == 'vector_only')
        
        hybrid_speedup = baseline['total_time'] / hybrid['total_time']
        vector_speedup = baseline['total_time'] / vector['total_time']
        
        print(f"\n🚀 SPEEDUP ANALYSIS:")
        print(f"  Hybrid vs LLM-only: {hybrid_speedup:.1f}x faster")
        print(f"  Vector-only vs LLM-only: {vector_speedup:.1f}x faster")
        print(f"  Time saved (hybrid): {baseline['total_time'] - hybrid['total_time']:.1f} seconds")


async def main():
    """Run all benchmarks."""
    # Default files
    csv_file = "test_leads_with_issues.csv"
    rules_file = "test_rules.txt"
    
    # Check if files exist
    if not Path(csv_file).exists():
        print(f"❌ CSV file not found: {csv_file}")
        return
    
    if not Path(rules_file).exists():
        print(f"❌ Rules file not found: {rules_file}")
        return
    
    print("🚀 Starting Performance Benchmarks")
    print(f"📁 CSV file: {csv_file}")
    print(f"📋 Rules file: {rules_file}")
    
    results = []
    
    try:
        # Benchmark 1: Vector-only processing
        vector_result = await benchmark_vector_only(csv_file)
        results.append(vector_result)
        
        # Benchmark 2: Hybrid processing
        hybrid_result = await benchmark_hybrid(csv_file, rules_file)
        results.append(hybrid_result)
        
        # Benchmark 3: Current approach (estimated)
        current_result = await benchmark_current_approach(csv_file, rules_file)
        results.append(current_result)
        
        # Compare results
        compare_results(results)
        
    except Exception as e:
        print(f"❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
