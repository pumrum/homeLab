#!/usr/bin/env python3
"""
Create a padded SRT file from a full subtitle file, keeping only the text
from lines that appear in a corresponding forced subtitle file.
All other lines are replaced with zero-width space characters.
"""

import re
import sys
import os
from typing import List, Tuple, Set
from datetime import timedelta


def parse_timecode(timecode: str) -> timedelta:
    """Parse SRT timecode string to timedelta object."""
    # Format: HH:MM:SS,mmm
    time_pattern = r'(\d{2}):(\d{2}):(\d{2}),(\d{3})'
    match = re.match(time_pattern, timecode)
    if not match:
        raise ValueError(f"Invalid timecode format: {timecode}")
    
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds, milliseconds=milliseconds)


def format_timecode(td: timedelta) -> str:
    """Format timedelta object as SRT timecode string."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = td.microseconds // 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def parse_srt(content: str) -> List[Tuple[timedelta, timedelta, str]]:
    """Parse SRT content and return list of (start_time, end_time, text) tuples."""
    subtitles = []
    blocks = content.strip().split('\n\n')
    
    for block in blocks:
        if not block.strip():
            continue
            
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        # Skip the subtitle number (first line)
        # Parse the timecode line (second line)
        timecode_line = lines[1]
        timecode_match = re.match(r'([\d:,]+)\s*-->\s*([\d:,]+)', timecode_line)
        
        if not timecode_match:
            continue
        
        start_time = parse_timecode(timecode_match.group(1))
        end_time = parse_timecode(timecode_match.group(2))
        
        # Join remaining lines as subtitle text
        text = '\n'.join(lines[2:])
        
        subtitles.append((start_time, end_time, text))
    
    return subtitles


def timecodes_match(tc1: Tuple[timedelta, timedelta], 
                    tc2: Tuple[timedelta, timedelta],
                    tolerance_ms: int = 50) -> bool:
    """Check if two timecode ranges match within a tolerance."""
    tolerance = timedelta(milliseconds=tolerance_ms)
    start_match = abs(tc1[0] - tc2[0]) <= tolerance
    end_match = abs(tc1[1] - tc2[1]) <= tolerance
    return start_match and end_match


def write_srt(subtitles: List[Tuple[timedelta, timedelta, str]], output_path: str):
    """Write subtitles to SRT file in UTF-8 without BOM."""
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        for i, (start_time, end_time, text) in enumerate(subtitles, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timecode(start_time)} --> {format_timecode(end_time)}\n")
            f.write(f"{text}\n\n")


def find_base_filename(input_path: str) -> Tuple[str, str]:
    """
    Extract base filename and determine the forced subtitle filename.
    Returns (base_name, forced_filename)
    """
    # Remove directory path
    basename = os.path.basename(input_path)
    
    # Match patterns like video.en.srt or video.en.sdh.srt
    if basename.endswith('.en.sdh.srt'):
        base_name = basename[:-len('.en.sdh.srt')]
        forced_filename = base_name + '.en.forced.srt'
    elif basename.endswith('.en.srt'):
        base_name = basename[:-len('.en.srt')]
        forced_filename = base_name + '.en.forced.srt'
    else:
        raise ValueError(f"Input file must end with .en.srt or .en.sdh.srt, got: {basename}")
    
    return base_name, forced_filename


def merge_subtitles(source_subs: List[Tuple[timedelta, timedelta, str]], 
                    forced_subs: List[Tuple[timedelta, timedelta, str]]) -> List[Tuple[timedelta, timedelta, str]]:
    """
    Merge forced subtitles into source subtitles.
    Returns combined list sorted by start time.
    """
    # Combine both lists
    merged = source_subs + forced_subs
    
    # Sort by start time
    merged.sort(key=lambda x: x[0])
    
    return merged


def main():
    if len(sys.argv) < 2:
        print("Usage: python create_padded_srt.py <video>.en.srt [merge]")
        print("   or: python create_padded_srt.py <video>.en.sdh.srt [merge]")
        print("\nCreates <video>.en.padded.srt with only forced subtitle text preserved.")
        print("Requires corresponding <video>.en.forced.srt file to exist.")
        print("\nOptions:")
        print("  merge   Merge forced subtitles into source file before padding")
        print("          (use when source file doesn't contain foreign language parts)")
        sys.exit(1)
    
    input_path = sys.argv[1]
    merge_mode = len(sys.argv) > 2 and sys.argv[2].lower() == 'merge'
    
    # Verify input file exists
    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)
    
    # Determine filenames
    try:
        base_name, forced_filename = find_base_filename(input_path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    # Build full paths
    input_dir = os.path.dirname(input_path) or '.'
    forced_path = os.path.join(input_dir, forced_filename)
    merged_path = os.path.join(input_dir, base_name + '.en.merged.srt')
    output_path = os.path.join(input_dir, base_name + '.en.padded.srt')
    
    # Verify forced subtitle file exists
    if not os.path.exists(forced_path):
        print(f"Error: Forced subtitle file not found: {forced_path}")
        sys.exit(1)
    
    print(f"Input file:  {input_path}")
    print(f"Forced file: {forced_path}")
    if merge_mode:
        print(f"Merged file: {merged_path}")
    print(f"Output file: {output_path}")
    print(f"Mode: {'MERGE' if merge_mode else 'STANDARD'}")
    print()
    
    # Read and parse both files
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            source_content = f.read()
        with open(forced_path, 'r', encoding='utf-8') as f:
            forced_content = f.read()
    except Exception as e:
        print(f"Error reading files: {e}")
        sys.exit(1)
    
    print("Parsing source subtitles...")
    source_subs = parse_srt(source_content)
    print(f"Found {len(source_subs)} subtitles in source file")
    
    print("Parsing forced subtitles...")
    forced_subs = parse_srt(forced_content)
    print(f"Found {len(forced_subs)} subtitles in forced file")
    print()
    
    if merge_mode:
        # Merge forced subtitles into source
        print("Merging forced subtitles into source file...")
        source_subs = merge_subtitles(source_subs, forced_subs)
        print(f"Merged file now has {len(source_subs)} subtitles")
        
        # Save the merged file
        print(f"Writing merged subtitles to {merged_path}...")
        write_srt(source_subs, merged_path)
        print("✓ Merged file saved")
        print()
    else:
        # Standard mode: verify all forced subtitles exist in source
        print("Verifying all forced subtitles exist in source file...")
        missing_forced = []
        
        for forced_start, forced_end, forced_text in forced_subs:
            # Look for matching timecode in source
            found = False
            for source_start, source_end, _ in source_subs:
                if timecodes_match((forced_start, forced_end), (source_start, source_end)):
                    found = True
                    break
            
            if not found:
                missing_forced.append((forced_start, forced_end, forced_text))
        
        if missing_forced:
            print(f"\nError: {len(missing_forced)} forced subtitle(s) not found in source file:")
            for start, end, text in missing_forced[:5]:  # Show first 5
                print(f"  {format_timecode(start)} --> {format_timecode(end)}")
                print(f"    {text[:50]}...")
            if len(missing_forced) > 5:
                print(f"  ... and {len(missing_forced) - 5} more")
            print("\nHint: Try running with 'merge' argument to merge forced subtitles into source first.")
            sys.exit(1)
        
        print("✓ All forced subtitles found in source file")
        print()
    
    # Create padded subtitles
    print("Creating padded subtitles...")
    padded_subs = []
    kept_count = 0
    replaced_count = 0
    
    for source_start, source_end, source_text in source_subs:
        # Check if this timecode matches any forced subtitle
        is_forced = False
        for forced_start, forced_end, _ in forced_subs:
            if timecodes_match((source_start, source_end), (forced_start, forced_end)):
                is_forced = True
                break
        
        if is_forced:
            # Keep the original text
            padded_subs.append((source_start, source_end, source_text))
            kept_count += 1
        else:
            # Replace with zero-width space
            padded_subs.append((source_start, source_end, "\u200B"))
            replaced_count += 1
    
    print(f"Kept {kept_count} forced subtitle(s)")
    print(f"Replaced {replaced_count} subtitle(s) with zero-width space")
    print()
    
    # Write output file
    print(f"Writing to {output_path}...")
    write_srt(padded_subs, output_path)
    
    print("✓ Done!")


if __name__ == "__main__":
    main()
