"""
Validation Reporter - Comprehensive validation reporting and analysis.
Generates detailed reports on data quality, validation results, and cleaning effectiveness.
"""

import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from loguru import logger
from pathlib import Path

from ..processors.change_tracker import ChangeTracker, ChangeType, ConfidenceLevel
from ..utils.error_handler import ErrorHandler


class ValidationReporter:
    """
    Comprehensive validation reporting system that generates detailed
    reports on data quality, cleaning effectiveness, and validation results.
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.validation_results: Dict[str, Any] = {}
        self.quality_metrics: Dict[str, Any] = {}
        self.cleaning_effectiveness: Dict[str, Any] = {}
        
    def add_validation_results(
        self, 
        validation_type: str, 
        results: Dict[str, Any], 
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Add validation results to the report.
        
        Args:
            validation_type: Type of validation (e.g., 'phone', 'geography', 'cross_field')
            results: Validation results dictionary
            metadata: Additional metadata about the validation
        """
        self.validation_results[validation_type] = {
            "results": results,
            "metadata": metadata or {},
            "timestamp": datetime.now().isoformat(),
            "status": results.get("status", "unknown")
        }
        
        logger.debug(f"Added {validation_type} validation results to report")
    
    def calculate_quality_metrics(
        self, 
        original_data: List[Dict[str, Any]], 
        cleaned_data: List[Dict[str, Any]],
        change_tracker: ChangeTracker
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive data quality metrics.
        
        Args:
            original_data: Original data before cleaning
            cleaned_data: Data after cleaning
            change_tracker: Change tracking instance
            
        Returns:
            Quality metrics dictionary
        """
        if not original_data:
            return {"error": "No original data provided"}
        
        # Basic metrics
        original_count = len(original_data)
        cleaned_count = len(cleaned_data)
        
        # Field completeness analysis
        field_completeness = self._analyze_field_completeness(original_data, cleaned_data)
        
        # Data consistency analysis
        consistency_metrics = self._analyze_data_consistency(cleaned_data)
        
        # Change impact analysis
        change_impact = self._analyze_change_impact(change_tracker)
        
        # Quality improvement scores
        improvement_scores = self._calculate_improvement_scores(
            original_data, 
            cleaned_data, 
            change_tracker
        )
        
        self.quality_metrics = {
            "basic_metrics": {
                "original_record_count": original_count,
                "cleaned_record_count": cleaned_count,
                "record_retention_rate": cleaned_count / original_count if original_count > 0 else 0,
                "total_fields": len(original_data[0].keys()) if original_data else 0
            },
            "field_completeness": field_completeness,
            "data_consistency": consistency_metrics,
            "change_impact": change_impact,
            "improvement_scores": improvement_scores,
            "calculated_at": datetime.now().isoformat()
        }
        
        return self.quality_metrics
    
    def _analyze_field_completeness(
        self, 
        original_data: List[Dict[str, Any]], 
        cleaned_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze field completeness before and after cleaning."""
        if not original_data or not cleaned_data:
            return {}
        
        # Get all fields
        all_fields = set()
        for record in original_data + cleaned_data:
            all_fields.update(record.keys())
        
        completeness_analysis = {}
        
        for field in all_fields:
            # Original completeness
            original_non_empty = sum(
                1 for record in original_data 
                if record.get(field) is not None and str(record.get(field)).strip()
            )
            original_completeness = original_non_empty / len(original_data) if original_data else 0
            
            # Cleaned completeness
            cleaned_non_empty = sum(
                1 for record in cleaned_data 
                if record.get(field) is not None and str(record.get(field)).strip()
            )
            cleaned_completeness = cleaned_non_empty / len(cleaned_data) if cleaned_data else 0
            
            completeness_analysis[field] = {
                "original_completeness": original_completeness,
                "cleaned_completeness": cleaned_completeness,
                "improvement": cleaned_completeness - original_completeness,
                "original_non_empty_count": original_non_empty,
                "cleaned_non_empty_count": cleaned_non_empty
            }
        
        return completeness_analysis
    
    def _analyze_data_consistency(self, cleaned_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze data consistency in cleaned data."""
        if not cleaned_data:
            return {}
        
        consistency_metrics = {}
        
        # Get all fields
        all_fields = set()
        for record in cleaned_data:
            all_fields.update(record.keys())
        
        for field in all_fields:
            field_values = [
                record.get(field) for record in cleaned_data 
                if record.get(field) is not None
            ]
            
            if not field_values:
                continue
            
            # Analyze format consistency
            string_values = [str(v).strip() for v in field_values if str(v).strip()]
            
            # Check for consistent formatting patterns
            format_patterns = {}
            for value in string_values:
                # Simple pattern analysis
                pattern = self._extract_format_pattern(value)
                format_patterns[pattern] = format_patterns.get(pattern, 0) + 1
            
            # Calculate consistency score
            if format_patterns:
                most_common_count = max(format_patterns.values())
                consistency_score = most_common_count / len(string_values)
            else:
                consistency_score = 0
            
            consistency_metrics[field] = {
                "consistency_score": consistency_score,
                "unique_values": len(set(string_values)),
                "total_values": len(string_values),
                "format_patterns": format_patterns,
                "most_common_pattern": max(format_patterns, key=format_patterns.get) if format_patterns else None
            }
        
        return consistency_metrics
    
    def _extract_format_pattern(self, value: str) -> str:
        """Extract a simple format pattern from a value."""
        if not value:
            return "empty"
        
        # Simple pattern extraction
        pattern = ""
        for char in value:
            if char.isalpha():
                pattern += "A"
            elif char.isdigit():
                pattern += "0"
            elif char.isspace():
                pattern += " "
            else:
                pattern += char
        
        return pattern
    
    def _analyze_change_impact(self, change_tracker: ChangeTracker) -> Dict[str, Any]:
        """Analyze the impact of changes made during cleaning."""
        change_stats = change_tracker.get_change_statistics()
        
        # Analyze change distribution
        changes_by_confidence = {}
        for confidence in ConfidenceLevel:
            confidence_changes = [
                c for c in change_tracker.changes 
                if c.confidence == confidence
            ]
            changes_by_confidence[confidence.value] = {
                "count": len(confidence_changes),
                "percentage": len(confidence_changes) / len(change_tracker.changes) if change_tracker.changes else 0
            }
        
        # Analyze change types
        changes_by_type = {}
        for change_type in ChangeType:
            type_changes = [
                c for c in change_tracker.changes 
                if c.change_type == change_type
            ]
            changes_by_type[change_type.value] = {
                "count": len(type_changes),
                "percentage": len(type_changes) / len(change_tracker.changes) if change_tracker.changes else 0
            }
        
        return {
            "total_changes": change_stats.get("total_changes", 0),
            "changes_by_confidence": changes_by_confidence,
            "changes_by_type": changes_by_type,
            "unique_records_affected": change_stats.get("unique_records_affected", 0),
            "unique_fields_affected": change_stats.get("unique_fields_affected", 0),
            "most_changed_field": change_stats.get("most_changed_field"),
            "most_common_change_type": change_stats.get("most_common_change_type")
        }
    
    def _calculate_improvement_scores(
        self, 
        original_data: List[Dict[str, Any]], 
        cleaned_data: List[Dict[str, Any]],
        change_tracker: ChangeTracker
    ) -> Dict[str, Any]:
        """Calculate overall improvement scores."""
        if not original_data or not cleaned_data:
            return {}
        
        # Overall data quality score (0-100)
        # Based on completeness, consistency, and change confidence
        
        # Completeness improvement
        original_total_fields = sum(
            len([v for v in record.values() if v is not None and str(v).strip()])
            for record in original_data
        )
        cleaned_total_fields = sum(
            len([v for v in record.values() if v is not None and str(v).strip()])
            for record in cleaned_data
        )
        
        completeness_score = min(100, (cleaned_total_fields / max(original_total_fields, 1)) * 100)
        
        # Confidence score based on changes
        high_confidence_changes = len([
            c for c in change_tracker.changes 
            if c.confidence == ConfidenceLevel.HIGH
        ])
        total_changes = len(change_tracker.changes)
        confidence_score = (high_confidence_changes / max(total_changes, 1)) * 100 if total_changes > 0 else 100
        
        # Consistency score (simplified)
        consistency_metrics = self._analyze_data_consistency(cleaned_data)
        avg_consistency = sum(
            metrics.get("consistency_score", 0) 
            for metrics in consistency_metrics.values()
        ) / max(len(consistency_metrics), 1)
        consistency_score = avg_consistency * 100
        
        # Overall improvement score
        overall_score = (completeness_score + confidence_score + consistency_score) / 3
        
        return {
            "overall_improvement_score": overall_score,
            "completeness_score": completeness_score,
            "confidence_score": confidence_score,
            "consistency_score": consistency_score,
            "score_explanation": {
                "completeness": "Based on field completeness improvement",
                "confidence": "Based on confidence levels of changes made",
                "consistency": "Based on data format consistency",
                "overall": "Average of all component scores"
            }
        }
    
    def analyze_cleaning_effectiveness(
        self, 
        processing_results: Dict[str, Any], 
        change_tracker: ChangeTracker,
        error_handler: Optional[ErrorHandler] = None
    ) -> Dict[str, Any]:
        """
        Analyze the effectiveness of the cleaning process.
        
        Args:
            processing_results: Results from the processing pipeline
            change_tracker: Change tracking instance
            error_handler: Optional error handler for error analysis
            
        Returns:
            Cleaning effectiveness analysis
        """
        effectiveness_analysis = {
            "processing_efficiency": self._analyze_processing_efficiency(processing_results),
            "change_quality": self._analyze_change_quality(change_tracker),
            "error_analysis": self._analyze_errors(error_handler) if error_handler else {},
            "recommendation_score": 0,
            "recommendations": []
        }
        
        # Calculate recommendation score and generate recommendations
        recommendation_analysis = self._generate_effectiveness_recommendations(effectiveness_analysis)
        effectiveness_analysis.update(recommendation_analysis)
        
        self.cleaning_effectiveness = effectiveness_analysis
        return effectiveness_analysis
    
    def _analyze_processing_efficiency(self, processing_results: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze processing efficiency metrics."""
        return {
            "success_rate": processing_results.get("success_rate", 0),
            "processing_time": processing_results.get("processing_time_seconds", 0),
            "chunks_processed": processing_results.get("successful_chunks", 0),
            "chunks_failed": processing_results.get("failed_chunks", 0),
            "chunks_per_second": processing_results.get("chunks_per_second", 0),
            "efficiency_rating": self._calculate_efficiency_rating(processing_results)
        }
    
    def _calculate_efficiency_rating(self, processing_results: Dict[str, Any]) -> str:
        """Calculate efficiency rating based on processing metrics."""
        success_rate = processing_results.get("success_rate", 0)
        chunks_per_second = processing_results.get("chunks_per_second", 0)
        
        if success_rate >= 0.95 and chunks_per_second >= 1.0:
            return "excellent"
        elif success_rate >= 0.90 and chunks_per_second >= 0.5:
            return "good"
        elif success_rate >= 0.80 and chunks_per_second >= 0.2:
            return "fair"
        else:
            return "poor"
    
    def _analyze_change_quality(self, change_tracker: ChangeTracker) -> Dict[str, Any]:
        """Analyze quality of changes made."""
        if not change_tracker.changes:
            return {"no_changes": True}
        
        # Confidence distribution
        high_confidence = len([c for c in change_tracker.changes if c.confidence == ConfidenceLevel.HIGH])
        medium_confidence = len([c for c in change_tracker.changes if c.confidence == ConfidenceLevel.MEDIUM])
        low_confidence = len([c for c in change_tracker.changes if c.confidence == ConfidenceLevel.LOW])
        total_changes = len(change_tracker.changes)
        
        return {
            "total_changes": total_changes,
            "high_confidence_percentage": (high_confidence / total_changes) * 100,
            "medium_confidence_percentage": (medium_confidence / total_changes) * 100,
            "low_confidence_percentage": (low_confidence / total_changes) * 100,
            "change_quality_score": (high_confidence * 1.0 + medium_confidence * 0.6 + low_confidence * 0.3) / total_changes * 100
        }
    
    def _analyze_errors(self, error_handler: ErrorHandler) -> Dict[str, Any]:
        """Analyze error patterns and impact."""
        error_stats = error_handler.get_error_statistics()
        
        return {
            "total_errors": error_stats.get("total_errors", 0),
            "resolution_rate": error_stats.get("resolution_stats", {}).get("resolution_rate", 0),
            "most_common_error_category": error_stats.get("most_common_error_category"),
            "error_impact_score": self._calculate_error_impact_score(error_stats)
        }
    
    def _calculate_error_impact_score(self, error_stats: Dict[str, Any]) -> float:
        """Calculate error impact score (0-100, lower is better)."""
        total_errors = error_stats.get("total_errors", 0)
        resolution_rate = error_stats.get("resolution_stats", {}).get("resolution_rate", 1.0)
        
        if total_errors == 0:
            return 0  # No errors, perfect score
        
        # Impact increases with number of errors and decreases with resolution rate
        base_impact = min(total_errors * 5, 100)  # Cap at 100
        resolved_impact = base_impact * (1 - resolution_rate)
        
        return resolved_impact
    
    def _generate_effectiveness_recommendations(self, effectiveness_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Generate recommendations based on effectiveness analysis."""
        recommendations = []
        score = 100  # Start with perfect score and deduct points
        
        # Analyze processing efficiency
        processing_efficiency = effectiveness_analysis.get("processing_efficiency", {})
        success_rate = processing_efficiency.get("success_rate", 1.0)
        
        if success_rate < 0.9:
            recommendations.append("Consider reviewing data quality or rule definitions to improve success rate")
            score -= (1.0 - success_rate) * 30
        
        if processing_efficiency.get("efficiency_rating") == "poor":
            recommendations.append("Optimize processing configuration or consider reducing chunk size")
            score -= 20
        
        # Analyze change quality
        change_quality = effectiveness_analysis.get("change_quality", {})
        if not change_quality.get("no_changes", False):
            change_quality_score = change_quality.get("change_quality_score", 100)
            if change_quality_score < 70:
                recommendations.append("Many changes have low confidence - consider refining rules or data sources")
                score -= (100 - change_quality_score) * 0.2
        
        # Analyze errors
        error_analysis = effectiveness_analysis.get("error_analysis", {})
        error_impact = error_analysis.get("error_impact_score", 0)
        if error_impact > 20:
            recommendations.append("High error impact detected - review error patterns and improve error handling")
            score -= error_impact * 0.5
        
        return {
            "recommendation_score": max(0, score),
            "recommendations": recommendations
        }
    
    def generate_comprehensive_report(
        self, 
        original_data: List[Dict[str, Any]],
        cleaned_data: List[Dict[str, Any]],
        processing_results: Dict[str, Any],
        change_tracker: ChangeTracker,
        error_handler: Optional[ErrorHandler] = None
    ) -> Dict[str, Any]:
        """
        Generate a comprehensive validation and quality report.
        
        Args:
            original_data: Original data before cleaning
            cleaned_data: Data after cleaning
            processing_results: Processing pipeline results
            change_tracker: Change tracking instance
            error_handler: Optional error handler
            
        Returns:
            Comprehensive validation report
        """
        # Calculate all metrics
        quality_metrics = self.calculate_quality_metrics(original_data, cleaned_data, change_tracker)
        effectiveness_analysis = self.analyze_cleaning_effectiveness(processing_results, change_tracker, error_handler)
        
        # Generate executive summary
        executive_summary = self._generate_executive_summary(
            quality_metrics, 
            effectiveness_analysis, 
            processing_results
        )
        
        # Compile comprehensive report
        comprehensive_report = {
            "report_metadata": {
                "session_id": self.session_id,
                "generated_at": datetime.now().isoformat(),
                "report_version": "1.0",
                "data_summary": {
                    "original_records": len(original_data),
                    "cleaned_records": len(cleaned_data),
                    "processing_time": processing_results.get("processing_time_seconds", 0)
                }
            },
            "executive_summary": executive_summary,
            "data_quality_metrics": quality_metrics,
            "validation_results": self.validation_results,
            "cleaning_effectiveness": effectiveness_analysis,
            "processing_results": processing_results,
            "change_analysis": change_tracker.generate_change_report(),
            "error_analysis": error_handler.get_error_report() if error_handler else {},
            "recommendations": self._generate_final_recommendations(
                quality_metrics, 
                effectiveness_analysis
            )
        }
        
        return comprehensive_report
    
    def _generate_executive_summary(
        self, 
        quality_metrics: Dict[str, Any], 
        effectiveness_analysis: Dict[str, Any],
        processing_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate executive summary of the cleaning process."""
        improvement_scores = quality_metrics.get("improvement_scores", {})
        overall_score = improvement_scores.get("overall_improvement_score", 0)
        
        # Determine overall success level
        if overall_score >= 90:
            success_level = "excellent"
        elif overall_score >= 75:
            success_level = "good"
        elif overall_score >= 60:
            success_level = "fair"
        else:
            success_level = "needs_improvement"
        
        return {
            "overall_success_level": success_level,
            "overall_improvement_score": overall_score,
            "key_achievements": self._identify_key_achievements(quality_metrics, effectiveness_analysis),
            "areas_for_improvement": self._identify_improvement_areas(quality_metrics, effectiveness_analysis),
            "processing_efficiency": effectiveness_analysis.get("processing_efficiency", {}).get("efficiency_rating", "unknown"),
            "data_quality_impact": "positive" if overall_score > 50 else "limited",
            "recommendation_summary": f"Review {len(effectiveness_analysis.get('recommendations', []))} specific recommendations for optimization"
        }
    
    def _identify_key_achievements(
        self, 
        quality_metrics: Dict[str, Any], 
        effectiveness_analysis: Dict[str, Any]
    ) -> List[str]:
        """Identify key achievements from the cleaning process."""
        achievements = []
        
        # Check completeness improvements
        field_completeness = quality_metrics.get("field_completeness", {})
        improved_fields = [
            field for field, metrics in field_completeness.items()
            if metrics.get("improvement", 0) > 0.1  # 10% improvement
        ]
        
        if improved_fields:
            achievements.append(f"Significantly improved completeness for {len(improved_fields)} fields")
        
        # Check processing efficiency
        processing_efficiency = effectiveness_analysis.get("processing_efficiency", {})
        if processing_efficiency.get("efficiency_rating") in ["excellent", "good"]:
            achievements.append("Achieved efficient processing with high success rate")
        
        # Check change quality
        change_quality = effectiveness_analysis.get("change_quality", {})
        if change_quality.get("high_confidence_percentage", 0) > 70:
            achievements.append("Majority of changes made with high confidence")
        
        return achievements
    
    def _identify_improvement_areas(
        self, 
        quality_metrics: Dict[str, Any], 
        effectiveness_analysis: Dict[str, Any]
    ) -> List[str]:
        """Identify areas needing improvement."""
        improvement_areas = []
        
        # Check for low improvement scores
        improvement_scores = quality_metrics.get("improvement_scores", {})
        if improvement_scores.get("completeness_score", 100) < 70:
            improvement_areas.append("Data completeness could be further improved")
        
        if improvement_scores.get("consistency_score", 100) < 70:
            improvement_areas.append("Data format consistency needs attention")
        
        # Check processing issues
        processing_efficiency = effectiveness_analysis.get("processing_efficiency", {})
        if processing_efficiency.get("success_rate", 1.0) < 0.9:
            improvement_areas.append("Processing success rate should be improved")
        
        return improvement_areas
    
    def _generate_final_recommendations(
        self, 
        quality_metrics: Dict[str, Any], 
        effectiveness_analysis: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate final actionable recommendations."""
        recommendations = []
        
        # Add structured recommendations with priorities
        existing_recommendations = effectiveness_analysis.get("recommendations", [])
        
        for i, rec in enumerate(existing_recommendations):
            recommendations.append({
                "priority": "high" if i < 2 else "medium",
                "category": "processing_optimization",
                "recommendation": rec,
                "expected_impact": "improved processing efficiency"
            })
        
        # Add data quality recommendations
        improvement_scores = quality_metrics.get("improvement_scores", {})
        if improvement_scores.get("consistency_score", 100) < 70:
            recommendations.append({
                "priority": "medium",
                "category": "data_quality",
                "recommendation": "Implement additional format standardization rules",
                "expected_impact": "improved data consistency"
            })
        
        return recommendations
    
    def export_report(self, report: Dict[str, Any], file_path: str) -> bool:
        """Export comprehensive report to JSON file."""
        try:
            # Ensure output directory exists
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Exported comprehensive validation report to {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error exporting report to {file_path}: {str(e)}")
            return False
