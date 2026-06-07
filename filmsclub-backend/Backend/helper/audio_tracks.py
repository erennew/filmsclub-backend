import asyncio
import json
import logging
from typing import List, Dict, Optional

# Language code to display name mapping
LANG_MAP = {
    'eng': 'English', 'en': 'English',
    'hin': 'Hindi', 'hi': 'Hindi',
    'tam': 'Tamil', 'ta': 'Tamil',
    'tel': 'Telugu', 'te': 'Telugu',
    'kan': 'Kannada', 'kn': 'Kannada',
    'mal': 'Malayalam', 'ml': 'Malayalam',
    'mar': 'Marathi', 'mr': 'Marathi',
    'ben': 'Bengali', 'bn': 'Bengali',
    'guj': 'Gujarati', 'gu': 'Gujarati',
    'pun': 'Punjabi', 'pa': 'Punjabi',
    'urd': 'Urdu', 'ur': 'Urdu',
    'ara': 'Arabic', 'ar': 'Arabic',
    'spa': 'Spanish', 'es': 'Spanish',
    'fra': 'French', 'fr': 'French',
    'deu': 'German', 'de': 'German',
    'ita': 'Italian', 'it': 'Italian',
    'por': 'Portuguese', 'pt': 'Portuguese',
    'rus': 'Russian', 'ru': 'Russian',
    'jpn': 'Japanese', 'ja': 'Japanese',
    'kor': 'Korean', 'ko': 'Korean',
    'zho': 'Chinese', 'zh': 'Chinese',
    'und': 'Unknown'
}


def get_language_name(code: str) -> str:
    """Get display name for language code."""
    if not code:
        return "Unknown"
    return LANG_MAP.get(code.lower(), code.upper())


async def probe_audio_tracks(file_url: str, timeout: int = 30) -> List[Dict]:
    """
    Detect audio tracks in video file using ffprobe.
    
    Args:
        file_url: Full URL to the video file (e.g., http://localhost:8000/dl/xxx/movie.mkv)
        timeout: Maximum time to wait for ffprobe
        
    Returns:
        List of audio track dictionaries with index, language, title, codec, channels
    """
    try:
        # First, check if file is accessible
        head_cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'json',
            file_url
        ]
        
        head_process = await asyncio.create_subprocess_exec(
            *head_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(
            head_process.communicate(),
            timeout=10
        )
        
        if head_process.returncode != 0:
            logging.warning(f"File not accessible: {stderr.decode()[:100]}")
            return []
        
        # Now get audio tracks
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'a',
            '-show_entries', 'stream=index,codec_name,codec_type,channels,sample_rate,bit_rate,tags:stream_tags=language,title',
            file_url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logging.warning(f"FFprobe timeout for {file_url}")
            return []
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "Unknown error"
            logging.warning(f"FFprobe failed: {error_msg[:200]}")
            return []
        
        try:
            data = json.loads(stdout.decode('utf-8', errors='ignore'))
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse ffprobe JSON: {e}")
            return []
        
        streams = data.get('streams', [])
        
        if not streams:
            logging.info(f"No audio streams found in {file_url}")
            return []
        
        audio_tracks = []
        for idx, stream in enumerate(streams):
            # Skip if not audio stream
            if stream.get('codec_type') != 'audio':
                continue
            
            tags = stream.get('tags', {})
            language = tags.get('language') or tags.get('lang') or ''
            title = tags.get('title') or tags.get('handler_name') or ''
            
            track_info = {
                'index': idx,
                'stream_index': stream.get('index', idx),
                'codec': stream.get('codec_name', 'unknown'),
                'codec_long': stream.get('codec_long_name', ''),
                'channels': stream.get('channels', 2),
                'channel_layout': stream.get('channel_layout', 'stereo'),
                'sample_rate': stream.get('sample_rate', '48000'),
                'bitrate': stream.get('bit_rate', ''),
                'language': language,
                'title': title,
                'is_default': idx == 0  # Mark first track as default
            }
            
            # Generate readable title if not present
            if not track_info['title']:
                track_info['title'] = get_language_name(track_info['language'])
                if track_info['title'] == "Unknown":
                    track_info['title'] = f"Audio {idx + 1}"
            
            audio_tracks.append(track_info)
        
        logging.info(f"Found {len(audio_tracks)} audio tracks for {file_url}")
        return audio_tracks
        
    except Exception as e:
        logging.error(f"Error in probe_audio_tracks: {e}")
        return []


async def get_audio_tracks_cached(
    db,
    file_id: str,
    file_url: str,
    media_type: str = None,
    tmdb_id: int = None,
    season_number: int = None,
    episode_number: int = None,
    quality: str = None
) -> List[Dict]:
    """
    Get audio tracks with caching in database.
    First checks if we already have the audio track info cached.
    If not, probes the file and caches the result.
    
    Args:
        db: Database instance
        file_id: Encoded file ID string
        file_url: Full URL to the video file
        media_type: "movie" or "tv"
        tmdb_id: TMDB ID of the media
        season_number: Season number (for TV)
        episode_number: Episode number (for TV)
        quality: Quality string (e.g., "1080p")
        
    Returns:
        List of audio track dictionaries
    """
    # Check if we have cached audio tracks in the database
    try:
        if media_type == "movie" and tmdb_id and quality:
            # Check movie collection
            movie = await db.movie_collection.find_one({"tmdb_id": tmdb_id})
            if movie and "telegram" in movie:
                for q in movie["telegram"]:
                    if q.get("id") == file_id and q.get("audio_tracks"):
                        return q["audio_tracks"]
        
        elif media_type == "tv" and tmdb_id and season_number is not None and episode_number is not None and quality:
            # Check TV collection
            tv_show = await db.tv_collection.find_one({"tmdb_id": tmdb_id})
            if tv_show and "seasons" in tv_show:
                for season in tv_show["seasons"]:
                    if season.get("season_number") == season_number:
                        for episode in season.get("episodes", []):
                            if episode.get("episode_number") == episode_number:
                                for q in episode.get("telegram", []):
                                    if q.get("id") == file_id and q.get("audio_tracks"):
                                        return q["audio_tracks"]
    except Exception as e:
        logging.debug(f"Error checking cached audio tracks: {e}")
    
    # Not cached, probe the file
    audio_tracks = await probe_audio_tracks(file_url)
    
    # Cache the result
    if audio_tracks and media_type and tmdb_id and quality:
        try:
            if media_type == "movie":
                await db.update_movie_audio_tracks(tmdb_id, quality, audio_tracks)
            elif media_type == "tv" and season_number is not None and episode_number is not None:
                await db.update_tv_episode_audio_tracks(tmdb_id, season_number, episode_number, quality, audio_tracks)
        except Exception as e:
            logging.warning(f"Failed to cache audio tracks: {e}")
    
    return audio_tracks
