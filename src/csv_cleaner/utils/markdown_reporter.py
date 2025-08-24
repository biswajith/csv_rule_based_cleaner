"""
Markdown Reporter - Generates human-readable markdown reports from processing results.
Creates clean, organized summaries that are easy to read and understand.
"""

import json
from typing import Dict, List, Any, Optional, Union
from datetime import datetime
from pathlib import Path
from loguru import logger


class MarkdownReporter:
    """
    Generates detailed, human-readable markdown reports from CSV cleaning results.
    Focuses on clarity, organization, and actionable insights.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        
    def generate_summary_report(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any],
        output_path: str
    ) -> str:
        """
        Generate a comprehensive markdown summary report.
        
        Args:
            processing_results: Results from the processing pipeline
            metadata: Additional metadata and analysis results
            output_path: Path where the markdown file should be saved
            
        Returns:
            Path to the generated markdown file
        """
        try:
            # Generate the markdown content
            markdown_content = self._build_markdown_content(processing_results, metadata)
            
            # Write to file
            markdown_path = f"{output_path}_summary.md"
            with open(markdown_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            
            logger.info(f"Generated markdown summary report: {markdown_path}")
            return markdown_path
            
        except Exception as e:
            logger.error(f"Error generating markdown summary: {str(e)}")
            raise
    
    def _build_markdown_content(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any]
    ) -> str:
        """Build the complete markdown content."""
        
        # Extract key information
        file_info = metadata.get("file_info", {})
        validation_report = metadata.get("validation_report", {})
        change_tracker = metadata.get("change_tracker", {})
        error_report = metadata.get("error_report", {})
        
        # Build markdown sections
        sections = [
            self._generate_header(file_info, processing_results),
            self._generate_executive_summary(processing_results, validation_report),
            self._generate_processing_overview(processing_results, metadata),
            self._generate_data_quality_section(validation_report),
            self._generate_changes_section(change_tracker),
            self._generate_issues_section(processing_results, error_report),
            self._generate_recommendations_section(validation_report),
            self._generate_technical_details(processing_results, metadata)
        ]
        
        return "\n\n".join(sections)
    
    def _generate_header(
        self, 
        file_info: Dict[str, Any], 
        processing_results: Dict[str, Any]
    ) -> str:
        """Generate the report header with basic information."""
        
        processed_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        input_file = file_info.get("file_path", "Unknown")
        total_records = file_info.get("total_records", 0)
        
        return f"""# CSV Cleaning Summary Report

**📄 File:** `{input_file}`  
**📊 Total Records:** {total_records:,}  
**📅 Processed:** {processed_at}  
**🆔 Session ID:** `{self.session_id}`

---"""

    def _generate_executive_summary(
        self, 
        processing_results: Dict[str, Any], 
        validation_report: Dict[str, Any]
    ) -> str:
        """Generate the executive summary section."""
        
        success_rate = processing_results.get("success_rate", 0)
        successful_chunks = processing_results.get("successful_chunks", 0)
        failed_chunks = processing_results.get("failed_chunks", 0)
        processing_time = processing_results.get("processing_time_seconds", 0)
        
        # Determine overall status
        if success_rate >= 0.9:
            status_icon = "✅"
            status_text = "Excellent"
        elif success_rate >= 0.7:
            status_icon = "⚠️"
            status_text = "Good with Issues"
        elif success_rate >= 0.5:
            status_icon = "❗"
            status_text = "Partial Success"
        else:
            status_icon = "❌"
            status_text = "Failed"
        
        # Get quality metrics if available
        executive_summary = validation_report.get("executive_summary", {})
        overall_improvement = executive_summary.get("overall_improvement_score", 0)
        
        return f"""## {status_icon} Executive Summary

**Overall Status:** {status_text}  
**Success Rate:** {success_rate:.1%}  
**Processing Time:** {processing_time:.1f} seconds  
**Data Quality Score:** {overall_improvement:.0f}/100

### Quick Stats
- ✅ **Successful Chunks:** {successful_chunks}
- ❌ **Failed Chunks:** {failed_chunks}
- ⏱️ **Processing Speed:** {processing_results.get("chunks_per_second", 0):.2f} chunks/second"""

    def _generate_processing_overview(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any]
    ) -> str:
        """Generate processing overview section."""
        
        rule_info = metadata.get("rule_initialization", {})
        original_rules = rule_info.get("original_rules", [])
        
        # Get model and configuration details
        file_info = metadata.get("file_info", {})
        chunk_size = metadata.get("chunk_size", "Unknown")
        
        content = f"""## 🔄 Processing Overview

### Data Processing
- **Chunk Size:** {chunk_size} records per chunk
- **Total Chunks:** {processing_results.get("successful_chunks", 0) + processing_results.get("failed_chunks", 0)}
- **Processing Strategy:** Parallel chunk processing with LLM validation

