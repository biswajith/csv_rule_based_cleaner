"""
Vector Processor - High-performance pattern-based cleaning for emails and phone numbers.
Uses pandas vectorization for predictable patterns, leaves complex logic to LLM.
"""

import pandas as pd
import numpy as np
import re
from typing import Dict, List, Any, Tuple, Optional
from loguru import logger
import time


class VectorProcessor:
    """
    Handles vectorized cleaning for predictable patterns like emails and phone numbers.
    Designed to process 100K+ records in seconds vs minutes with LLM.
    """
    
    def __init__(self):
        self.stats = {
            'emails_fixed': 0,
            'emails_flagged': 0,
            'phones_normalized': 0,
            'phones_flagged': 0,
            'processing_time': 0
        }
        
        # Phone number letter-to-digit mapping (old phone keypad)
        self.phone_letter_map = str.maketrans(
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
            '22233344455566677778889999'
        )
        
        # Common email domain fixes
        self.email_domain_fixes = {
            'gmial.com': 'gmail.com',
            'gmai.com': 'gmail.com', 
            'yahooo.com': 'yahoo.com',
            'yahoo.co': 'yahoo.com',
            'hotmial.com': 'hotmail.com',
            'outlok.com': 'outlook.com'
        }
    
    def process_dataframe(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
        """
        Process entire DataFrame with vectorized operations.
        
        Args:
            df: Input DataFrame
            
        Returns:
            Tuple of (cleaned_df, change_logs)
        """
        start_time = time.time()
        logger.info(f"Starting vectorized processing of {len(df)} records")
        
        # Create a copy to avoid modifying original
        cleaned_df = df.copy()
        all_change_logs = []
        
        # Process emails if email column exists
        if 'email' in cleaned_df.columns:
            email_changes = self._process_emails_vectorized(cleaned_df)
            all_change_logs.extend(email_changes)
        
        # Process phone numbers if phone column exists  
        if 'phone' in cleaned_df.columns:
            phone_changes = self._process_phones_vectorized(cleaned_df)
            all_change_logs.extend(phone_changes)
        
        self.stats['processing_time'] = time.time() - start_time
        logger.info(f"Vectorized processing completed in {self.stats['processing_time']:.3f}s")
        logger.info(f"Stats: {self.stats}")
        
        return cleaned_df, all_change_logs
    
    def _process_emails_vectorized(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Vectorized email processing - fixes common patterns and flags issues.
        
        Args:
            df: DataFrame with 'email' column (modified in place)
            
        Returns:
            List of change log entries
        """
        change_logs = []
        original_emails = df['email'].copy()
        
        logger.debug("Processing emails with vectorized operations")
        
        # Fix 1: Remove double @ symbols (emily@@domain.com → emily@domain.com)
        double_at_mask = df['email'].str.contains('@@', na=False)
        if double_at_mask.any():
            df.loc[double_at_mask, 'email'] = df.loc[double_at_mask, 'email'].str.replace('@@', '@')
            self.stats['emails_fixed'] += double_at_mask.sum()
            
            # Log changes
            for idx in df[double_at_mask].index:
                change_logs.append({
                    'row_number': idx + 2,  # CSV row (1-indexed + header)
                    'column_number': df.columns.get_loc('email') + 1,
                    'column_name': 'email',
                    'field': 'email',
                    'issue': 'Double @ symbol in email',
                    'original_value': original_emails.iloc[idx],
                    'corrected_value': df.iloc[idx]['email'],
                    'correction': f"Fixed double @ symbol: {original_emails.iloc[idx]} → {df.iloc[idx]['email']}",
                    'notes': 'Vectorized email cleanup'
                })
        
        # Fix 2: Remove extra dots (user..name@domain.com → user.name@domain.com)
        double_dot_mask = df['email'].str.contains('\\.\\.', na=False)
        if double_dot_mask.any():
            df.loc[double_dot_mask, 'email'] = df.loc[double_dot_mask, 'email'].str.replace('..', '.')
            self.stats['emails_fixed'] += double_dot_mask.sum()
            
            # Log changes (for records not already logged)
            for idx in df[double_dot_mask & ~double_at_mask].index:
                change_logs.append({
                    'row_number': idx + 2,
                    'column_number': df.columns.get_loc('email') + 1, 
                    'column_name': 'email',
                    'field': 'email',
                    'issue': 'Double dots in email',
                    'original_value': original_emails.iloc[idx],
                    'corrected_value': df.iloc[idx]['email'],
                    'correction': f"Fixed double dots: {original_emails.iloc[idx]} → {df.iloc[idx]['email']}",
                    'notes': 'Vectorized email cleanup'
                })
        
        # Fix 3: Common domain typos
        for typo, correct in self.email_domain_fixes.items():
            typo_mask = df['email'].str.contains(f'@{re.escape(typo)}$', na=False)
            if typo_mask.any():
                df.loc[typo_mask, 'email'] = df.loc[typo_mask, 'email'].str.replace(f'@{typo}', f'@{correct}')
                self.stats['emails_fixed'] += typo_mask.sum()
                
                # Log changes
                for idx in df[typo_mask].index:
                    change_logs.append({
                        'row_number': idx + 2,
                        'column_number': df.columns.get_loc('email') + 1,
                        'column_name': 'email',
                        'field': 'email', 
                        'issue': f'Common domain typo: {typo}',
                        'original_value': original_emails.iloc[idx],
                        'corrected_value': df.iloc[idx]['email'],
                        'correction': f"Fixed domain typo: {original_emails.iloc[idx]} → {df.iloc[idx]['email']}",
                        'notes': 'Vectorized domain correction'
                    })
        
        # Flag 4: Invalid domains that can't be fixed
        invalid_patterns = [
            '@invalid',      # @invalid domain
            '@$',           # ends with @
            '@\\.',         # @. pattern
            '@[^.]*$'       # @ without domain
        ]
        
        for pattern in invalid_patterns:
            invalid_mask = df['email'].str.contains(pattern, na=False, regex=True)
            if invalid_mask.any():
                self.stats['emails_flagged'] += invalid_mask.sum()
                
                # Log issues (don't modify data)
                for idx in df[invalid_mask].index:
                    change_logs.append({
                        'row_number': idx + 2,
                        'column_number': df.columns.get_loc('email') + 1,
                        'column_name': 'email',
                        'field': 'email',
                        'issue': 'Invalid email domain - cannot be corrected',
                        'original_value': df.iloc[idx]['email'],
                        'corrected_value': df.iloc[idx]['email'],  # No change
                        'correction': 'Unable to fix invalid email domain',
                        'notes': f'Flagged by vectorized validation: pattern {pattern}'
                    })
        
        logger.debug(f"Email processing: {self.stats['emails_fixed']} fixed, {self.stats['emails_flagged']} flagged")
        return change_logs
    
    def _process_phones_vectorized(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Vectorized phone number processing - normalization and validation.
        
        Args:
            df: DataFrame with 'phone' column (modified in place)
            
        Returns:
            List of change log entries
        """
        change_logs = []
        original_phones = df['phone'].copy()
        
        logger.debug("Processing phone numbers with vectorized operations")
        
        # Step 1: Convert letters to numbers (555-ABCD-1234 → 555-2223-1234)
        letter_mask = df['phone'].str.contains('[A-Za-z]', na=False)
        if letter_mask.any():
            df.loc[letter_mask, 'phone'] = df.loc[letter_mask, 'phone'].str.translate(self.phone_letter_map)
            
            # Log conversions
            for idx in df[letter_mask].index:
                change_logs.append({
                    'row_number': idx + 2,
                    'column_number': df.columns.get_loc('phone') + 1,
                    'column_name': 'phone', 
                    'field': 'phone',
                    'issue': 'Letters in phone number',
                    'original_value': original_phones.iloc[idx],
                    'corrected_value': df.iloc[idx]['phone'],
                    'correction': f"Converted letters to digits: {original_phones.iloc[idx]} → {df.iloc[idx]['phone']}",
                    'notes': 'Vectorized phone letter conversion'
                })
        
        # Step 2: Extract and normalize all digits
        df['phone_digits'] = df['phone'].str.replace(r'\D', '', regex=True)
        
        # Step 3: Format 10-digit numbers as (XXX) XXX-XXXX
        ten_digit_mask = df['phone_digits'].str.len() == 10
        if ten_digit_mask.any():
            # Create formatted phone numbers for all 10-digit entries
            formatted_pattern = df['phone_digits'].str.replace(
                r'^(\d{3})(\d{3})(\d{4})$', 
                r'(\1) \2-\3', 
                regex=True
            )
            
            # Check which ones actually need formatting (format is different from current)
            for idx in df[ten_digit_mask].index:
                current_phone = df.loc[idx, 'phone']
                formatted_phone = formatted_pattern.loc[idx]
                
                if current_phone != formatted_phone:
                    self.stats['phones_normalized'] += 1
                    
                    # Update the phone number
                    df.loc[idx, 'phone'] = formatted_phone
                    
                    # Log the change
                    change_logs.append({
                        'row_number': idx + 2,
                        'column_number': df.columns.get_loc('phone') + 1,
                        'column_name': 'phone',
                        'field': 'phone',
                        'issue': 'Phone number format normalization',
                        'original_value': original_phones.iloc[idx],
                        'corrected_value': formatted_phone,
                        'correction': f"Normalized format: {original_phones.iloc[idx]} → {formatted_phone}",
                        'notes': 'Vectorized phone formatting'
                    })
        
        # Step 4: Flag invalid phone numbers
        invalid_phone_mask = (
            (df['phone_digits'].str.len() < 10) |
            (df['phone_digits'].str.len() > 11) |  
            (df['phone_digits'].str.len() == 11) & (~df['phone_digits'].str.startswith('1'))
        )
        
        if invalid_phone_mask.any():
            self.stats['phones_flagged'] += invalid_phone_mask.sum()
            
            # Log invalid phones
            for idx in df[invalid_phone_mask].index:
                change_logs.append({
                    'row_number': idx + 2,
                    'column_number': df.columns.get_loc('phone') + 1,
                    'column_name': 'phone',
                    'field': 'phone',
                    'issue': 'Invalid phone number length',
                    'original_value': original_phones.iloc[idx],
                    'corrected_value': df.iloc[idx]['phone'],  # No change for invalid
                    'correction': 'Unable to fix invalid phone number',
                    'notes': f'Invalid length: {df.iloc[idx]["phone_digits"]} digits'
                })
        
        # Clean up temporary column
        df.drop('phone_digits', axis=1, inplace=True)
        
        logger.debug(f"Phone processing: {self.stats['phones_normalized']} normalized, {self.stats['phones_flagged']} flagged")
        return change_logs
    
    def get_stats(self) -> Dict[str, Any]:
        """Return processing statistics."""
        return self.stats.copy()
    
    def reset_stats(self):
        """Reset processing statistics."""
        self.stats = {
            'emails_fixed': 0,
            'emails_flagged': 0, 
            'phones_normalized': 0,
            'phones_flagged': 0,
            'processing_time': 0
        }
