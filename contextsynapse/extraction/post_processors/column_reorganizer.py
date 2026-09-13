"""
Column Reorganizer Post-Processor

Fixes interleaved multi-column text by reorganizing it to read complete columns.
Detects when text alternates between columns (row-by-row) and reorganizes to
read each column completely (column-by-column).
"""

import re
import logging
from typing import List, Tuple, Dict

logger = logging.getLogger(__name__)


class ColumnReorganizer:
    """
    Reorganizes interleaved multi-column text to proper column-by-column reading order.
    """
    
    def __init__(self):
        """Initialize the column reorganizer."""
        pass
    
    def reorganize(self, text: str) -> str:
        """
        Reorganize text if it appears to be interleaved between columns.
        
        Handles two types of interleaving:
        1. Line-by-line: Lines alternate between columns (row-by-row extraction)
        2. Within-line: Single lines contain text from both columns (left-to-right reading)
        
        Args:
            text: Input text that may be interleaved
            
        Returns:
            Reorganized text with proper column-by-column reading order
        """
        if not text or len(text.strip()) < 50:
            return text
        
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        
        if len(lines) < 4:
            # Too few lines to reorganize
            return text
        
        # Check for within-line interleaving (lines containing text from both columns)
        # This happens when pdfplumber reads left-to-right across the page
        lines_with_both_columns = self._detect_within_line_interleaving(lines)
        
        if lines_with_both_columns:
            logger.info(f"Detected within-line interleaving ({lines_with_both_columns} lines), splitting lines first...")
            # Split lines that contain both columns
            lines = self._split_mixed_column_lines(lines)
        
        original_breaks = self._count_mid_breaks(lines)
        
        # If there are significant mid-sentence breaks, try reorganizing
        # This catches cases where extraction produced interleaved text
        if original_breaks > len(lines) * 0.1:  # More than 10% of lines have mid-breaks
            logger.info(f"Attempting reorganization ({len(lines)} lines, {original_breaks} mid-breaks)...")
            
            # Try reorganization
            reorganized = self._reorganize_columns(lines)
            reorganized_lines = reorganized.splitlines()
            reorganized_breaks = self._count_mid_breaks(reorganized_lines)
            
            # Use reorganized version if it's better
            if reorganized_breaks < original_breaks:
                improvement = original_breaks - reorganized_breaks
                logger.info(f"Reorganization improved: {original_breaks} -> {reorganized_breaks} mid-sentence breaks (reduced by {improvement})")
                return reorganized
            else:
                logger.debug(f"Reorganization didn't improve ({original_breaks} -> {reorganized_breaks} breaks), keeping original")
                return '\n'.join(lines)  # Return split lines even if reorganization didn't help
        else:
            # Few mid-breaks, likely not interleaved
            return '\n'.join(lines)
    
    def _detect_within_line_interleaving(self, lines: List[str]) -> int:
        """
        Detect lines that contain text from both columns.
        
        Indicators:
        - Very long lines (>100 chars) with mid-sentence breaks
        - Lines with multiple sentence fragments
        - Lines that don't form coherent sentences
        """
        count = 0
        for line in lines:
            # Long lines with no sentence-ending punctuation might contain both columns
            if len(line) > 100 and line[-1] not in '.!?;:':
                # Check if line has multiple sentence-like fragments
                # (indicated by capital letters mid-line after lowercase)
                has_fragments = False
                for i in range(1, len(line) - 1):
                    if (line[i-1].islower() and line[i] == ' ' and 
                        line[i+1].isupper() and i > 20):  # Capital after space, not at start
                        has_fragments = True
                        break
                if has_fragments:
                    count += 1
        return count
    
    def _split_mixed_column_lines(self, lines: List[str]) -> List[str]:
        """
        Split lines that contain text from both columns.
        
        The problem: pdfplumber.extract_text() reads left-to-right, so a single line
        can contain: "left column text" + "right column text"
        
        Strategy: Look for patterns indicating column boundaries:
        1. Mid-sentence end (no punctuation) followed by capital letter
        2. Long lines (>100 chars) with multiple sentence fragments
        3. Pattern: "...word " + "Capital word..." (column boundary)
        """
        split_lines = []
        
        for line in lines:
            if len(line) < 60:
                # Short line, likely single column
                split_lines.append(line)
                continue
            
            # Look for split points
            # Pattern 1: lowercase word, space, capital word (after 30% of line)
            # This suggests: "...left column text " + "Right column text..."
            best_split = None
            best_score = 0
            
            for i in range(int(len(line) * 0.3), len(line) - 20):
                # Check for pattern: word boundary with case change
                if i > 0 and i < len(line) - 1:
                    char_before = line[i-1] if i > 0 else ' '
                    char_at = line[i]
                    char_after = line[i+1] if i < len(line) - 1 else ' '
                    
                    # Pattern: space or lowercase, then space, then capital
                    if (char_before.islower() and char_at == ' ' and char_after.isupper()):
                        before = line[:i].strip()
                        after = line[i:].strip()
                        
                        # Score this split point
                        score = 0
                        
                        # Good if before doesn't end with punctuation (mid-sentence)
                        if before and before[-1] not in '.!?;:':
                            score += 2
                        
                        # Good if after starts with capital (new thought)
                        if after and after[0].isupper():
                            score += 2
                        
                        # Good if both parts are substantial
                        if len(before) > 30 and len(after) > 20:
                            score += 1
                        
                        # Prefer splits near middle (40-60% of line)
                        position_ratio = i / len(line)
                        if 0.4 <= position_ratio <= 0.6:
                            score += 1
                        
                        if score > best_score:
                            best_score = score
                            best_split = i
            
            if best_split and best_score >= 4:  # Require good score
                # Split the line
                part1 = line[:best_split].strip()
                part2 = line[best_split:].strip()
                split_lines.append(part1)
                split_lines.append(part2)
                logger.debug(f"Split line ({len(line)} chars) at position {best_split} (score: {best_score})")
            else:
                split_lines.append(line)
        
        return split_lines
    
    def _is_interleaved(self, lines: List[str]) -> bool:
        """
        Detect if text is interleaved between columns (row-by-row).
        
        Interleaved pattern:
        - Alternating between columns: col1_row1, col2_row1, col1_row2, col2_row2, ...
        - Many mid-sentence breaks
        - Lines that don't form complete thoughts
        """
        if len(lines) < 4:
            return False
        
        # Check for mid-sentence breaks (sentence continues on next line)
        # This is a strong indicator of interleaved columns
        mid_sentence_breaks = 0
        for i in range(len(lines) - 1):
            if (lines[i] and not lines[i][-1] in '.!?;:' and 
                len(lines[i]) > 10 and 
                lines[i+1] and lines[i+1][0].islower()):
                mid_sentence_breaks += 1
        
        mid_break_ratio = mid_sentence_breaks / len(lines) if lines else 0
        
        # Check for alternating pattern in even/odd lines
        # In interleaved text, even-indexed lines (col1) and odd-indexed lines (col2)
        # should have similar characteristics
        if len(lines) >= 4:
            even_lines = [lines[i] for i in range(0, len(lines), 2)]
            odd_lines = [lines[i] for i in range(1, len(lines), 2)]
            
            # Check if even and odd lines have similar average lengths
            # (both columns should have similar text density)
            if len(even_lines) > 0 and len(odd_lines) > 0:
                even_avg = sum(len(l) for l in even_lines) / len(even_lines)
                odd_avg = sum(len(l) for l in odd_lines) / len(odd_lines)
                
                # Similar average lengths suggest interleaving
                length_similarity = abs(even_avg - odd_avg) < 30
                
                # Check if both columns have some complete sentences
                even_complete = sum(1 for l in even_lines if l and l[-1] in '.!?;:')
                odd_complete = sum(1 for l in odd_lines if l and l[-1] in '.!?;:')
                
                # If lengths are similar and both have sentences, likely interleaved
                if length_similarity and even_complete > 0 and odd_complete > 0:
                    return True
        
        # Interleaved if:
        # 1. High ratio of mid-sentence breaks (>10%) - strong indicator
        # 2. OR both even/odd columns have similar characteristics (suggests interleaving)
        is_interleaved = mid_break_ratio > 0.10
        
        # Also check if even/odd pattern makes sense
        if not is_interleaved and len(lines) >= 4:
            even_lines = [lines[i] for i in range(0, len(lines), 2)]
            odd_lines = [lines[i] for i in range(1, len(lines), 2)]
            
            if len(even_lines) > 0 and len(odd_lines) > 0:
                even_avg = sum(len(l) for l in even_lines) / len(even_lines)
                odd_avg = sum(len(l) for l in odd_lines) / len(odd_lines)
                
                # Similar average lengths + both have sentences = likely interleaved
                if abs(even_avg - odd_avg) < 40:
                    even_complete = sum(1 for l in even_lines if l and l[-1] in '.!?;:')
                    odd_complete = sum(1 for l in odd_lines if l and l[-1] in '.!?;:')
                    if even_complete > 0 and odd_complete > 0:
                        is_interleaved = True
        
        return is_interleaved
    
    def _reorganize_columns(self, lines: List[str]) -> str:
        """
        Reorganize interleaved lines into proper column-by-column reading order.
        
        The problem: Text extracted row-by-row instead of column-by-column.
        Pattern: Line 0 (col1,row1), Line 1 (col2,row1), Line 2 (col1,row2), Line 3 (col2,row2)
        
        Solution: Try multiple split strategies and choose the one that produces
        the best reading order (fewest mid-sentence breaks).
        """
        if len(lines) < 4:
            return '\n'.join(lines)
        
        original_breaks = self._count_mid_breaks(lines)
        
        # Strategy 1: Even/odd split (most common for row-by-row extraction)
        even_lines = [lines[i] for i in range(0, len(lines), 2)]
        odd_lines = [lines[i] for i in range(1, len(lines), 2)]
        even_odd_breaks = self._count_mid_breaks(even_lines) + self._count_mid_breaks(odd_lines)
        even_odd_score = self._evaluate_split_quality(even_lines, odd_lines)
        
        # Strategy 2: Midpoint split
        mid_point = len(lines) // 2
        first_half = lines[:mid_point]
        second_half = lines[mid_point:]
        midpoint_breaks = self._count_mid_breaks(first_half) + self._count_mid_breaks(second_half)
        midpoint_score = self._evaluate_split_quality(first_half, second_half)
        
        # Strategy 3: Try different split points based on content
        best_split = None
        best_breaks = original_breaks
        best_score = 0
        
        # Try splits at various points (30%, 40%, 50%, 60%, 70%)
        for ratio in [0.3, 0.4, 0.5, 0.6, 0.7]:
            split_point = int(len(lines) * ratio)
            if split_point < 5 or split_point > len(lines) - 5:
                continue
            col1 = lines[:split_point]
            col2 = lines[split_point:]
            total_breaks = self._count_mid_breaks(col1) + self._count_mid_breaks(col2)
            score = self._evaluate_split_quality(col1, col2)
            
            if total_breaks < best_breaks or (total_breaks == best_breaks and score > best_score):
                best_split = (col1, col2)
                best_breaks = total_breaks
                best_score = score
        
        # Choose best strategy based on:
        # 1. Fewest total mid-breaks
        # 2. Highest quality score
        candidates = [
            (even_lines, odd_lines, even_odd_breaks, even_odd_score, "even/odd"),
            (first_half, second_half, midpoint_breaks, midpoint_score, "midpoint"),
        ]
        
        if best_split:
            candidates.append((best_split[0], best_split[1], best_breaks, best_score, "content-based"))
        
        # Sort by: fewer breaks first, then higher score
        candidates.sort(key=lambda x: (x[2], -x[3]))
        best_col1, best_col2, _, _, method = candidates[0]
        
        # Reorganize: column 1 complete, then column 2 complete
        reorganized = best_col1 + best_col2
        
        result = '\n'.join(reorganized)
        final_breaks = self._count_mid_breaks(best_col1) + self._count_mid_breaks(best_col2)
        logger.info(f"Reorganized {len(lines)} lines using {method}: {len(best_col1)} in col1, {len(best_col2)} in col2, breaks: {original_breaks} -> {final_breaks}")
        
        return result
        """
        Reorganize interleaved lines into proper column-by-column reading order.
        
        The problem: Text is extracted row-by-row instead of column-by-column:
        - Line 1 (col 1, row 1)
        - Line 2 (col 2, row 1)
        - Line 3 (col 1, row 2)
        - Line 4 (col 2, row 2)
        
        Should be:
        - All lines from col 1 (complete)
        - All lines from col 2 (complete)
        
        Strategy:
        1. Try even/odd split (most common interleaving pattern)
        2. Evaluate which split produces better reading order
        3. Use the best split
        """
        if len(lines) < 4:
            return '\n'.join(lines)
        
        # Method 1: Try even/odd split (assumes perfect alternation)
        even_lines = [lines[i] for i in range(0, len(lines), 2)]
        odd_lines = [lines[i] for i in range(1, len(lines), 2)]
        
        # Evaluate even/odd split quality
        even_odd_score = self._evaluate_split_quality(even_lines, odd_lines)
        
        # Method 2: Try midpoint split
        mid_point = len(lines) // 2
        first_half = lines[:mid_point]
        second_half = lines[mid_point:]
        midpoint_score = self._evaluate_split_quality(first_half, second_half)
        
        # Method 3: Try using column breaks
        column_breaks = self._detect_column_boundaries(lines)
        break_score = -1
        break_split = None
        if column_breaks:
            # Try first significant break
            split_point = column_breaks[0]
            col1 = lines[:split_point]
            col2 = lines[split_point:]
            break_score = self._evaluate_split_quality(col1, col2)
            break_split = (col1, col2)
        
        # Choose best split
        best_score = max(even_odd_score, midpoint_score, break_score)
        
        if best_score == even_odd_score and even_odd_score > 0:
            column1_lines = even_lines
            column2_lines = odd_lines
            method = "even/odd"
        elif best_score == break_score and break_split:
            column1_lines, column2_lines = break_split
            method = "column break"
        else:
            column1_lines = first_half
            column2_lines = second_half
            method = "midpoint"
        
        # Reorganize: column 1 complete, then column 2 complete
        reorganized = column1_lines + column2_lines
        
        # Join lines, preserving paragraph structure
        result = '\n'.join(reorganized)
        
        logger.info(f"Reorganized {len(lines)} lines using {method}: {len(column1_lines)} in column 1, {len(column2_lines)} in column 2 (score: {best_score:.2f})")
        
        return result
    
    def _evaluate_split_quality(self, col1_lines: List[str], col2_lines: List[str]) -> float:
        """
        Evaluate quality of a column split.
        
        Higher score = better split (fewer mid-sentence breaks, more complete sentences).
        """
        if not col1_lines or not col2_lines:
            return 0.0
        
        score = 0.0
        
        # Check both columns have reasonable length
        col1_avg = sum(len(l) for l in col1_lines) / len(col1_lines)
        col2_avg = sum(len(l) for l in col2_lines) / len(col2_lines)
        
        # Similar average lengths is good (both columns have similar content)
        length_diff = abs(col1_avg - col2_avg)
        if length_diff < 30:
            score += 0.3
        elif length_diff < 50:
            score += 0.1
        
        # Count complete sentences in each column
        col1_complete = sum(1 for l in col1_lines if l and l[-1] in '.!?;:')
        col2_complete = sum(1 for l in col2_lines if l and l[-1] in '.!?;:')
        
        # Both columns should have some complete sentences
        if col1_complete > 0 and col2_complete > 0:
            score += 0.3
        
        # Count mid-sentence breaks (fewer is better)
        col1_breaks = self._count_mid_breaks(col1_lines)
        col2_breaks = self._count_mid_breaks(col2_lines)
        
        total_breaks = col1_breaks + col2_breaks
        total_lines = len(col1_lines) + len(col2_lines)
        break_ratio = total_breaks / total_lines if total_lines > 0 else 1.0
        
        # Lower break ratio is better
        if break_ratio < 0.1:
            score += 0.4
        elif break_ratio < 0.2:
            score += 0.2
        elif break_ratio < 0.3:
            score += 0.1
        
        return score
    
    def _count_mid_breaks(self, lines: List[str]) -> int:
        """Count mid-sentence breaks in a list of lines."""
        breaks = 0
        for i in range(len(lines) - 1):
            if (lines[i] and not lines[i][-1] in '.!?;:' and 
                len(lines[i]) > 10 and 
                lines[i+1] and lines[i+1][0].islower()):
                breaks += 1
        return breaks
    
    def _detect_column_boundaries(self, lines: List[str]) -> List[int]:
        """
        Detect column boundaries in interleaved text.
        
        Returns list of line indices where columns change.
        """
        boundaries = []
        
        # Look for patterns that indicate column changes:
        # 1. Short line followed by line starting with capital (new sentence/paragraph)
        # 2. Line ending with punctuation followed by short line
        # 3. Significant change in line length
        
        for i in range(len(lines) - 1):
            current = lines[i]
            next_line = lines[i+1]
            
            # Pattern 1: Short line + capital start (potential column break)
            if len(current) < 25 and next_line and next_line[0].isupper():
                boundaries.append(i)
            
            # Pattern 2: Punctuation end + short line (potential column break)
            elif current and current[-1] in '.!?' and len(next_line) < 25:
                boundaries.append(i)
            
            # Pattern 3: Significant length change
            elif abs(len(current) - len(next_line)) > 40:
                boundaries.append(i)
        
        return boundaries




























