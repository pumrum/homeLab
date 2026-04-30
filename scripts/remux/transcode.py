"""
Video Transcoding Script with Google Sheets Integration
Reads transcoding parameters from Google Sheets and processes video files
Automatically downscales videos larger than 1080p while preserving aspect ratio
"""

import os
import sys
import json
import re
import subprocess
import signal
from pathlib import Path
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import time
import platform
import requests

# Import ctypes only on Windows
if platform.system() == 'Windows':
    import ctypes

# Configuration
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
CONFIG_FILE = 'config.json'  # Path to configuration file
CREDENTIALS_FILE = 'credentials.json'  # Path to your service account credentials

# TVDB IDs that should be downscaled to 720p instead of 1080p
# Edit this list to add or remove TVDB IDs as needed
TVDB_720P_IDS = [123456, 654321]

# Colors to use for text coloring
BLUE   = '\033[94m'
GREEN  = '\033[92m' #variables
PURPLE = '\033[95m'
RED    = '\033[91m' #errors
YELLOW = '\033[33m' #warnings
RESET  = '\033[0m'  #reset to white

# Separator line width for console output
SEPARATOR_WIDTH = 60


class InterruptHandler:
    """
    Handles CTRL+C interrupts with different behaviors based on transcoding state.
    
    When NOT transcoding:
        - Single CTRL+C: Exit gracefully
    
    When transcoding IS running:
        - Single CTRL+C: Finish current file, then exit gracefully
        - Double CTRL+C: Stop current transcode, delete partial output, exit gracefully
        - Triple CTRL+C: Terminate immediately
    """
    def __init__(self):
        self.interrupt_count = 0
        self.is_transcoding = False
        self.current_process = None
        self.current_output_file = None
        self.should_stop_after_current = False
        self.original_handler = None
        
    def install(self):
        """Install the signal handler"""
        self.original_handler = signal.signal(signal.SIGINT, self._handle_interrupt)
        
    def uninstall(self):
        """Restore the original signal handler"""
        if self.original_handler is not None:
            signal.signal(signal.SIGINT, self.original_handler)
    
    def start_transcode(self, process, output_file):
        """Called when transcoding starts"""
        self.is_transcoding = True
        self.current_process = process
        self.current_output_file = output_file
        self.interrupt_count = 0
        
    def end_transcode(self):
        """Called when transcoding ends"""
        self.is_transcoding = False
        self.current_process = None
        self.current_output_file = None
        # Don't reset interrupt_count here - we need it to know if we should stop
        
    def reset_interrupt_count(self):
        """Reset interrupt count (call after successfully handling an interrupt)"""
        self.interrupt_count = 0
        
    def should_exit(self):
        """Check if we should exit after the current operation"""
        return self.should_stop_after_current or self.interrupt_count > 0
    
    def _handle_interrupt(self, signum, frame):
        """Handle CTRL+C interrupt"""
        self.interrupt_count += 1
        
        if not self.is_transcoding:
            # Not transcoding - exit gracefully on first interrupt
            print(f"\n{YELLOW}Interrupt received. Exiting gracefully...{RESET}")
            self.should_stop_after_current = True
            raise KeyboardInterrupt
        
        # Currently transcoding - behavior depends on interrupt count
        if self.interrupt_count == 1:
            print(f"\n{YELLOW}{'='*SEPARATOR_WIDTH}")
            print(f"Interrupt received (1/3): Will finish current file then exit.")
            print(f"Press CTRL+C again to stop current transcode and delete partial output.")
            print(f"{'='*SEPARATOR_WIDTH}{RESET}")
            self.should_stop_after_current = True
            
        elif self.interrupt_count == 2:
            print(f"\n{YELLOW}{'='*SEPARATOR_WIDTH}")
            print(f"Interrupt received (2/3): Stopping current transcode...")
            print(f"Press CTRL+C again to terminate immediately.")
            print(f"{'='*SEPARATOR_WIDTH}{RESET}")
            self.should_stop_after_current = True
            
            # Kill the current ffmpeg process
            if self.current_process:
                try:
                    self.current_process.terminate()
                    # Give it a moment to terminate gracefully
                    try:
                        self.current_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.current_process.kill()
                except Exception as e:
                    print(f"{RED}Error terminating process: {e}{RESET}")
            
            # Delete partial output file
            if self.current_output_file and Path(self.current_output_file).exists():
                try:
                    Path(self.current_output_file).unlink()
                    print(f"{YELLOW}Deleted partial output file: {self.current_output_file}{RESET}")
                except Exception as e:
                    print(f"{RED}Error deleting partial file: {e}{RESET}")
                    
        else:  # 3 or more
            print(f"\n{RED}{'='*SEPARATOR_WIDTH}")
            print(f"Interrupt received (3/3): Terminating immediately!")
            print(f"{'='*SEPARATOR_WIDTH}{RESET}")
            
            # Force kill the process
            if self.current_process:
                try:
                    self.current_process.kill()
                except Exception:
                    pass
            
            # Exit immediately
            sys.exit(1)


# Global interrupt handler instance
interrupt_handler = InterruptHandler()

class TeeOutput:
    """
    Duplicate output to both console and log file
    Strip ANSI color codes from log file output
    """
    def __init__(self, log_file, original_stream):
        self.log_file = log_file
        self.original_stream = original_stream
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
    def write(self, message):
        # Write colored output to console
        self.original_stream.write(message)
        self.original_stream.flush()
        
        # Strip ANSI color codes before writing to log file
        clean_message = self.ansi_escape.sub('', message)
        self.log_file.write(clean_message)
        self.log_file.flush()
        
    def flush(self):
        self.original_stream.flush()
        self.log_file.flush()