### Cleaning Rules Applied
"""
        
        if original_rules:
            for i, rule in enumerate(original_rules, 1):
                content += f"{i}. {rule}\n"
        else:
            content += "No specific rules were provided for this cleaning session.\n"
        
        return content

    def _generate_data_quality_section(
        self, 
        validation_report: Dict[str, Any]
    ) -> str:
        """Generate data quality analysis section."""
        
        quality_metrics = validation_report.get("data_quality_metrics", {})
        basic_metrics = quality_metrics.get("basic_metrics", {})
        
        if not quality_metrics:
            return """## 📊 Data Quality Analysis

⚠️ **No quality analysis available** - Processing may have failed before quality assessment could be completed."""

        original_count = basic_metrics.get("original_record_count", 0)
        cleaned_count = basic_metrics.get("cleaned_record_count", 0)
        retention_rate = basic_metrics.get("record_retention_rate", 0)
        
        content = f"""## 📊 Data Quality Analysis

### Record Processing
- **Original Records:** {original_count:,}
- **Successfully Cleaned:** {cleaned_count:,}
- **Retention Rate:** {retention_rate:.1%}

### Field Analysis"""
        
        # Field completeness analysis
        field_completeness = quality_metrics.get("field_completeness", {})
        if field_completeness:
            content += "\n\n**Field Completeness Improvements:**\n"
            for field, metrics in field_completeness.items():
                improvement = metrics.get("improvement", 0)
                if improvement > 0.05:  # Show fields with >5% improvement
                    content += f"- **{field}:** +{improvement:.1%} improvement\n"
                elif improvement < -0.05:  # Show fields with >5% degradation
                    content += f"- **{field}:** {improvement:.1%} (decreased)\n"
        
        # Consistency analysis
        consistency_metrics = quality_metrics.get("data_consistency", {})
        if consistency_metrics:
            content += "\n\n**Data Consistency Scores:**\n"
            for field, metrics in consistency_metrics.items():
                score = metrics.get("consistency_score", 0)
                if score < 0.8:
                    content += f"- **{field}:** {score:.1%} (needs attention)\n"
                elif score > 0.95:
                    content += f"- **{field}:** {score:.1%} (excellent)\n"
        
        return content

    def _generate_changes_section(
        self, 
        change_tracker: Dict[str, Any]
    ) -> str:
        """Generate changes and modifications section."""
        
        statistics = change_tracker.get("statistics", {})
        total_changes = statistics.get("total_changes", 0)
        
        if total_changes == 0:
            return """## 📝 Changes Made

**No changes were made during this cleaning session.**

This could indicate:
- The data was already clean
- Processing failed before changes could be applied
- Rules were too restrictive or didn't match the data"""

        content = f"""## 📝 Changes Made

**Total Changes:** {total_changes:,}

### Change Breakdown"""
        
        # Changes by field
        changes_by_field = statistics.get("changes_by_field", {})
        if changes_by_field:
            content += "\n\n**Changes by Field:**\n"
            sorted_fields = sorted(changes_by_field.items(), key=lambda x: x[1], reverse=True)
            for field, count in sorted_fields[:10]:  # Top 10 most changed fields
                content += f"- **{field}:** {count:,} changes\n"
        
        # Changes by reason
        changes_by_reason = statistics.get("changes_by_reason", {})
        if changes_by_reason:
            content += "\n\n**Reasons for Changes:**\n"
            sorted_reasons = sorted(changes_by_reason.items(), key=lambda x: x[1], reverse=True)
            for reason, count in sorted_reasons[:5]:  # Top 5 reasons
                content += f"- **{reason}:** {count:,} occurrences\n"
        
        # Confidence distribution
        confidence_dist = statistics.get("confidence_distribution", {})
        if confidence_dist:
            total = sum(confidence_dist.values())
            content += "\n\n**Confidence Levels:**\n"
            for level, count in confidence_dist.items():
                percentage = (count / total * 100) if total > 0 else 0
                content += f"- **{level.title()}:** {count:,} changes ({percentage:.1f}%)\n"
        
        return content

    def _generate_issues_section(
        self, 
        processing_results: Dict[str, Any], 
        error_report: Dict[str, Any]
    ) -> str:
        """Generate issues and errors section."""
        
        failures = processing_results.get("failures", [])
        error_stats = error_report.get("statistics", {})
        total_errors = error_stats.get("total_errors", 0)
        
        if not failures and total_errors == 0:
            return """## ✅ Issues and Errors

**No issues encountered during processing!**"""

        content = f"""## ⚠️ Issues and Errors

"""
        
        # Processing failures
        if failures:
            content += f"**Processing Failures:** {len(failures)} chunks failed\n\n"
            content += "**Failed Chunks:**\n"
            for chunk_index, error_message in failures:
                content += f"- **Chunk {chunk_index}:** {error_message}\n"
        
        # Error analysis
        if total_errors > 0:
            content += f"\n**Total Errors Logged:** {total_errors}\n"
            
            errors_by_category = error_stats.get("errors_by_category", {})
            if errors_by_category:
                content += "\n**Error Categories:**\n"
                for category, count in errors_by_category.items():
                    content += f"- **{category}:** {count} errors\n"
        
        return content

    def _generate_recommendations_section(
        self, 
        validation_report: Dict[str, Any]
    ) -> str:
        """Generate recommendations section."""
        
        recommendations = validation_report.get("recommendations", [])
        cleaning_effectiveness = validation_report.get("cleaning_effectiveness", {})
        effectiveness_recommendations = cleaning_effectiveness.get("recommendations", [])
        
        all_recommendations = recommendations + effectiveness_recommendations
        
        if not all_recommendations:
            return """## 💡 Recommendations

**No specific recommendations available.**"""

        content = f"""## 💡 Recommendations

Based on the analysis, here are actionable suggestions to improve your data cleaning process:

"""
        
        # Group recommendations by priority if available
        high_priority = []
        medium_priority = []
        other_recommendations = []
        
        for rec in all_recommendations:
            if isinstance(rec, dict):
                priority = rec.get("priority", "medium")
                recommendation_text = rec.get("recommendation", str(rec))
                if priority == "high":
                    high_priority.append(recommendation_text)
                elif priority == "medium":
                    medium_priority.append(recommendation_text)
                else:
                    other_recommendations.append(recommendation_text)
            else:
                other_recommendations.append(str(rec))
        
        if high_priority:
            content += "### 🔥 High Priority\n"
            for i, rec in enumerate(high_priority, 1):
                content += f"{i}. {rec}\n"
            content += "\n"
        
        if medium_priority:
            content += "### ⚡ Medium Priority\n"
            for i, rec in enumerate(medium_priority, 1):
                content += f"{i}. {rec}\n"
            content += "\n"
        
        if other_recommendations:
            content += "### 📋 Additional Suggestions\n"
            for i, rec in enumerate(other_recommendations, 1):
                content += f"{i}. {rec}\n"
        
        return content

    def _generate_technical_details(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any]
    ) -> str:
        """Generate technical details section."""
        
        from ..config.settings import settings
        
        return f"""## 🔧 Technical Details

### Processing Configuration
- **LLM Model:** `{settings.openai_model}`
- **Chunk Size:** {settings.chunk_size} records
- **Max Concurrent Requests:** {settings.max_concurrent_requests}
- **Processing Time:** {processing_results.get("processing_time_seconds", 0):.2f} seconds
- **Throughput:** {processing_results.get("records_per_second", 0):.1f} records/second

### Session Information
- **Session ID:** `{self.session_id}`
- **Start Time:** {metadata.get("session_start_time", "Unknown")}
- **Processing Strategy:** Parallel chunk processing with LLM validation
- **Output Format:** JSON + Markdown summary

### Performance Metrics
- **Chunks per Second:** {processing_results.get("chunks_per_second", 0):.2f}
- **Success Rate:** {processing_results.get("success_rate", 0):.1%}
- **Memory Usage:** Optimized chunk-based processing
- **Error Recovery:** Automatic retry with exponential backoff

---

*Report generated by CSV Rule-Based Cleaner v1.0*  
*For questions or support, please refer to the project documentation.*"""

    def generate_quick_summary(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any]
    ) -> str:
        """
        Generate a quick, one-page summary for immediate review.
        
        Args:
            processing_results: Results from processing
            metadata: Additional metadata
            
        Returns:
            Quick summary as markdown string
        """
        
        file_info = metadata.get("file_info", {})
        success_rate = processing_results.get("success_rate", 0)
        total_records = file_info.get("total_records", 0)
        processing_time = processing_results.get("processing_time_seconds", 0)
        
        # Status determination
        if success_rate >= 0.9:
            status = "✅ SUCCESS"
        elif success_rate >= 0.5:
            status = "⚠️ PARTIAL SUCCESS"
        else:
            status = "❌ FAILED"
        
        change_tracker = metadata.get("change_tracker", {})
        total_changes = change_tracker.get("statistics", {}).get("total_changes", 0)
        
        return f"""# Quick Summary - {status}

**File:** `{file_info.get("file_path", "Unknown")}`  
**Records:** {total_records:,} | **Success Rate:** {success_rate:.1%} | **Time:** {processing_time:.1f}s

## Results
- **Changes Made:** {total_changes:,}
- **Processing Speed:** {processing_results.get("records_per_second", 0):.0f} records/second
- **Status:** {"Completed successfully" if success_rate > 0.8 else "Completed with issues" if success_rate > 0 else "Failed to process"}

## Next Steps
{self._get_quick_recommendations(processing_results, metadata)}

---
*Session: {self.session_id} | Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*"""

    def _get_quick_recommendations(
        self, 
        processing_results: Dict[str, Any], 
        metadata: Dict[str, Any]
    ) -> str:
        """Get quick recommendations for immediate action."""
        
        success_rate = processing_results.get("success_rate", 0)
        
        if success_rate == 0:
            return "❗ **Review errors and check data format/rules compatibility**"
        elif success_rate < 0.5:
            return "⚠️ **Investigate processing failures and consider adjusting chunk size**"
        elif success_rate < 0.9:
            return "📊 **Review partial results and address failed chunks**"
        else:
            return "✅ **Review cleaned data and export results**"