def setup_logging(output_path):
    """
    Set up logging to both console and file
    
    Args:
        output_path: Path object for output directory
        
    Returns:
        Log file handle (to be closed later)
    """
    # Create output directory if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Create log filename with current date
    log_filename = datetime.now().strftime('%Y-%m-%d.log')
    log_path = output_path / log_filename
    
    # Open log file in append mode
    log_file = open(log_path, 'a', encoding='utf-8')
    
    # Write session header
    log_file.write(f"\n{'='*SEPARATOR_WIDTH}\n")
    log_file.write(f"Session started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"{'='*SEPARATOR_WIDTH}\n")
    log_file.flush()
    
    # Redirect stdout and stderr to both console and file
    sys.stdout = TeeOutput(log_file, sys.stdout)
    sys.stderr = TeeOutput(log_file, sys.stderr)
    
    return log_file


def beep_alert(count=3):
    """
    Play system beep alert (cross-platform)
    
    Args:
        count: Number of times to beep (default: 3)
    """
    system = platform.system()
    
    try:
        for i in range(count):
            if system == 'Windows':
                # Windows Beep API: Beep(frequency_in_hz, duration_in_ms)
                # 1000 Hz for 200ms creates a clear, audible tone
                ctypes.windll.kernel32.Beep(1000, 200)
            elif system == 'Darwin':  # macOS
                # Use afplay to play system beep sound
                subprocess.run(['afplay', '/System/Library/Sounds/Ping.aiff'], 
                             check=False, capture_output=True)
            else:  # Linux and others
                # Try to use paplay (PulseAudio) or aplay (ALSA)
                # Fallback to system beep if neither available
                try:
                    subprocess.run(['paplay', '/usr/share/sounds/freedesktop/stereo/complete.oga'],
                                 check=False, capture_output=True, timeout=1)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    # Fallback to terminal bell
                    print('\a', end='', flush=True)
            
            if i < count - 1:
                time.sleep(0.15)  # Short pause between beeps (3 beeps in ~1 second)
                
    except Exception as e:
        # Final fallback to system beep
        print(f"\n{YELLOW}Note: Could not play beep sound ({e}), using terminal bell{RESET}")
        for i in range(count):
            print('\a', end='', flush=True)
            if i < count - 1:
                time.sleep(0.3)


def send_webhook_notification(webhook_url, message):
    """
    Send a notification to a webhook endpoint
    
    Args:
        webhook_url: The webhook URL to send the notification to
        message: The message to include in the JSON payload
    """
    try:
        payload = {"message": message}
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        print(f"{GREEN}Webhook notification sent successfully.{RESET}")
    except requests.exceptions.RequestException as e:
        print(f"{YELLOW}Warning: Failed to send webhook notification: {e}{RESET}")


class TranscodeConfig:
    """Container for transcoding configuration from a sheet row"""
    def __init__(self, row_data, col_map):
        # col_map is a dict of {column_name: index} built from the header row.
        # Using it here means adding/removing unrelated columns in the sheet
        # never requires changes to this script.
        def get(col_name):
            idx = col_map.get(col_name)
            if idx is None:
                return ''
            return row_data[idx] if idx < len(row_data) else ''

        self.title         = get('Title')
        self.year          = get('Year')
        self.tmdb_id       = get('TMDB')
        self.input_file    = get('Source File')
        self.edition       = get('Edition')
        self.pressing      = get('Pressing')
        self.collection    = get('Collection')
        self.video_title   = get('Video')
        self.video_quality = get('Quality')
        self.audio_0       = get('Audio 0')
        self.audio_1       = get('Audio 1')
        self.sub_enforced  = get('Forced')
        self.sub_en        = get('English')
        self.sub_ensdh     = get('English SDH')
        self.todo          = get('To Do')

        # Store raw Complete value and parsed boolean
        self.raw_complete = get('Complete')
        self.complete = self._parse_boolean(self.raw_complete)
    
    @staticmethod
    def _parse_boolean(value):
        """
        Parse boolean values from Google Sheets
        
        Args:
            value: Value from sheet
            
        Returns:
            Boolean value, or None if invalid
        """
        if value is True or value is False:
            return value
        if isinstance(value, str):
            value_upper = value.strip().upper()
            if value_upper == 'TRUE':
                return True
            elif value_upper == 'FALSE':
                return False
        return None
    
    def _is_raw_complete_false(self):
        """
        Check if the raw Complete value is exactly "FALSE" (case-insensitive)
        
        Returns:
            True if raw value is "FALSE", False otherwise
        """
        if self.raw_complete is False:
            return True
        if isinstance(self.raw_complete, str):
            return self.raw_complete.strip().upper() == 'FALSE'
        return False
    
    def is_valid(self):
        """
        Check if this row has valid data (all fields are populated)
        
        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        # If row should be skipped (Complete is not FALSE), skip all other validation
        # (it will be skipped later anyway, no need to validate incomplete fields)
        if not self._is_raw_complete_false():
            return (True, "")
        
        # Check that all string fields are not empty
        string_fields = [
            self.title, self.year, self.tmdb_id, self.input_file,
            self.edition, self.pressing, self.collection, self.video_title,
            self.video_quality, self.audio_0, self.audio_1,
            self.sub_enforced, self.sub_en, self.sub_ensdh, self.todo
        ]
        
        for field in string_fields:
            if not field or not field.strip():
                return (False, "blank field(s) detected")
        
        # Check that video_quality is one of the allowed values
        valid_qualities = ['Copy', 'Best', 'Standard', 'Lesser', 'Dreck']
        if self.video_quality not in valid_qualities:
            return (False, f"video quality must be one of: {', '.join(valid_qualities)}")
        
        return (True, "")
    
    def should_skip_complete(self):
        """
        Check if this row should be skipped because Complete is not FALSE
        
        Returns:
            Boolean indicating if the row should be skipped (Complete is not FALSE)
        """
        return not self._is_raw_complete_false()
    
    def is_complete(self):
        """
        Check if this row is marked as complete (for backwards compatibility)
        
        Returns:
            Boolean indicating if the row is complete
        """
        return self.complete is True
        
    def __repr__(self):
        return (f"TranscodeConfig(title='{self.title}', "
                f"year='{self.year}', "
                f"input_file='{self.input_file}', "
                f"complete={self.complete})")


class TVTranscodeConfig:
    """Container for TV show transcoding configuration from a sheet row"""
    def __init__(self, row_data, col_map):
        # col_map is a dict of {column_name: index} built from the header row.
        # Using it here means adding/removing unrelated columns in the sheet
        # (e.g. introStart/introEnd/recapStart/recapEnd/creditsStart/creditsEnd
        # or any future columns) never requires changes to this script.
        def get(col_name):
            idx = col_map.get(col_name)
            if idx is None:
                return ''
            return row_data[idx] if idx < len(row_data) else ''

        self.title         = get('Show')
        self.tmdb_id       = get('TVDB')
        self.season        = get('Season')
        self.episode       = get('Episode')
        self.episode_title = get('Title')
        self.input_file    = get('Source File')
        self.video_title   = get('Video')   # source description (e.g. "1080p BluRay")
        self.video_quality = get('Quality') # encode params (e.g. "Best")
        self.audio_0       = get('Audio 0')
        self.audio_1       = get('Audio 1')
        self.sub_enforced  = get('Forced')
        self.sub_en        = get('English')
        self.sub_ensdh     = get('English SDH')
        self.todo          = get('To Do')

        # Store raw Complete value and parsed boolean
        self.raw_complete = get('Complete')
        self.complete = self._parse_boolean(self.raw_complete)

        # For compatibility with shared functions
        self.content_type = 'tv'
        self.year      = ''    # TV shows don't have year in sheet
        self.pressing  = 'N/A'
        self.collection = ''
        self.edition   = ''
    @staticmethod
    def _parse_boolean(value):
        """Parse boolean values from Google Sheets"""
        if value is True or value is False:
            return value
        if isinstance(value, str):
            value_upper = value.strip().upper()
            if value_upper == 'TRUE':
                return True
            elif value_upper == 'FALSE':
                return False
        return None
    
    def _is_raw_complete_false(self):
        """
        Check if the raw Complete value is exactly "FALSE" (case-insensitive)
        
        Returns:
            True if raw value is "FALSE", False otherwise
        """
        if self.raw_complete is False:
            return True
        if isinstance(self.raw_complete, str):
            return self.raw_complete.strip().upper() == 'FALSE'
        return False
    
    def is_valid(self):
        """Check if this row has valid data"""
        # If row should be skipped (Complete is not FALSE), skip all other validation
        # (it will be skipped later anyway, no need to validate incomplete fields)
        if not self._is_raw_complete_false():
            return (True, "")
        
        string_fields = [
            self.title, self.season, self.episode, self.episode_title,
            self.tmdb_id, self.input_file, self.video_title,
            self.video_quality, self.audio_0, self.audio_1,
            self.sub_enforced, self.sub_en, self.sub_ensdh, self.todo
        ]
        
        for field in string_fields:
            if not field or not field.strip():
                return (False, "blank field(s) detected")
        
        valid_qualities = ['Copy', 'Best', 'Standard', 'Lesser', 'Dreck']
        if self.video_quality not in valid_qualities:
            return (False, f"video quality must be one of: {', '.join(valid_qualities)}")
        
        return (True, "")
    
    def should_skip_complete(self):
        """
        Check if this row should be skipped because Complete is not FALSE
        
        Returns:
            Boolean indicating if the row should be skipped (Complete is not FALSE)
        """
        return not self._is_raw_complete_false()
    
    def is_complete(self):
        """Check if this row is marked as complete (for backwards compatibility)"""
        return self.complete is True
    
    def get_display_name(self):
        """Get formatted display name for TV episode"""
        return f"{self.title} - S{self.season.zfill(2)}E{self.episode.zfill(2)} - {self.episode_title}"
    
    def __repr__(self):
        return (f"TVTranscodeConfig(title='{self.title}', "
                f"season='{self.season}', episode='{self.episode}', "
                f"input_file='{self.input_file}', "
                f"complete={self.complete})")

def load_config():
    """
    Load configuration from config.json file
    Supports both movie and TV processing with separate sheet names and working paths
    
    Returns:
        Dictionary containing configuration settings with Path objects
    """
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        
        # Validate required fields for movie/TV dual mode
        required_fields = [
            'spreadsheet_id', 'sheet_name_movie', 'sheet_name_tv', 'sheet_mode', 
            'execution_mode', 'ffmpeg_path', 'ffprobe_path', 'mediainfo_path', 
            'working_path_movie', 'working_path_tv', 'working_path_legacy', 'output_path'
        ]
        missing_fields = [field for field in required_fields if field not in config]
        
        if missing_fields:
            print(f"{RED}Error: Missing required fields in {CONFIG_FILE}: {', '.join(missing_fields)}{RESET}")
            sys.exit(1)
        
        # Validate sheet_mode
        if config['sheet_mode'] not in ['Online', 'Offline']:
            print(f"{RED}Error: sheet_mode must be 'Online' or 'Offline', got '{config['sheet_mode']}'{RESET}")
            sys.exit(1)
        
        # Validate execution_mode
        if config['execution_mode'] not in ['Enabled', 'Disabled']:
            print(f"{RED}Error: execution_mode must be 'Enabled' or 'Disabled', got '{config['execution_mode']}'{RESET}")
            sys.exit(1)
        
        # Convert path strings to Path objects
        config['ffmpeg_path'] = Path(config['ffmpeg_path'])
        config['ffprobe_path'] = Path(config['ffprobe_path'])
        config['mediainfo_path'] = Path(config['mediainfo_path'])
        config['working_path_movie'] = Path(config['working_path_movie'])
        config['working_path_tv'] = Path(config['working_path_tv'])
        config['working_path_legacy'] = Path(config['working_path_legacy'])
        config['output_path'] = Path(config['output_path'])
        
        return config
        
    except FileNotFoundError:
        print(f"{RED}Error: Configuration file '{CONFIG_FILE}' not found.{RESET}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"{RED}Error: Invalid JSON in {CONFIG_FILE}: {e}{RESET}")
        sys.exit(1)



def get_google_sheets_service():
    """
    Authenticate and return Google Sheets service object
    
    Returns:
        Google Sheets API service object
    """
    try:
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        return service
    except FileNotFoundError:
        print(f"{RED}Error: Credentials file '{CREDENTIALS_FILE}' not found.")
        print(f"\nDetailed setup instructions available at:")
        print(f"https://cloud.google.com/docs/authentication/getting-started{RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{RED}Error authenticating with Google Sheets: {e}{RESET}")
        sys.exit(1)


def read_sheet_data(service, spreadsheet_id, sheet_name):
    """
    Read all data from the Google Sheet
    
    Args:
        service: Google Sheets API service object
        spreadsheet_id: ID of the Google Spreadsheet
        sheet_name: Name of the sheet tab to read
        
    Returns:
        Tuple of (header_row, data_rows) where header_row is a list of column
        name strings and data_rows is the list of data rows from the sheet.
        Returns ([], []) on error or empty sheet.
    """
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=f'{sheet_name}!A:Z'  # Adjust range as needed
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            print(f"{RED}No data found in sheet.{RESET}")
            return [], []
        
        # First row is the header; remaining rows are data
        header = values[0]
        data_rows = values[1:] if len(values) > 1 else []
        return header, data_rows
        
    except HttpError as error:
        print(f"An error occurred: {error}")
        return [], []


def save_sheet_data(sheet_rows, output_path):
    """
    Save sheet data to movie.json file
    
    Args:
        sheet_rows: List of rows from the sheet
        output_path: Path object for output directory
    """
    json_path = output_path / 'movie.json'
    
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(sheet_rows, f, indent=2)
        print(f"{PURPLE}Saved sheet data to {GREEN}{json_path}{RESET}")
    except Exception as e:
        print(f"{RED}Error saving sheet data: {e}{RESET}")


def load_sheet_data(output_path):
    """
    Load sheet data from movie.json file
    
    Args:
        output_path: Path object for output directory
        
    Returns:
        List of rows from the saved file
    """
    json_path = output_path / 'movie.json'
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            sheet_rows = json.load(f)
        print(f"{PURPLE}Loaded sheet data from {GREEN}{json_path}{RESET}")
        return sheet_rows
    except FileNotFoundError:
        print(f"{RED}Error: movie.json not found at {json_path}")
        print(f"Run in Online mode first to download the sheet data.{RESET}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"{RED}Error: Invalid JSON in movie.json: {e}{RESET}")
        sys.exit(1)


def get_mkv_files(working_path):
    """
    Get all video files in the working folder
    
    Args:
        working_path: Path object for folder containing video files
        
    Returns:
        List of Path objects for video files
    """
    if not working_path.exists():
        print(f"{RED}Warning: Working folder '{working_path}' does not exist.{RESET}")
        return []
    
    # Support multiple video formats
    video_files = []
    extensions = ['*.mkv', '*.MKV', '*.mp4', '*.MP4', '*.avi', '*.AVI', '*.mov', '*.MOV', '*.ts']
    for ext in extensions:
        video_files.extend(working_path.glob(ext))
    
    return video_files



def detect_dolby_vision(file_path, ffprobe_path):
    """
    Detect if video contains Dolby Vision by checking for DV metadata
    
    Args:
        file_path: Path to video file
        ffprobe_path: Path to ffprobe executable
        
    Returns:
        Boolean indicating if Dolby Vision is present
    """
    try:
        # Check for Dolby Vision side data
        cmd = [
            str(ffprobe_path),
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream_side_data',
            '-of', 'json',
            str(file_path)
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                # Check for Dolby Vision side data
                if 'streams' in data and len(data['streams']) > 0:
                    stream = data['streams'][0]
                    if 'side_data_list' in stream:
                        for side_data in stream['side_data_list']:
                            if 'DOVI configuration record' in str(side_data) or                                ('side_data_type' in side_data and 'dovi' in side_data['side_data_type'].lower()):
                                return True
            except json.JSONDecodeError:
                pass
        
        # Fallback: check codec_tag_string for dvhe/dvh1
        cmd_codec = [
            str(ffprobe_path),
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_tag_string',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(file_path)
        ]
        
        result = subprocess.run(
            cmd_codec,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0:
            codec_tag = result.stdout.strip().lower()
            if 'dvh' in codec_tag or 'dvhe' in codec_tag:
                return True
                
    except Exception as e:
        print(f"{YELLOW}Warning: Could not detect Dolby Vision: {e}{RESET}")
    
    return False


def get_video_info(file_path, ffprobe_path):
    """
    Get the video resolution and bit depth from the video file
    
    Args:
        file_path: Path to the video file
        ffprobe_path: Path to ffprobe executable
        
    Returns:
        Dictionary with 'width', 'height', and 'bit_depth', or None if error
    """
    try:
        result = subprocess.run(
            [str(ffprobe_path), '-v', 'quiet',
             '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height,pix_fmt',
             '-of', 'json', str(file_path)],
            capture_output=True, text=True, check=True
        )
        
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        
        if streams and len(streams) > 0:
            width = streams[0].get('width')
            height = streams[0].get('height')
            pix_fmt = streams[0].get('pix_fmt', '')
            
            # Detect if it's 10-bit based on pixel format
            # Common 10-bit formats include: yuv420p10le, yuv422p10le, yuv444p10le, p010le, etc.
            is_10bit = '10' in pix_fmt or 'p010' in pix_fmt.lower()
            
            if width and height:
                return {
                    'width': int(width),
                    'height': int(height),
                    'pix_fmt': pix_fmt,
                    'is_10bit': is_10bit
                }
        
        return None
        
    except subprocess.CalledProcessError as e:
        print(f"{RED}Error getting video info: {e}{RESET}")
        return None
    except Exception as e:
        print(f"{RED}Error parsing video info: {e}{RESET}")
        return None


def _normalize_tokens(layout_str):
    """Return a set of canonical FFmpeg-ish channel tokens from a MediaInfo ChannelLayout string."""
    # Split on whitespace/commas and normalize case
    toks = re.split(r"[,\s]+", layout_str.strip())
    norm = []
    for t in toks:
        u = t.upper()
        # Canonicalize common aliases to FFmpeg-style names
        if u in {"L", "FL"}:           norm.append("FL")
        elif u in {"R", "FR"}:         norm.append("FR")
        elif u in {"C", "FC", "M"}:    norm.append("FC")  # M is MediaInfo's mono identifier
        elif u in {"LFE"}:             norm.append("LFE")
        elif u in {"LS", "SL"}:        norm.append("SL")
        elif u in {"LSS", "SSL"}:      norm.append("SL")
        elif u in {"RS", "SR"}:        norm.append("SR")
        elif u in {"RSS", "SSR"}:      norm.append("SR")
        elif u in {"LB", "BL"}:        norm.append("BL")
        elif u in {"RB", "BR"}:        norm.append("BR")
        elif u in {"LW", "WL"}:        norm.append("WL")
        elif u in {"RW", "WR"}:        norm.append("WR")
        else:
            # keep unknowns as-is (rare formats, heights, etc.)
            norm.append(u)
    return set(norm)


def get_audio_channel_layout(file_path, audio_track, mediainfo_path, channel_count=None):
    """
    Return the *raw* MediaInfo ChannelLayout string for the requested audio track.
    More robust than parsing the full output; we read all audio ChannelLayout lines and index.
    Falls back to ChannelLayout_Original for DTS-HD MA and similar formats, or when the
    detected layout doesn't match the expected channel count.
    
    Args:
        file_path: Path to the video file
        audio_track: Zero-based audio track index
        mediainfo_path: Path to MediaInfo executable
        channel_count: Optional expected channel count to validate against
        
    Returns:
        String containing the channel layout (e.g., "L R C LFE Ls Rs")
    """
    try:
        # First try standard ChannelLayout
        result = subprocess.run(
            [str(mediainfo_path), '--Inform=Audio;%ChannelLayout%\\n', str(file_path)],
            capture_output=True, text=True, check=True
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        
        layout = lines[audio_track] if 0 <= audio_track < len(lines) else ''
        
        # If we got a layout, check if it matches the channel count
        # Some formats like DTS-HD MA report incorrect ChannelLayout but correct ChannelLayout_Original
        if layout and channel_count is not None:
            # Count tokens in the layout string to estimate channel count
            layout_tokens = len(re.split(r'[,\s]+', layout.strip()))
            if layout_tokens != channel_count:
                # Layout doesn't match channel count, try ChannelLayout_Original
                print(f"{YELLOW}Warning: ChannelLayout '{layout}' has {layout_tokens} channels but expected {channel_count}, trying ChannelLayout_Original{RESET}")
                result = subprocess.run(
                    [str(mediainfo_path), '--Inform=Audio;%ChannelLayout_Original%\\n', str(file_path)],
                    capture_output=True, text=True, check=True
                )
                lines_original = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
                if 0 <= audio_track < len(lines_original) and lines_original[audio_track]:
                    return lines_original[audio_track]
                # If ChannelLayout_Original is also empty/wrong, fall back to original layout
                return layout
        
        # If we got a valid layout, return it
        if layout:
            return layout
        
        # If ChannelLayout is empty, try ChannelLayout_Original (for DTS-HD MA, etc.)
        result = subprocess.run(
            [str(mediainfo_path), '--Inform=Audio;%ChannelLayout_Original%\\n', str(file_path)],
            capture_output=True, text=True, check=True
        )
        lines_original = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return lines_original[audio_track] if 0 <= audio_track < len(lines_original) else ''
        
    except subprocess.CalledProcessError as e:
        print(f"{RED}Error getting audio info for {file_path}: {e}{RESET}")
        return ''
    except FileNotFoundError:
        print(f"{RED}MediaInfo not found at {mediainfo_path}{RESET}")
        return ''


def get_audio_channel_count(file_path, audio_track, mediainfo_path):
    """
    Get the raw channel count for the requested audio track.
    
    Args:
        file_path: Path to the video file
        audio_track: Zero-based audio track index
        mediainfo_path: Path to MediaInfo executable
        
    Returns:
        Integer containing the channel count (e.g., 2, 6, 8)
    """
    try:
        result = subprocess.run(
            [str(mediainfo_path), '--Inform=Audio;%Channel(s)%\\n', str(file_path)],
            capture_output=True, text=True, check=True
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if 0 <= audio_track < len(lines):
            try:
                return int(lines[audio_track])
            except ValueError:
                return 0
        return 0
    except subprocess.CalledProcessError as e:
        print(f"{RED}Error getting channel count for {file_path}: {e}{RESET}")
        return 0
    except FileNotFoundError:
        print(f"{RED}MediaInfo not found at {mediainfo_path}{RESET}")
        return 0


def get_audio_codec(file_path, audio_track, mediainfo_path):
    """
    Get the audio codec for the requested audio track.
    
    Args:
        file_path: Path to the video file
        audio_track: Zero-based audio track index
        mediainfo_path: Path to MediaInfo executable
        
    Returns:
        String containing the codec name (e.g., "AC-3", "E-AC-3", "DTS", "AAC")
    """
    try:
        result = subprocess.run(
            [str(mediainfo_path), '--Inform=Audio;%Format%\\n', str(file_path)],
            capture_output=True, text=True, check=True
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return lines[audio_track] if 0 <= audio_track < len(lines) else ''
    except subprocess.CalledProcessError as e:
        print(f"{RED}Error getting audio codec for {file_path}: {e}{RESET}")
        return ''
    except FileNotFoundError:
        print(f"{RED}MediaInfo not found at {mediainfo_path}{RESET}")
        return ''


def determine_audio_settings(channel_layout):
    """
    Determine audio settings based on ChannelLayout (from MediaInfo).
    
    Args:
        channel_layout: String containing channel layout (e.g., "L R C LFE Ls Rs")
        
    Returns:
        Dictionary with 'audio_layout' description, 'filter' for ffmpeg, 'channels' count,
        'ffmpeg_channel_layout' for re-encoding multichannel, and 'bitrate' for encoding
    """
    normalize = 'dynaudnorm=f=150:g=31'
    if not channel_layout:
        print(f"{RED}Unknown channel layout (empty).{RESET}")
        return None  # Return None to signal empty/invalid layout

    chans = _normalize_tokens(channel_layout)
    # quick counts
    count_map = {
        "FL","FR","FC","LFE","SL","SR","BL","BR","WL","WR"
    }
    channel_count = sum(1 for c in chans if c in count_map)
    has_center = "FC" in chans
    has_lfe = "LFE" in chans
    has_side = {"SL","SR"}.issubset(chans)
    has_back = {"BL","BR"}.issubset(chans)
    has_wide = {"WL","WR"}.issubset(chans)

    # Stereo
    if {"FL","FR"}.issubset(chans) and channel_count == 2:
        return {
            "audio_layout": "Stereo",
            "filter": "anull",
            "channels": 2,
            "ffmpeg_channel_layout": "stereo",
            "bitrate": "256k"
        }

    # 5.1 side/back
    if channel_count == 6 and has_center and has_lfe:
        if has_side and not has_back:
            return {
                "audio_layout": "Surround 5.1 (side)",
                "filter": f"pan=stereo|FL=1.0*FL+0.707*FC+0.707*SL|FR=1.0*FR+0.707*FC+0.707*SR,{normalize}",
                "channels": 6,
                "ffmpeg_channel_layout": "5.1",
                "bitrate": "512k"
            }
        if has_back and not has_side:
            return {
                "audio_layout": "Surround 5.1 (back)",
                "filter": f"pan=stereo|FL=1.0*FL+0.707*FC+0.5*BL|FR=1.0*FR+0.707*FC+0.5*BR,{normalize}",
                "channels": 6,
                "ffmpeg_channel_layout": "5.1",
                "bitrate": "512k"
            }

    # 7.1 variants (8 channels)
    if channel_count == 8 and has_center and has_lfe:
        # 7.1(back): SL/SR + BL/BR (no WL/WR)
        if has_side and has_back and not has_wide:
            return {
                "audio_layout": "Surround 7.1 (back)",
                "filter": f"pan=stereo|FL=1.0*FL+0.707*FC+0.5*SL+0.5*BL|FR=1.0*FR+0.707*FC+0.5*SR+0.5*BR,{normalize}",
                "channels": 8,
                "ffmpeg_channel_layout": "7.1",
                "bitrate": "640k"
            }
        # 7.1(wide): SL/SR + WL/WR (no BL/BR)
        if has_side and has_wide and not has_back:
            return {
                "audio_layout": "Surround 7.1 (wide)",
                "filter": ("pan=stereo|"
                           "c0=1.0*FL+0.707*FC+0.5*SL+0.5*WL|"
                           "c1=1.0*FR+0.707*FC+0.5*SR+0.5*WR," + normalize),
                "channels": 8,
                "ffmpeg_channel_layout": "7.1(wide)",
                "bitrate": "640k"
            }
        # If both back and wide were somehow present (nonstandard), prefer back
        if has_side and has_back and has_wide:
            return {
                "audio_layout": "Surround 7.1 (ambiguous: back+wide)",
                "filter": ("pan=stereo|"
                           "c0=1.0*FL+0.707*FC+0.5*SL+0.5*BL|"
                           "c1=1.0*FR+0.707*FC+0.5*SR+0.5*BR," + normalize),
                "channels": 8,
                "ffmpeg_channel_layout": "7.1",
                "bitrate": "640k"
            }

    # 5.0 / 4.0 / 3.0 / 1.0 fallbacks
    if channel_count == 5 and has_center and not has_lfe and has_side:
        return {
            "audio_layout": "Surround 5.0 (side)",
            "filter": f"pan=stereo|FL=0.8*FL+0.6*FC+0.6*SL|FR=0.8*FR+0.6*FC+0.6*SR,{normalize}",
            "channels": 5,
            "ffmpeg_channel_layout": "5.0(side)",
            "bitrate": "384k"
        }
    if channel_count == 5 and has_center and not has_lfe and has_back:
        return {
            "audio_layout": "Surround 5.0 (back)",
            "filter": f"pan=stereo|FL=0.8*FL+0.6*FC+0.6*BL|FR=0.8*FR+0.6*FC+0.6*BR,{normalize}",
            "channels": 5,
            "ffmpeg_channel_layout": "5.0",
            "bitrate": "384k"
        }
    if channel_count == 4 and has_side and not has_back:
        return {
            "audio_layout": "Surround 4.0 (side)",
            "filter": f"pan=stereo|FL=0.9*FL+0.5*SL|FR=0.9*FR+0.5*SR,{normalize}",
            "channels": 4,
            "ffmpeg_channel_layout": "quad(side)",
            "bitrate": "320k"
        }
    if channel_count == 4 and has_back and not has_side:
        return {
            "audio_layout": "Surround 4.0 (back)",
            "filter": f"pan=stereo|FL=0.9*FL+0.5*BL|FR=0.9*FR+0.5*BR,{normalize}",
            "channels": 4,
            "ffmpeg_channel_layout": "quad",
            "bitrate": "320k"
        }
    if channel_count == 3 and has_center:
        return {
            "audio_layout": "Surround 3.0",
            "filter": f"pan=stereo|FL=0.8*FL+0.6*FC|FR=0.8*FR+0.6*FC,{normalize}",
            "channels": 3,
            "ffmpeg_channel_layout": "3.0",
            "bitrate": "256k"
        }
    if channel_count == 1:
        if "FC" in chans:
            return {"audio_layout": "Mono", "filter": f"pan=stereo|FL=0.5*FC|FR=0.5*FC", "channels": 1, "ffmpeg_channel_layout": "mono", "bitrate": "128k"}
        if "FL" in chans:
            return {"audio_layout": "Mono", "filter": f"pan=stereo|FL=FL|FR=FL", "channels": 1, "ffmpeg_channel_layout": "mono", "bitrate": "128k"}
        if "FR" in chans:
            return {"audio_layout": "Mono", "filter": f"pan=stereo|FL=FR|FR=FR", "channels": 1, "ffmpeg_channel_layout": "mono", "bitrate": "128k"}

    print(f"{RED}Unknown channel layout: {channel_layout}. Unable to determine appropriate stereo filter.{RESET}")
    return None  # Return None to signal unknown layout


def get_video_encoding_params(quality, target_height=1080):
    """
    Get video encoding parameters based on quality setting and target resolution
    
    Args:
        quality: Quality setting string from config (must be exact match)
        target_height: Target resolution height (1080 or 720). Default is 1080.
                      When 720, bitrates are scaled to 50% of 1080p values.
        
    Returns:
        List of ffmpeg parameters for video encoding
    """
    # Base bitrates are defined for 1080p
    # For 720p, all bitrates are scaled to 50% (720p has ~44% of pixels, 50% is a balanced approach)
    bitrate_scale = 0.5 if target_height == 720 else 1.0
    
    if quality == 'Copy':
        return ['-c:v', 'copy']
    elif quality == 'Best':
        return [
            '-c:v', 'h264_nvenc',
            '-a53cc', '0',
            '-preset', 'p7',
            '-rc', 'vbr',
            '-tune', 'hq',
            '-b:v', f'{int(6500 * bitrate_scale)}k',
            '-maxrate', f'{int(7000 * bitrate_scale)}k',
            '-bufsize', f'{int(14000 * bitrate_scale)}k'
        ]
    elif quality == 'Standard':
        return [
            '-c:v', 'h264_nvenc',
            '-a53cc', '0',
            '-preset', 'p7',
            '-rc', 'vbr',
            '-tune', 'hq',
            '-b:v', f'{int(5000 * bitrate_scale)}k',
            '-maxrate', f'{int(5200 * bitrate_scale)}k',
            '-bufsize', f'{int(10000 * bitrate_scale)}k'
        ]
    elif quality == 'Lesser':
        return [
            '-c:v', 'h264_nvenc',
            '-a53cc', '0',
            '-preset', 'p7',
            '-rc', 'vbr',
            '-tune', 'hq',
            '-b:v', f'{int(3000 * bitrate_scale)}k',
            '-maxrate', f'{int(3200 * bitrate_scale)}k',
            '-bufsize', f'{int(6000 * bitrate_scale)}k'
        ]
    elif quality == 'Dreck':
        return [
            '-c:v', 'h264_nvenc',
            '-a53cc', '0',
            '-preset', 'p7',
            '-rc', 'vbr',
            '-tune', 'hq',
            '-b:v', f'{int(2300 * bitrate_scale)}k',
            '-maxrate', f'{int(2400 * bitrate_scale)}k',
            '-bufsize', f'{int(4800 * bitrate_scale)}k'
        ]
    else:
        # This should never happen due to validation in is_valid()
        raise ValueError(f"Invalid quality setting: {quality}")


def get_source_metadata(video_path, ffprobe_path):
    """
    Extract metadata from source video file
    
    Args:
        video_path: Path to the video file
        ffprobe_path: Path to ffprobe executable
        
    Returns:
        Dictionary containing creation_time, encoder, and modified timestamp
    """
    try:
        # Get file modified time and format it
        modified_ts = video_path.stat().st_mtime
        modified_iso = datetime.fromtimestamp(modified_ts).isoformat()
        
        # Get encoder information from metadata
        result = subprocess.run(
            [str(ffprobe_path), '-v', 'quiet',
             '-show_entries', 'format_tags=creation_time,encoder',
             '-of', 'json', str(video_path)],
            capture_output=True, text=True, check=True
        )
        
        metadata = json.loads(result.stdout)
        tags = metadata.get("format", {}).get("tags", {})
        
        # Normalize tag keys to lowercase for case-insensitive access
        tags_lower = {k.lower(): v for k, v in tags.items()}
        creation_time = tags_lower.get("creation_time")
        encoder = tags_lower.get("encoder")
        
        print(f"{PURPLE}Source Metadata:{RESET}")
        print(f"  creation_time: {GREEN}{creation_time}{RESET}")
        print(f"  encoder: {GREEN}{encoder}{RESET}")
        print(f"  modified: {GREEN}{modified_iso}{RESET}")
        
        return {
            'creation_time': creation_time,
            'encoder': encoder,
            'modified': modified_iso
        }
        
    except subprocess.CalledProcessError as e:
        print(f"{RED}Error getting source metadata: {e}{RESET}")
        return {
            'creation_time': None,
            'encoder': None,
            'modified': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"{RED}Error processing metadata: {e}{RESET}")
        return {
            'creation_time': None,
            'encoder': None,
            'modified': datetime.now().isoformat()
        }


def build_comment_metadata(config, video_path, source_metadata):
    """
    Build JSON comment metadata for output file
    
    Args:
        config: TranscodeConfig or TVTranscodeConfig object
        video_path: Path to source video file
        source_metadata: Dictionary with creation_time, encoder, modified
        
    Returns:
        JSON string for comment metadata
    """
    # Build JSON payload with only non-empty values
    comment_data = {
        "source_file": video_path.name,
        "source_video": source_metadata.get('codec_name', 'unknown'),
        "tmdb": str(config.tmdb_id)
    }
    
    # Add optional fields only if they have values
    if source_metadata.get('creation_time'):
        comment_data["creation_time"] = source_metadata['creation_time']
    
    if source_metadata.get('encoder'):
        comment_data["encoder"] = source_metadata['encoder']
    
    # Add movie-specific fields
    if hasattr(config, 'collection') and config.collection and config.collection.lower() not in ['none', 'n/a', '']:
        comment_data["collection"] = config.collection
    
    if hasattr(config, 'edition') and config.edition and config.edition.lower() not in ['none', 'n/a', '']:
        comment_data["edition"] = config.edition
    
    if hasattr(config, 'pressing') and config.pressing and config.pressing.lower() not in ['none', 'n/a', '']:
        comment_data["pressing"] = config.pressing
    
    return json.dumps(comment_data)


def parse_srt_file(srt_path):
    """
    Parse an SRT subtitle file into a list of subtitle entries
    
    Args:
        srt_path: Path to the SRT file
        
    Returns:
        List of dictionaries with 'start', 'end', and 'text' for each subtitle
    """
    subtitles = []
    
    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by double newlines to get each subtitle block
        blocks = re.split(r'\n\s*\n', content.strip())
        
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            
            # Skip the index number (first line)
            # Parse timestamp line (second line)
            timestamp_line = lines[1]
            match = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})', timestamp_line)
            
            if match:
                # Convert start time to seconds
                start_h, start_m, start_s, start_ms = map(int, match.groups()[:4])
                start_time = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000.0
                
                # Convert end time to seconds
                end_h, end_m, end_s, end_ms = map(int, match.groups()[4:])
                end_time = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000.0
                
                # Get subtitle text (all remaining lines)
                text = ' '.join(lines[2:])
                
                # Escape special characters for ffmpeg drawtext filter
                # Replace: \ with \\, ' with \', : with \:, , with \,
                text = text.replace('\\', '\\\\')
                text = text.replace("'", "\\'")
                text = text.replace(':', '\\:')
                text = text.replace(',', '\\,')
                
                subtitles.append({
                    'start': start_time,
                    'end': end_time,
                    'text': text
                })
    
    except Exception as e:
        print(f"{RED}Error parsing SRT file {srt_path}: {e}{RESET}")
        return []
    
    return subtitles


def build_burn_subtitle_filter(srt_path):
    """
    Build ffmpeg drawtext filter to burn subtitles from an SRT file
    
    Args:
        srt_path: Path to the SRT file
        
    Returns:
        String containing the drawtext filter for ffmpeg
    """
    subtitles = parse_srt_file(srt_path)
    
    if not subtitles:
        print(f"{RED}Warning: No subtitles found in {srt_path}{RESET}")
        return None
    
    # Build drawtext filter with all subtitles
    # Use a common font path that works on Windows
    font_path = "C\\\\:/Windows/Fonts/arial.ttf"
    
    filters = []
    for sub in subtitles:
        # Create enable expression for time range
        enable_expr = f"between(t\\,{sub['start']}\\,{sub['end']})"
        
        # Build drawtext filter for this subtitle
        filter_str = (
            f"drawtext=fontfile={font_path}:"
            f"text='{sub['text']}':"
            f"fontcolor=white:"
            f"fontsize=48:"
            f"borderw=2:"
            f"bordercolor=black:"
            f"x=(w-text_w)/2:"
            f"y=h-80:"
            f"enable={enable_expr}"
        )
        filters.append(filter_str)
    
    # Chain all drawtext filters together with commas
    return ','.join(filters)


def parse_subtitle_source(subtitle_field, video_path, working_path, subtitle_type):
    """
    Parse subtitle field to determine source and type
    
    Args:
        subtitle_field: Value from subtitle field (track number, "file", "burn", or anything else = ignore)
        video_path: Path to the source video file (used to construct SRT filename)
        working_path: Path to working directory for SRT files
        subtitle_type: Type of subtitle ('forced', 'en', or 'ensdh') for filename construction
        
    Returns:
        Dictionary with 'type' ('internal', 'external', 'burn', or 'none') and 'source' (track number, file path, or filter string)
        Returns None if subtitle doesn't exist or should be ignored
        
    Raises:
        FileNotFoundError: If subtitle field is "file" or "burn" but the SRT file doesn't exist
    """
    if not subtitle_field or subtitle_field.strip() == '':
        return None
    
    field_value = subtitle_field.strip().lower()
    
    # Check if it's a track number (0, 1, 2, etc.)
    try:
        track_num = int(subtitle_field)
        return {'type': 'internal', 'source': track_num}
    except ValueError:
        pass
    
    # Check if it's the "file" or "burn" keyword
    if field_value in ['file', 'burn']:
        # Construct the appropriate SRT filename based on subtitle type
        video_stem = video_path.stem  # Get filename without extension
        
        if subtitle_type == 'forced':
            srt_filename = f"{video_stem}.en.forced.srt"
        elif subtitle_type == 'en':
            srt_filename = f"{video_stem}.en.srt"
        elif subtitle_type == 'ensdh':
            srt_filename = f"{video_stem}.en.sdh.srt"
        else:
            raise ValueError(f"Unknown subtitle type '{subtitle_type}'")
        
        srt_path = working_path / srt_filename
        if srt_path.exists():
            if field_value == 'burn':
                # Build burn filter
                burn_filter = build_burn_subtitle_filter(srt_path)
                if burn_filter:
                    return {'type': 'burn', 'source': burn_filter, 'srt_path': srt_path}
                else:
                    raise ValueError(f"Could not build burn filter from {srt_path}")
            else:  # file
                return {'type': 'external', 'source': srt_path}
        else:
            raise FileNotFoundError(f"Required SRT file not found: {srt_path}")
    
    # Any other value is ignored
    return None


def build_subtitle_commands(config, video_path, working_path, input_index=0):
    """
    Build ffmpeg subtitle commands based on configuration
    
    Args:
        config: TranscodeConfig object
        video_path: Path to the source video file
        working_path: Path to working directory
        input_index: Current input file index (for external subtitles)
        
    Returns:
        Tuple of (input_commands, mapping_commands, burn_filters, next_input_index)
        
    Raises:
        FileNotFoundError: If a required subtitle file is missing
    """
    input_cmds = []
    mapping_cmds = []
    burn_filters = []
    current_input_idx = input_index
    output_subtitle_idx = 0
    
    # Process subtitles in order: Forced, English, English SDH
    subtitles = [
        {'field': config.sub_enforced, 'title': 'English Forced', 'disposition': 'default+forced', 'type': 'forced'},
        {'field': config.sub_en, 'title': 'English', 'disposition': '0', 'type': 'en'},
        {'field': config.sub_ensdh, 'title': 'English SDH', 'disposition': '0', 'type': 'ensdh'}
    ]
    
    for sub in subtitles:
        # This will raise FileNotFoundError if subtitle field is "file"/"burn" but file doesn't exist
        sub_info = parse_subtitle_source(sub['field'], video_path, working_path, sub['type'])
        
        if sub_info is None:
            continue  # Skip if subtitle doesn't exist
        
        if sub_info['type'] == 'burn':
            # Subtitle to be burned into video
            burn_filters.append(sub_info['source'])
            print(f"{PURPLE}Will burn {sub['title']} subtitles from: {sub_info['srt_path'].name}{RESET}")
        elif sub_info['type'] == 'internal':
            # Subtitle from input file
            mapping_cmds.extend([
                '-map', f'0:s:{sub_info["source"]}',
                '-c:s:' + str(output_subtitle_idx), 'mov_text',
                '-disposition:s:' + str(output_subtitle_idx), sub['disposition'],
                '-metadata:s:s:' + str(output_subtitle_idx), 'language=eng',
                '-metadata:s:s:' + str(output_subtitle_idx), f'title={sub["title"]}'
            ])
            output_subtitle_idx += 1
        elif sub_info['type'] == 'external':
            # Subtitle from external SRT file
            current_input_idx += 1
            input_cmds.extend(['-i', str(sub_info['source'])])
            
            mapping_cmds.extend([
                '-map', f'{current_input_idx}:s:0',
                '-c:s:' + str(output_subtitle_idx), 'mov_text',
                '-disposition:s:' + str(output_subtitle_idx), sub['disposition'],
                '-metadata:s:s:' + str(output_subtitle_idx), 'language=eng',
                '-metadata:s:s:' + str(output_subtitle_idx), f'title={sub["title"]}'
            ])
            output_subtitle_idx += 1
    
    return (input_cmds, mapping_cmds, burn_filters, current_input_idx)


def transcode_file(video_path, config, paths, execution_mode):
    """
    Transcode a single video file based on configuration
    
    Args:
        video_path: Path to the video file
        config: TranscodeConfig object with transcoding parameters
        paths: Dictionary containing all configured paths
        execution_mode: 'Enabled' to execute ffmpeg, 'Disabled' to log only
        
    Returns:
        Boolean indicating processing status:
        - True: Output file already exists (skipped)
        - False: Error occurred (missing .nzb or subtitle files)
        - None: Successfully processed or would process
    """
    print(f"\n{'='*SEPARATOR_WIDTH}")
    print(f"Processing: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{video_path.name}")
    print(f"{'='*SEPARATOR_WIDTH}")
    
    # Print configuration details
    print(f"Title: {config.title} ({config.year})")
    print(f"TMDB ID: {config.tmdb_id}")
    print(f"Edition: {config.edition}")
    print(f"Pressing: {config.pressing}")
    print(f"Collection: {config.collection}")
    print(f"Video Title: {config.video_title}")
    print(f"Video Quality: {config.video_quality}")
    print(f"Audio 0: {config.audio_0}")
    print(f"Audio 1: {config.audio_1}")
    print(f"Forced Subtitles: {config.sub_enforced}")
    print(f"English Subtitles: {config.sub_en}")
    print(f"English SDH: {config.sub_ensdh}")
    print(f"To Do: {config.todo}")
    print(f"Complete: {config.complete}")
    
    # Check if output file already exists before analyzing audio
    # Use same logic as build_ffmpeg_command to determine output path
    if hasattr(config, 'content_type') and config.content_type == 'tv':
        # TV show: output to subfolder named after the show
        show_folder = paths['output_path'] / config.title
        output_filename = f"S{config.season.zfill(2)}E{config.episode.zfill(2)} - {config.episode_title}.mp4"
        output_file_path = show_folder / output_filename
    else:
        # Movie: output directly to output_path
        if config.edition and config.edition.lower() not in ['none', 'n/a', '']:
            output_filename = f"{config.title} ({config.year}) [tmdbid-{config.tmdb_id}] - {config.edition}.mp4"
        else:
            output_filename = f"{config.title} ({config.year}) [tmdbid-{config.tmdb_id}].mp4"
        output_file_path = paths['output_path'] / output_filename
    
    if output_file_path.exists():
        print(f"{YELLOW}Warning: Output file already exists: {output_file_path}{RESET}")
        print(f"{YELLOW}Skipping transcoding to avoid overwrite.{RESET}")
        return True  # File exists
    
    # Check for required .nzb file (movies only)
    if hasattr(config, 'content_type') and config.content_type == 'movie':
        nzb_path = video_path.with_suffix('.nzb')
        if not nzb_path.exists():
            print(f"{RED}Error: Required .nzb file not found: {nzb_path}{RESET}")
            print(f"{RED}Processing stopped for this video.{RESET}")
            return False
        else:
            print(f"{GREEN}Found required .nzb file: {nzb_path.name}{RESET}")
    
    # Detect video resolution and bit depth
    print(f"\n{PURPLE}Analyzing video properties...{RESET}")
    video_info = get_video_info(video_path, paths['ffprobe_path'])
    
    if video_info:
        print(f"{PURPLE}Source resolution: {GREEN}{video_info['width']}x{video_info['height']}{RESET}")
        print(f"{PURPLE}Pixel format: {GREEN}{video_info['pix_fmt']}{RESET}")
        print(f"{PURPLE}10-bit source: {GREEN}{video_info['is_10bit']}{RESET}")
        
        is_10bit = video_info['is_10bit']
        is_2160p = video_info['height'] == 2160
        
        # Check if this TV show should be downscaled to 720p instead of 1080p
        needs_720p_scaling = False
        if hasattr(config, 'content_type') and config.content_type == 'tv':
            try:
                tvdb_id = int(config.tmdb_id)  # tmdb_id field actually contains TVDB ID for TV shows
                if tvdb_id in TVDB_720P_IDS:
                    needs_720p_scaling = True
                    print(f"{PURPLE}TVDB ID {GREEN}{tvdb_id}{PURPLE} is in 720p list - will downscale to 720p{RESET}")
            except (ValueError, AttributeError):
                pass  # If TVDB ID is not a valid integer, ignore
        
        # Determine if scaling is needed
        # Scale if: video is larger than 1080p, OR if it's 1080p and needs 720p downscaling
        needs_scaling = video_info['height'] > 1080 or (needs_720p_scaling and video_info['height'] > 720)
        
        # Detect Dolby Vision
        has_dolby_vision = detect_dolby_vision(video_path, paths['ffprobe_path'])
        if has_dolby_vision:
            print(f"{PURPLE}Dolby Vision detected: {GREEN}Yes (will filter NAL messages){RESET}")
        else:
            print(f"{PURPLE}Dolby Vision detected: {GREEN}No{RESET}")
        
        if needs_scaling:
            if needs_720p_scaling:
                print(f"{PURPLE}Video will be downscaled to 720p maintaining aspect ratio{RESET}")
            else:
                print(f"{PURPLE}Video is larger than 1080p - will downscale to 1080p maintaining aspect ratio{RESET}")
        else:
            target_res = "720p" if needs_720p_scaling else "1080p"
            print(f"{PURPLE}Video is {target_res} or smaller - no scaling needed{RESET}")
    else:
        print(f"{RED}Error: Could not detect video properties{RESET}")
        return False
    
    # Only analyze audio tracks if we're actually going to process the file
    print(f"\n{PURPLE}Analyzing audio tracks...{RESET}")
    
    # Audio 0 analysis
    audio0_track = int(config.audio_0)
    
    # Get channel count
    audio0_channel_count = get_audio_channel_count(video_path, audio0_track, paths['mediainfo_path'])
    
    # Get channel layout (pass channel count for validation)
    audio0_layout = get_audio_channel_layout(video_path, audio0_track, paths['mediainfo_path'], audio0_channel_count)
    
    # Print MediaInfo commands used
    print(f"{PURPLE}Audio 0 - MediaInfo command for channel count:{RESET}")
    print(f"  {GREEN}{paths['mediainfo_path']} --Inform=Audio;%Channel(s)%\\n {video_path}{RESET}")
    print(f"{PURPLE}Audio 0 - MediaInfo command for channel layout:{RESET}")
    print(f"  {GREEN}{paths['mediainfo_path']} --Inform=Audio;%ChannelLayout%\\n {video_path}{RESET}")
    
    if audio0_layout:
        print(f"{PURPLE}Audio 0 - Raw channel count: {GREEN}{audio0_channel_count}{RESET}")
        print(f"{PURPLE}Audio 0 - Detected channel layout: {GREEN}{audio0_layout}{RESET}")
        audio0_settings = determine_audio_settings(audio0_layout)
        if audio0_settings is None:
            print(f"{RED}Error: Unknown audio layout for Audio 0{RESET}")
            return False
        print(f"{PURPLE}Audio 0 - Determined audio layout: {GREEN}{audio0_settings['audio_layout']}{RESET}")
    else:
        # Fallback: Use channel count to determine layout when MediaInfo doesn't provide layout string
        print(f"{YELLOW}Warning: MediaInfo did not return a channel layout for Audio 0{RESET}")
        print(f"{PURPLE}Audio 0 - Raw channel count: {GREEN}{audio0_channel_count}{RESET}")
        print(f"{PURPLE}Audio 0 - Using channel count to determine layout{RESET}")
        
        if audio0_channel_count == 1:
            # Mono
            audio0_settings = {
                "audio_layout": "Mono",
                "filter": "pan=stereo|FL=0.5*c0|FR=0.5*c0",
                "channels": 1,
                "ffmpeg_channel_layout": "mono",
                "bitrate": "128k"
            }
            print(f"{PURPLE}Audio 0 - Determined audio layout: {GREEN}Mono (from channel count){RESET}")
        elif audio0_channel_count == 2:
            # Stereo
            audio0_settings = {
                "audio_layout": "Stereo",
                "filter": "anull",
                "channels": 2,
                "ffmpeg_channel_layout": "stereo",
                "bitrate": "256k"
            }
            print(f"{PURPLE}Audio 0 - Determined audio layout: {GREEN}Stereo (from channel count){RESET}")
        else:
            print(f"{RED}Error: Could not detect audio layout for Audio 0 and channel count ({audio0_channel_count}) is not 1 or 2{RESET}")
            return False
    
    # Audio 1 analysis - skip if "No", reuse Audio 0 settings if same track
    if config.audio_1.strip().lower() == 'no':
        print(f"{PURPLE}Audio 1 - Skipping (field is 'No' - no surround track){RESET}")
        audio1_settings = None
        audio1_codec = None
    else:
        audio1_track = int(config.audio_1)
        if audio1_track == audio0_track:
            print(f"{PURPLE}Audio 1 - Using same track as Audio 0, reusing settings{RESET}")
            audio1_settings = audio0_settings.copy()  # Make a copy so we can modify it
            audio1_codec = get_audio_codec(video_path, audio1_track, paths['mediainfo_path'])
            audio1_channel_count = get_audio_channel_count(video_path, audio1_track, paths['mediainfo_path'])
            
            # Check if this is AC3 5.1 that should be copied
            if audio1_codec == 'AC-3' and audio1_channel_count == 6:
                audio1_settings['copy_audio'] = True
                print(f"{GREEN}Audio 1 - AC3 5.1 detected - will copy instead of transcoding{RESET}")
            else:
                audio1_settings['copy_audio'] = False
        else:
            # Get codec first to determine if we need full analysis
            audio1_codec = get_audio_codec(video_path, audio1_track, paths['mediainfo_path'])
            
            # Get channel count
            audio1_channel_count = get_audio_channel_count(video_path, audio1_track, paths['mediainfo_path'])
            
            # Get channel layout (pass channel count for validation)
            audio1_layout = get_audio_channel_layout(video_path, audio1_track, paths['mediainfo_path'], audio1_channel_count)
            
            # Print MediaInfo commands used
            print(f"{PURPLE}Audio 1 - MediaInfo command for codec:{RESET}")
            print(f"  {GREEN}{paths['mediainfo_path']} --Inform=Audio;%Format%\\n {video_path}{RESET}")
            print(f"{PURPLE}Audio 1 - MediaInfo command for channel count:{RESET}")
            print(f"  {GREEN}{paths['mediainfo_path']} --Inform=Audio;%Channel(s)%\\n {video_path}{RESET}")
            print(f"{PURPLE}Audio 1 - MediaInfo command for channel layout:{RESET}")
            print(f"  {GREEN}{paths['mediainfo_path']} --Inform=Audio;%ChannelLayout%\\n {video_path}{RESET}")
            
            if audio1_layout:
                print(f"{PURPLE}Audio 1 - Codec: {GREEN}{audio1_codec}{RESET}")
                print(f"{PURPLE}Audio 1 - Raw channel count: {GREEN}{audio1_channel_count}{RESET}")
                print(f"{PURPLE}Audio 1 - Detected channel layout: {GREEN}{audio1_layout}{RESET}")
                audio1_settings = determine_audio_settings(audio1_layout)
                if audio1_settings is None:
                    print(f"{RED}Error: Unknown audio layout for Audio 1{RESET}")
                    return False
                print(f"{PURPLE}Audio 1 - Determined audio layout: {GREEN}{audio1_settings['audio_layout']}{RESET}")
                
                # Decide whether to copy or transcode based on codec and channel count
                if audio1_codec == 'AC-3' and audio1_channel_count == 6:
                    audio1_settings['copy_audio'] = True
                    print(f"{GREEN}Audio 1 - AC3 5.1 detected - will copy instead of transcoding{RESET}")
                else:
                    audio1_settings['copy_audio'] = False
                    if audio1_channel_count > 6:
                        print(f"{PURPLE}Audio 1 - {audio1_channel_count} channels detected - will transcode to AC3{RESET}")
                    else:
                        print(f"{PURPLE}Audio 1 - Non-AC3 codec detected - will transcode to AC3{RESET}")
            else:
                # Fallback: Use channel count when MediaInfo doesn't provide layout string
                print(f"{YELLOW}Warning: MediaInfo did not return a channel layout for Audio 1{RESET}")
                print(f"{PURPLE}Audio 1 - Codec: {GREEN}{audio1_codec}{RESET}")
                print(f"{PURPLE}Audio 1 - Raw channel count: {GREEN}{audio1_channel_count}{RESET}")
                print(f"{PURPLE}Audio 1 - Using channel count to determine layout{RESET}")
                
                if audio1_channel_count == 1:
                    # Mono
                    audio1_settings = {
                        "audio_layout": "Mono",
                        "filter": "pan=stereo|FL=0.5*c0|FR=0.5*c0",
                        "channels": 1,
                        "ffmpeg_channel_layout": "mono",
                        "bitrate": "128k",
                        "copy_audio": False
                    }
                    print(f"{PURPLE}Audio 1 - Determined audio layout: {GREEN}Mono (from channel count){RESET}")
                elif audio1_channel_count == 2:
                    # Stereo
                    audio1_settings = {
                        "audio_layout": "Stereo",
                        "filter": "anull",
                        "channels": 2,
                        "ffmpeg_channel_layout": "stereo",
                        "bitrate": "256k",
                        "copy_audio": False
                    }
                    print(f"{PURPLE}Audio 1 - Determined audio layout: {GREEN}Stereo (from channel count){RESET}")
                else:
                    print(f"{RED}Error: Could not detect audio layout for Audio 1 and channel count ({audio1_channel_count}) is not 1 or 2{RESET}")
                    return False
    
    # Extract source metadata
    print(f"\n{PURPLE}Extracting source metadata...{RESET}")
    source_metadata = get_source_metadata(video_path, paths['ffprobe_path'])
    
    # Build subtitle commands - this will raise FileNotFoundError if required subtitle files are missing
    try:
        subtitle_input_cmds, subtitle_mapping_cmds, burn_filters, final_input_idx = build_subtitle_commands(
            config, video_path, paths['working_path'], input_index=0
        )
    except FileNotFoundError as e:
        print(f"{RED}Error: {e}{RESET}")
        print(f"{RED}Processing stopped for this video.{RESET}")
        return False
    
    # Build ffmpeg command
    cmd = build_ffmpeg_command(video_path, config, paths, audio0_settings, audio1_settings, 
                               source_metadata, video_info, needs_scaling, is_10bit, is_2160p, has_dolby_vision,
                               subtitle_input_cmds, subtitle_mapping_cmds, burn_filters, needs_720p_scaling)
    
    # Print the command in a format that can be copy/pasted to terminal
    print(f"\n{PURPLE}Command to be executed:{RESET}")
    # Quote arguments that contain spaces or special characters
    quoted_cmd = []
    prev_arg = None
    for i, arg in enumerate(cmd):
        arg_str = str(arg)
        # Check if this is an input file path (follows -i flag)
        if prev_arg == '-i':
            # Always quote input file paths
            quoted_cmd.append(f'"{arg_str.replace(chr(34), chr(92) + chr(34))}"')
        # Check if this is a value following a -filter flag
        elif prev_arg and prev_arg.startswith('-filter'):
            # Always quote filter values (they contain pipes and commas)
            quoted_cmd.append(f'"{arg_str.replace(chr(34), chr(92) + chr(34))}"')
        # Check if it's a key=value metadata argument
        elif '=' in arg_str and prev_arg and prev_arg.startswith('-metadata'):
            # Quote only the value part if it contains spaces
            key, value = arg_str.split('=', 1)
            if ' ' in value:
                quoted_cmd.append(f'{key}="{value.replace(chr(34), chr(92) + chr(34))}"')
            else:
                quoted_cmd.append(arg_str)
        elif ' ' in arg_str or '|' in arg_str or ',' in arg_str or '{' in arg_str or '}' in arg_str:
            # Quote if it contains special characters
            quoted_cmd.append(f'"{arg_str.replace(chr(34), chr(92) + chr(34))}"')
        else:
            quoted_cmd.append(arg_str)
        prev_arg = arg_str
    print(f"{GREEN}{' '.join(quoted_cmd)}{RESET}")
    
    print(f"\n{BLUE}Output will be saved to: {paths['output_path']}{RESET}\n")
    
    # Execute command if enabled
    if execution_mode == 'Enabled':
        print(f"{PURPLE}Executing ffmpeg command...{RESET}")
        try:
            # Register with interrupt handler
            interrupt_handler.start_transcode(None, output_file_path)
            
            # For Dolby Vision files, filter NAL messages to avoid console spam
            if has_dolby_vision:
                print(f"{YELLOW}Dolby Vision detected - filtering NAL messages from output{RESET}")
                # Start ffmpeg in its own process group so CTRL+C doesn't kill it directly
                # Use platform-specific method to prevent signal propagation
                popen_kwargs = {
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.STDOUT,
                    'universal_newlines': False,  # Read bytes for proper \r handling
                    'bufsize': 0,
                }
                if platform.system() == 'Windows':
                    # On Windows, use CREATE_NEW_PROCESS_GROUP to prevent CTRL+C propagation
                    popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    # On Unix, start in a new session
                    popen_kwargs['start_new_session'] = True
                
                process = subprocess.Popen(cmd, **popen_kwargs)
                
                # Update interrupt handler with the process
                interrupt_handler.current_process = process
                
                # Read and filter output, handling carriage returns properly
                try:
                    buffer = b''
                    while True:
                        # Check if we were interrupted (double CTRL+C)
                        if interrupt_handler.interrupt_count >= 2:
                            break
                        
                        chunk = process.stdout.read(1)
                        if not chunk:
                            # Process ended
                            if buffer:
                                line = buffer.decode('utf-8', errors='replace')
                                if "Skipping NAL" not in line and "repeated" not in line:
                                    sys.stdout.write(line)
                                    sys.stdout.flush()
                            break
                        
                        if chunk == b'\r':
                            # Carriage return - output buffer and reset cursor
                            if buffer:
                                line = buffer.decode('utf-8', errors='replace')
                                if "Skipping NAL" not in line and "repeated" not in line:
                                    sys.stdout.write('\r' + line)
                                    sys.stdout.flush()
                                buffer = b''
                        elif chunk == b'\n':
                            # Newline - output buffer with newline
                            if buffer:
                                line = buffer.decode('utf-8', errors='replace')
                                if "Skipping NAL" not in line and "repeated" not in line:
                                    sys.stdout.write(line + '\n')
                                    sys.stdout.flush()
                                buffer = b''
                            else:
                                sys.stdout.write('\n')
                                sys.stdout.flush()
                        else:
                            buffer += chunk
                except Exception:
                    pass  # Handle broken pipe if process was killed
                
                return_code = process.wait()
                
                # Check if we were interrupted
                if interrupt_handler.interrupt_count >= 2:
                    print(f"\n{YELLOW}Transcoding interrupted by user.{RESET}")
                    interrupt_handler.end_transcode()
                    return 'interrupted'
                
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd)
                
                print(f"\n{GREEN}Transcoding completed successfully!{RESET}")
            else:
                # Normal execution for non-Dolby Vision files
                # Start ffmpeg in its own process group so CTRL+C doesn't kill it directly
                # Use platform-specific method to prevent signal propagation
                popen_kwargs = {
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.STDOUT,
                    'universal_newlines': False,  # Read bytes for proper \r handling
                    'bufsize': 0,
                }
                if platform.system() == 'Windows':
                    # On Windows, use CREATE_NEW_PROCESS_GROUP to prevent CTRL+C propagation
                    popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    # On Unix, start in a new session
                    popen_kwargs['start_new_session'] = True
                
                process = subprocess.Popen(cmd, **popen_kwargs)
                
                # Update interrupt handler with the process
                interrupt_handler.current_process = process
                
                # Read output, handling carriage returns properly for progress updates
                try:
                    buffer = b''
                    while True:
                        # Check if we were interrupted (double CTRL+C)
                        if interrupt_handler.interrupt_count >= 2:
                            break
                        
                        chunk = process.stdout.read(1)
                        if not chunk:
                            # Process ended
                            if buffer:
                                sys.stdout.write(buffer.decode('utf-8', errors='replace'))
                                sys.stdout.flush()
                            break
                        
                        if chunk == b'\r':
                            # Carriage return - output buffer and reset cursor
                            if buffer:
                                sys.stdout.write('\r' + buffer.decode('utf-8', errors='replace'))
                                sys.stdout.flush()
                                buffer = b''
                        elif chunk == b'\n':
                            # Newline - output buffer with newline
                            if buffer:
                                sys.stdout.write(buffer.decode('utf-8', errors='replace') + '\n')
                                sys.stdout.flush()
                                buffer = b''
                            else:
                                sys.stdout.write('\n')
                                sys.stdout.flush()
                        else:
                            buffer += chunk
                except Exception:
                    pass  # Handle broken pipe if process was killed
                
                return_code = process.wait()
                
                # Check if we were interrupted
                if interrupt_handler.interrupt_count >= 2:
                    print(f"\n{YELLOW}Transcoding interrupted by user.{RESET}")
                    interrupt_handler.end_transcode()
                    return 'interrupted'
                
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd)
                
                print(f"\n{GREEN}Transcoding completed successfully!{RESET}")
            
            # End transcode tracking
            interrupt_handler.end_transcode()
            
        except subprocess.CalledProcessError as e:
            interrupt_handler.end_transcode()
            print(f"\n{RED}Error during transcoding: {e}{RESET}")
            print(f"{RED}Return code: {e.returncode}{RESET}")
            print(f"{RED}Skipping this file and continuing with next file...{RESET}")
            return False  # Error occurred, but continue processing
        except Exception as e:
            interrupt_handler.end_transcode()
            print(f"{RED}Unexpected error: {e}{RESET}")
            print(f"{RED}Skipping this file and continuing with next file...{RESET}")
            return False  # Error occurred, but continue processing
    else:
        print(f"{YELLOW}Execution mode is Disabled - command not executed{RESET}")
    
    return None  # Successfully processed (or would process in disabled mode)


def build_ffmpeg_command(video_path, config, paths, audio0_settings, audio1_settings, 
                        source_metadata, video_info, needs_scaling, is_10bit, is_2160p, has_dolby_vision,
                        subtitle_input_cmds, subtitle_mapping_cmds, burn_filters, needs_720p_scaling=False):
    """
    Build the complete ffmpeg command based on configuration
    
    Args:
        video_path: Path to the video file
        config: TranscodeConfig object with transcoding parameters
        paths: Dictionary containing all configured paths
        audio0_settings: Dictionary with audio_layout and filter for Audio 0
        audio1_settings: Dictionary with audio_layout and filter for Audio 1
        source_metadata: Dictionary with creation_time, encoder, modified
        video_info: Dictionary with width, height, pix_fmt, is_10bit
        needs_scaling: Boolean indicating if video needs to be downscaled to 1080p
        is_10bit: Boolean indicating if source video is 10-bit
        is_2160p: Boolean indicating if source video is 2160p
        has_dolby_vision: Boolean indicating if video has Dolby Vision
        subtitle_input_cmds: List of subtitle input commands from build_subtitle_commands
        subtitle_mapping_cmds: List of subtitle mapping commands from build_subtitle_commands
        burn_filters: List of burn subtitle filters to apply to video
        needs_720p_scaling: Boolean indicating if video should be downscaled to 720p instead of 1080p (for specific TV shows)
        
    Returns:
        List containing the complete ffmpeg command with all arguments
    """
    cmd = []
    
    # Base ffmpeg command with hardware acceleration
    # Use CUDA output format for GPU-based scaling whenever scaling is needed
    if needs_scaling:
        cmd.extend([
            str(paths['ffmpeg_path']),
            '-hwaccel', 'cuda',
            '-hwaccel_output_format', 'cuda',
            '-i', str(video_path)
        ])
    else:
        cmd.extend([
            str(paths['ffmpeg_path']),
            '-hwaccel', 'cuda',
            '-i', str(video_path)
        ])
    
    # Add external subtitle inputs
    cmd.extend(subtitle_input_cmds)
    
    # Video mapping and encoding
    cmd.extend(['-map', '0:v:0'])
    
    # Determine target resolution for bitrate calculation
    # Use 720p bitrates for shows in the 720p list, 1080p otherwise
    target_resolution = 720 if needs_720p_scaling else 1080
    
    # Add video encoding parameters based on quality setting and target resolution
    video_params = get_video_encoding_params(config.video_quality, target_resolution)
    cmd.extend(video_params)
    
    # Build video filter chain (only if not copying video)
    vf_filters = []
    
    if config.video_quality != 'Copy':
        # Add scaling filter if needed (before format conversion and burn)
        if needs_scaling:
            # Determine target height: 720p for specific TV shows, 1080p otherwise
            target_height = 720 if needs_720p_scaling else 1080
            
            # Always use CUDA-accelerated scaling with Lanczos interpolation
            # scale_cuda handles format conversion to yuv420p automatically
            vf_filters.append(f'scale_cuda=-2:{target_height}:interp_algo=lanczos:format=yuv420p')
        
        # Add color format filter if 10-bit source (skip if scale_cuda already handled it)
        if is_10bit and not needs_scaling:
            vf_filters.append('format=yuv420p')
        
        # Add burned subtitle filters (these must come after scaling/format)
        if burn_filters:
            vf_filters.extend(burn_filters)
        
        # Apply video filters if any exist
        if vf_filters:
            cmd.extend(['-vf', ','.join(vf_filters)])
    
    # Video metadata
    cmd.extend([
        '-metadata:s:v:0', 'language=eng',
        '-metadata:s:v:0', f'title={config.video_title}'
    ])
    
    # Audio track 0 (stereo downmix or mono)
    audio0_track = int(config.audio_0)
    # Determine the output format - if source is mono, output is mono; otherwise stereo
    if audio0_settings['channels'] == 1:
        audio0_title = "AAC Mono"
    else:
        audio0_title = "AAC Stereo"
    
    cmd.extend([
        '-map', f'0:a:{audio0_track}',
        '-c:a:0', 'libfdk_aac',
        '-ac:a:0', '2',
        '-filter:a:0', audio0_settings['filter'],
        '-disposition:a:0', 'default',
        '-metadata:s:a:0', 'language=eng',
        '-metadata:s:a:0', f'title={audio0_title}'
    ])
    
    # Audio track 1 (surround) - only if audio1_settings is not None and source has more than 2 channels
    if audio1_settings is not None and audio1_settings['channels'] > 2:
        audio1_track = int(config.audio_1)
        
        # Check if we should copy the audio (AC3 5.1) or transcode it
        if audio1_settings.get('copy_audio', False):
            # Copy AC3 5.1 audio as-is
            cmd.extend([
                '-map', f'0:a:{audio1_track}',
                '-c:a:1', 'copy',
                '-disposition:a:1', '0',
                '-metadata:s:a:1', 'language=eng',
                '-metadata:s:a:1', f'title=AC3 {audio1_settings["audio_layout"]}'
            ])
        else:
            # Transcode to AC3 (for 7.1 or non-AC3 codecs) - better device compatibility
            # AC3 maximum is 5.1 (6 channels), so 7.1 sources will be downmixed automatically
            ac3_channels = min(audio1_settings['channels'], 6)  # Cap at 6 for AC3 5.1 max
            
            # Adjust title if downmixing from 7.1 to 5.1
            if audio1_settings['channels'] > 6:
                # 7.1 downmixed to 5.1 - show as 5.1 in title
                audio1_title = audio1_settings['audio_layout'].replace('7.1', '5.1').replace('back', 'side')
            else:
                # Use original layout
                audio1_title = audio1_settings['audio_layout']
            
            cmd.extend([
                '-map', f'0:a:{audio1_track}',
                '-c:a:1', 'ac3',
                '-ac:a:1', str(ac3_channels),
                '-b:a:1', '640k',
                '-disposition:a:1', '0',
                '-metadata:s:a:1', 'language=eng',
                '-metadata:s:a:1', f'title=AC3 {audio1_title}'
            ])
    
    # Add subtitle mapping commands
    cmd.extend(subtitle_mapping_cmds)
    
    # Build comment metadata JSON
    comment_json = build_comment_metadata(config, video_path, source_metadata)
    
    # Determine title for metadata
    if hasattr(config, 'content_type') and config.content_type == 'tv':
        metadata_title = config.get_display_name()
    else:
        metadata_title = config.title
    
    # Global metadata
    cmd.extend([
        '-map_metadata', '-1',
        '-map_chapters', '-1',
        '-metadata', f'title={metadata_title}',
        '-metadata', 'stik=9',
        '-metadata', f'comment={comment_json}'
    ])
    
    # Output options
    if hasattr(config, 'content_type') and config.content_type == 'tv':
        # TV show: output to subfolder named after the show
        show_folder = paths['output_path'] / config.title
        show_folder.mkdir(parents=True, exist_ok=True)
        output_filename = f"S{config.season.zfill(2)}E{config.episode.zfill(2)} - {config.episode_title}.mp4"
        output_path = show_folder / output_filename
    else:
        # Movie: output directly to output_path
        if config.edition and config.edition.lower() not in ['none', 'n/a', '']:
            output_filename = f"{config.title} ({config.year}) [tmdbid-{config.tmdb_id}] - {config.edition}.mp4"
        else:
            output_filename = f"{config.title} ({config.year}) [tmdbid-{config.tmdb_id}].mp4"
        output_path = paths['output_path'] / output_filename
    
    cmd.extend([
        '-movflags', '+faststart',
        '-n',
        str(output_path)
    ])
    
    return cmd



def process_content_type(content_type, config, paths, execution_mode):
    """
    Process all items for a given content type (movie or tv)
    
    Args:
        content_type: 'movie' or 'tv'
        config: Configuration dictionary
        paths: Dictionary of tool paths
        execution_mode: 'Enabled' or 'Disabled'
        
    Returns:
        Dictionary with processing statistics
    """
    # Determine which sheet and working path to use
    if content_type == 'movie':
        sheet_name = config['sheet_name_movie']
        working_path = config['working_path_movie']
        config_class = TranscodeConfig
        json_filename = 'movie.json'
    else:  # tv
        sheet_name = config['sheet_name_tv']
        working_path = config['working_path_tv']
        config_class = TVTranscodeConfig
        json_filename = 'tv.json'
    
    sheet_mode = config['sheet_mode']
    output_path = config['output_path']
    
    print(f"\n{BLUE}{'='*SEPARATOR_WIDTH}")
    print(f"Processing {content_type.upper()} content")
    print(f"{'='*SEPARATOR_WIDTH}{RESET}")
    print(f"{BLUE}Sheet: {GREEN}{sheet_name}{BLUE}")
    print(f"Working folder: {GREEN}{working_path}{RESET}")
    
    # Get sheet data based on mode
    if sheet_mode == 'Online':
        print(f"\n{PURPLE}Reading data from sheet {GREEN}'{sheet_name}'{PURPLE}...{RESET}")
        service = get_google_sheets_service()
        header, sheet_rows = read_sheet_data(service, config['spreadsheet_id'], sheet_name)
        
        if not sheet_rows:
            print(f"{YELLOW}No rows found in {sheet_name}. Skipping {content_type} processing.{RESET}")
            return {'processed': 0, 'skipped_complete': 0, 'skipped_invalid': 0, 
                    'skipped_not_found': 0, 'skipped_exists': 0, 'errors': 0, 'total': 0, 
                    'error_files': [], 'interrupted': False}
        
        # Save sheet data (header + rows) to JSON so Offline mode can use column names too
        json_path = output_path / json_filename
        with open(json_path, 'w') as f:
            json.dump({'header': header, 'rows': sheet_rows}, f, indent=2)
        print(f"{GREEN}Saved sheet data to {json_path}{RESET}")
    else:  # Offline mode
        print(f"\n{PURPLE}Loading data from {GREEN}{json_filename}{PURPLE}...{RESET}")
        json_path = output_path / json_filename
        try:
            with open(json_path, 'r') as f:
                saved = json.load(f)
            # Support both the new {header, rows} format and the legacy flat-list format
            if isinstance(saved, dict) and 'header' in saved and 'rows' in saved:
                header = saved['header']
                sheet_rows = saved['rows']
            else:
                # Legacy format: list of rows with no header stored
                print(f"{YELLOW}Warning: {json_filename} uses legacy format (no header). "
                      f"Re-run in Online mode to update it.{RESET}")
                header = []
                sheet_rows = saved
        except FileNotFoundError:
            print(f"{YELLOW}File {json_filename} not found. Skipping {content_type} processing.{RESET}")
            return {'processed': 0, 'skipped_complete': 0, 'skipped_invalid': 0, 
                    'skipped_not_found': 0, 'skipped_exists': 0, 'errors': 0, 'total': 0,
                    'error_files': [], 'interrupted': False}
    
    # Build column-name → index map so config classes look up values by name, not position.
    col_map = {name: idx for idx, name in enumerate(header)}
    
    print(f"{PURPLE}Found {GREEN}{len(sheet_rows)}{PURPLE} rows in the sheet.{RESET}")
    
    # Get video files in folder
    if content_type == 'movie':
        # Movies: scan both working_path_movie and working_path_legacy
        working_path_legacy = config.get('working_path_legacy')
        
        print(f"\n{PURPLE}Scanning for video files in {GREEN}'{working_path}'{PURPLE}...{RESET}")
        video_files = get_mkv_files(working_path)
        video_map = {f.stem.lower(): f for f in video_files}
        print(f"Found {len(video_files)} video file(s) in main movie folder.")
        
        # Also scan legacy folder
        legacy_video_files = []
        legacy_video_map = {}
        if working_path_legacy and working_path_legacy.exists():
            print(f"{PURPLE}Scanning for video files in {GREEN}'{working_path_legacy}'{PURPLE} (Legacy)...{RESET}")
            legacy_video_files = get_mkv_files(working_path_legacy)
            legacy_video_map = {f.stem.lower(): f for f in legacy_video_files}
            print(f"Found {len(legacy_video_files)} video file(s) in legacy folder.")
        else:
            print(f"{YELLOW}Legacy folder not found or not configured: {working_path_legacy}{RESET}")
        
        # Combine both maps - files are looked up based on which folder they should be in
        # (determined per-row based on video_title containing "Legacy")
        combined_video_map = {**video_map, **legacy_video_map}
        
        if not combined_video_map:
            print(f"{YELLOW}No video files found in any movie folders. Skipping {content_type} processing.{RESET}")
            return {'processed': 0, 'skipped_complete': 0, 'skipped_invalid': 0, 
                    'skipped_not_found': 0, 'skipped_exists': 0, 'errors': 0, 'total': 0, 
                    'error_files': [], 'interrupted': False}
        
        print(f"{GREEN}Total: {len(combined_video_map)} unique video file(s) found across all movie folders.{RESET}")
        
        # Store both maps and paths for per-row lookup
        video_map = combined_video_map
        video_map_main = {f.stem.lower(): f for f in video_files}
        video_map_legacy = legacy_video_map
    else:
        # TV: scan subfolders for each unique show in the sheet
        print(f"\n{PURPLE}Scanning for TV show folders...{RESET}")
        
        # Get unique show names from sheet rows
        show_names = set()
        for row in sheet_rows:
            if row and len(row) > 0 and row[col_map.get('Show', 0)]:  # Has title
                title_idx = col_map.get('Show', 0)
                show_names.add(row[title_idx])
        
        print(f"Found {len(show_names)} unique show(s) in sheet: {', '.join(sorted(show_names))}")
        
        # Scan each show's subfolder
        video_files = []
        for show_name in show_names:
            show_folder = working_path / show_name
            if show_folder.exists() and show_folder.is_dir():
                show_videos = get_mkv_files(show_folder)
                video_files.extend(show_videos)
                print(f"  {show_name}: {len(show_videos)} file(s)")
            else:
                print(f"  {YELLOW}{show_name}: folder not found{RESET}")
        
        if not video_files:
            print(f"{YELLOW}No video files found in any show folders. Skipping {content_type} processing.{RESET}")
            return {'processed': 0, 'skipped_complete': 0, 'skipped_invalid': 0, 
                    'skipped_not_found': 0, 'skipped_exists': 0, 'errors': 0, 'total': 0, 
                    'error_files': [], 'interrupted': False}
        
        print(f"{GREEN}Total: {len(video_files)} video file(s) found across all shows.{RESET}")
        video_map = {f.stem.lower(): f for f in video_files}
    
    # Update paths dictionary with correct working path for this content type
    paths_for_type = paths.copy()
    paths_for_type['working_path'] = working_path
    
    # Process each row
    stats = {
        'processed': 0,
        'skipped_complete': 0,
        'skipped_invalid': 0,
        'skipped_not_found': 0,
        'skipped_exists': 0,
        'errors': 0,
        'total': 0,
        'error_files': [],  # Track input files that resulted in errors
        'interrupted': False  # Track if processing was interrupted
    }
    
    for idx, row in enumerate(sheet_rows, start=2):  # Start at 2 (row 1 is header)
        # Check if we should stop due to interrupt
        if interrupt_handler.should_exit():
            print(f"\n{YELLOW}Stopping processing due to user interrupt...{RESET}")
            stats['interrupted'] = True
            break
        
        if not row or not row[0]:  # Skip empty rows
            continue
        
        stats['total'] += 1
            
        # Parse row into config object
        config_obj = config_class(row, col_map)
        
        # Skip rows with invalid data
        is_valid, error_msg = config_obj.is_valid()
        if not is_valid:
            display_name = config_obj.get_display_name() if hasattr(config_obj, 'get_display_name') else config_obj.title
            print(f"{RED}Skipping row {idx}: Invalid data ({error_msg}) - '{display_name}'{RESET}")
            stats['skipped_invalid'] += 1
            continue
        
        # Skip rows where Complete is not FALSE (includes TRUE, empty, or any other value)
        if config_obj.should_skip_complete():
            display_name = config_obj.get_display_name() if hasattr(config_obj, 'get_display_name') else config_obj.title
            print(f"{YELLOW}Skipping row {idx}: Complete is not FALSE - '{display_name}'{RESET}")
            stats['skipped_complete'] += 1
            continue
        
        # Check if the file exists in our video folder
        file_stem = Path(config_obj.input_file).stem.lower()
        
        if file_stem in video_map:
            video_path = video_map[file_stem]
            
            # For movies, determine the correct working path based on video_title
            if content_type == 'movie':
                # Check if "Legacy" is in the video_title field (case-insensitive)
                is_legacy = 'legacy' in config_obj.video_title.lower() if config_obj.video_title else False
                
                if is_legacy:
                    # Use legacy working path
                    paths_for_type['working_path'] = config.get('working_path_legacy', working_path)
                else:
                    # Use standard movie working path
                    paths_for_type['working_path'] = working_path
            else:
                # For TV shows, use the video file's parent directory as working path
                # This ensures subtitle files are looked for in the same folder as the video
                paths_for_type['working_path'] = video_path.parent
            
            result = transcode_file(video_path, config_obj, paths_for_type, execution_mode)
            if result is True:
                stats['skipped_exists'] += 1
            elif result is False:
                stats['errors'] += 1
                stats['error_files'].append(config_obj.input_file)
            elif result == 'interrupted':
                # Transcoding was interrupted - stop processing
                stats['interrupted'] = True
                break
            else:
                stats['processed'] += 1
        else:
            # More helpful error message for movies
            if content_type == 'movie':
                is_legacy = 'legacy' in config_obj.video_title.lower() if config_obj.video_title else False
                if is_legacy:
                    expected_path = config.get('working_path_legacy', working_path)
                    print(f"{RED}Skipping row {idx}: File '{config_obj.input_file}' not found in {expected_path} (Legacy){RESET}")
                else:
                    print(f"{RED}Skipping row {idx}: File '{config_obj.input_file}' not found in {working_path}{RESET}")
            else:
                print(f"{RED}Skipping row {idx}: File '{config_obj.input_file}' not found in {working_path}{RESET}")
            stats['skipped_not_found'] += 1
    
    return stats



def main():
    """Main execution function - processes both movies and TV shows"""
    # Load configuration first (before logging setup)
    config = load_config()
    output_path = config['output_path']
    
    # Set up logging to file and console
    log_file = setup_logging(output_path)
    
    # Install the interrupt handler
    interrupt_handler.install()
    
    was_interrupted = False
    
    try:
        print(f"{BLUE}Video Transcoding Script with Google Sheets Integration")
        print(f"{'='*SEPARATOR_WIDTH}{RESET}")
        
        # Extract configuration values
        sheet_mode = config['sheet_mode']
        execution_mode = config['execution_mode']
        
        # Create paths dictionary for easy passing to functions
        paths = {
            'ffmpeg_path': config['ffmpeg_path'],
            'ffprobe_path': config['ffprobe_path'],
            'mediainfo_path': config['mediainfo_path'],
            'output_path': output_path
        }
        
        print(f"{BLUE}Sheet Mode: {GREEN}{sheet_mode}{BLUE}")
        print(f"Execution Mode: {GREEN}{execution_mode}{BLUE}")
        print(f"Output folder: {GREEN}{output_path}{BLUE}")
        print(f"Log file: {GREEN}{output_path / datetime.now().strftime('%Y-%m-%d.log')}{RESET}")
        print(f"{BLUE}Press CTRL+C to interrupt (behavior varies based on transcoding state){RESET}")
        
        # Process movies first
        print(f"\n{BLUE}{'='*SEPARATOR_WIDTH}")
        print(f"Starting transcoding process: {GREEN}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{BLUE}")
        print(f"{'='*SEPARATOR_WIDTH}{RESET}")
        
        movie_stats = process_content_type('movie', config, paths, execution_mode)
        
        # Check if movie processing was interrupted
        if movie_stats.get('interrupted', False) or interrupt_handler.should_exit():
            was_interrupted = True
            print(f"\n{YELLOW}Movie processing interrupted. Skipping TV shows.{RESET}")
            tv_stats = {'processed': 0, 'skipped_complete': 0, 'skipped_invalid': 0, 
                        'skipped_not_found': 0, 'skipped_exists': 0, 'errors': 0, 
                        'total': 0, 'error_files': [], 'interrupted': True}
        else:
            tv_stats = process_content_type('tv', config, paths, execution_mode)
            if tv_stats.get('interrupted', False):
                was_interrupted = True
        
        # Combined summary
        total_processed = movie_stats['processed'] + tv_stats['processed']
        total_skipped_complete = movie_stats['skipped_complete'] + tv_stats['skipped_complete']
        total_skipped_exists = movie_stats['skipped_exists'] + tv_stats['skipped_exists']
        total_skipped_invalid = movie_stats['skipped_invalid'] + tv_stats['skipped_invalid']
        total_skipped_not_found = movie_stats['skipped_not_found'] + tv_stats['skipped_not_found']
        total_errors = movie_stats['errors'] + tv_stats['errors']
        total_rows = movie_stats['total'] + tv_stats['total']
        
        print(f"{BLUE}\n" + "="*SEPARATOR_WIDTH)
        if was_interrupted:
            print(f"Transcoding Interrupted by User!")
        else:
            print(f"Transcoding Complete!")
        print(f"\nMOVIES:")
        print(f"  Total rows: {movie_stats['total']}")
        print(f"  Processed: {movie_stats['processed']}")
        print(f"  Skipped (already complete): {movie_stats['skipped_complete']}")
        print(f"  Skipped (output file exists): {movie_stats['skipped_exists']}")
        print(f"  Skipped (invalid data): {movie_stats['skipped_invalid']}")
        print(f"  Skipped (file not found): {movie_stats['skipped_not_found']}")
        print(f"  Errors: {movie_stats['errors']}")
        print(f"\nTV SHOWS:")
        print(f"  Total rows: {tv_stats['total']}")
        print(f"  Processed: {tv_stats['processed']}")
        print(f"  Skipped (already complete): {tv_stats['skipped_complete']}")
        print(f"  Skipped (output file exists): {tv_stats['skipped_exists']}")
        print(f"  Skipped (invalid data): {tv_stats['skipped_invalid']}")
        print(f"  Skipped (file not found): {tv_stats['skipped_not_found']}")
        print(f"  Errors: {tv_stats['errors']}")
        print(f"\nOVERALL:")
        print(f"  Total rows: {total_rows}")
        print(f"  Processed: {total_processed}")
        print(f"  Skipped (already complete): {total_skipped_complete}")
        print(f"  Skipped (output file exists): {total_skipped_exists}")
        print(f"  Skipped (invalid data): {total_skipped_invalid}")
        print(f"  Skipped (file not found): {total_skipped_not_found}")
        print(f"  Errors: {total_errors}")
        
        # List files that had errors
        all_error_files = movie_stats['error_files'] + tv_stats['error_files']
        if all_error_files:
            print(f"\n  Files with errors:{RED}")
            for error_file in all_error_files:
                print(f"    - {error_file}")
        
        print(f"{BLUE}{'='*SEPARATOR_WIDTH}{RESET}")
        
        # Play completion alert
        beep_alert(3)
    
    except KeyboardInterrupt:
        # Handle interrupt when not transcoding
        was_interrupted = True
        print(f"\n{YELLOW}Script interrupted by user.{RESET}")
        
    finally:
        # Uninstall the interrupt handler
        interrupt_handler.uninstall()
        
        # Send webhook notification
        webhook_url = "https://server.domain.com:1234/api/webhook/webhook_job_name"
        if was_interrupted:
            send_webhook_notification(webhook_url, "remux interrupted")
        else:
            send_webhook_notification(webhook_url, "remux done")
        
        # Clean up and close log file
        print(f"{BLUE}\n{'='*SEPARATOR_WIDTH}")
        print(f"Session ended: {GREEN}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{BLUE}")
        print(f"{'='*SEPARATOR_WIDTH}{RESET}")
        sys.stdout = sys.stdout.original_stream
        sys.stderr = sys.stderr.original_stream
        log_file.close()



if __name__ == "__main__":
    main()