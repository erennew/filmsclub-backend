"""
Media probing with ffmpeg and mediainfo for detailed track information.
Supports progressive streaming analysis without full downloads.
"""

import asyncio
import json
import os
import re
import uuid
import logging
from typing import Optional, List, Dict, Tuple, Any
from functools import lru_cache
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove

logger = logging.getLogger(__name__)

# Language mapping for audio tracks
LANGUAGE_MAP: Dict[str, str] = {
    'en': 'English', 'eng': 'English',
    'hi': 'Hindi', 'hin': 'Hindi',
    'ta': 'Tamil', 'tam': 'Tamil',
    'te': 'Telugu', 'tel': 'Telugu',
    'ml': 'Malayalam', 'mal': 'Malayalam',
    'kn': 'Kannada', 'kan': 'Kannada',
    'bn': 'Bengali', 'ben': 'Bengali',
    'mr': 'Marathi', 'mar': 'Marathi',
    'gu': 'Gujarati', 'guj': 'Gujarati',
    'pa': 'Punjabi', 'pun': 'Punjabi',
    'ja': 'Japanese', 'jpn': 'Japanese',
    'ko': 'Korean', 'kor': 'Korean',
    'zh': 'Chinese', 'chi': 'Chinese',
    'es': 'Spanish', 'spa': 'Spanish',
    'fr': 'French', 'fra': 'French',
    'de': 'German', 'deu': 'German',
    'it': 'Italian', 'ita': 'Italian',
    'ru': 'Russian', 'rus': 'Russian',
    'ar': 'Arabic', 'ara': 'Arabic',
    'pt': 'Portuguese', 'por': 'Portuguese',
    'tr': 'Turkish', 'tur': 'Turkish',
    'nl': 'Dutch', 'nld': 'Dutch',
    'pl': 'Polish', 'pol': 'Polish',
    'vi': 'Vietnamese', 'vie': 'Vietnamese',
    'th': 'Thai', 'tha': 'Thai',
    'id': 'Indonesian', 'ind': 'Indonesian',
    'ms': 'Malay', 'msa': 'Malay',
    'unknown': 'Original Audio',
}

# Progressive streaming steps (increasing sizes)
STREAM_STEPS = [
    ("16KB", 16 * 1024),
    ("64KB", 64 * 1024),
    ("256KB", 256 * 1024),
    ("1MB", 1 * 1024 * 1024),
    ("4MB", 4 * 1024 * 1024),
    ("10MB", 10 * 1024 * 1024),
    ("25MB", 25 * 1024 * 1024),
]


@lru_cache(maxsize=256)
def get_language_name(code: str) -> str:
    """Get full language name from code."""
    if not code:
        return 'Unknown'
    cleaned = code.split('(')[0].strip().lower()
    return LANGUAGE_MAP.get(cleaned, code.title())


@lru_cache(maxsize=64)
def get_quality_from_height(height: int) -> Optional[str]:
    """Convert height to quality string."""
    if not height:
        return None
    if height <= 240: return "240p"
    if height <= 360: return "360p"
    if height <= 480: return "480p"
    if height <= 720: return "720p"
    if height <= 1080: return "1080p"
    if height <= 1440: return "1440p"
    if height <= 2160: return "2160p"
    return "4K+"


@lru_cache(maxsize=128)
def get_video_format(codec: str, hdr: str = '', bit_depth: str = '') -> Optional[str]:
    """Format video codec string."""
    if not codec:
        return None
    
    codec = codec.lower()
    parts = []
    
    # Codec detection
    if any(x in codec for x in ('hevc', 'h.265', 'h265')):
        parts.append('HEVC')
    elif 'av1' in codec:
        parts.append('AV1')
    elif any(x in codec for x in ('avc', 'avc1', 'h.264', 'h264')):
        parts.append('x264')
    elif 'vp9' in codec:
        parts.append('VP9')
    elif any(x in codec for x in ('mpeg4', 'xvid')):
        parts.append('MPEG4')
    else:
        return None
    
    # Bit depth
    if bit_depth and bit_depth.isdigit() and int(bit_depth) > 8:
        parts.append(f"{bit_depth}bit")
    
    # HDR detection
    if hdr and any(x in hdr.lower() for x in ('hdr', 'dolby vision', 'pq', 'hlg')):
        parts.append('HDR')
    
    return ' '.join(parts)


def parse_duration(value: Any) -> float:
    """Parse duration from various formats."""
    try:
        if not value:
            return 0.0
        v = str(value).strip()
        
        # Direct number (seconds)
        if v.replace('.', '', 1).lstrip('-').isdigit():
            f = float(v)
            if f > 86400000:  # Microseconds
                return f / 1000000
            if f > 86400:  # Milliseconds
                return f / 1000
            return f
        
        # HH:MM:SS format
        if ':' in v:
            parts = [float(p) for p in v.split(':')]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
    except Exception:
        pass
    return 0.0


def format_duration(seconds: float) -> str:
    """Format duration as HH:MM:SS."""
    if not seconds:
        return "00:00:00"
    s = int(seconds)
    return f"{s//3600:02}:{(s%3600)//60:02}:{s%60:02}"


async def run_mediainfo(path: str, timeout: int = 10) -> dict:
    """Run mediainfo on file."""
    try:
        proc = await asyncio.create_subprocess_shell(
            f'mediainfo --Output=JSON "{path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return json.loads(stdout.decode() or '{}')
    except Exception as e:
        logger.debug(f"mediainfo error: {e}")
        return {}


async def run_ffprobe(path: str, timeout: int = 10) -> dict:
    """Run ffprobe on file."""
    try:
        proc = await asyncio.create_subprocess_shell(
            f'ffprobe -v error -show_streams -show_format -of json "{path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return json.loads(stdout.decode() or '{}')
    except Exception as e:
        logger.debug(f"ffprobe error: {e}")
        return {}


async def probe_file(path: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Probe media file using both mediainfo and ffprobe.
    Returns comprehensive media information.
    """
    try:
        # Run both tools in parallel
        mi_task = run_mediainfo(path)
        fp_task = run_ffprobe(path)
        
        mi_data, fp_data = await asyncio.gather(mi_task, fp_task)
        
        result = {
            'duration': 0.0,
            'width': None,
            'height': None,
            'codec': None,
            'bit_depth': None,
            'hdr': None,
            'audio_tracks': [],
            'subtitle_tracks': [],
            'has_subtitles': False,
        }
        
        # Parse ffprobe data (primary)
        if fp_data:
            streams = fp_data.get('streams', [])
            fmt = fp_data.get('format', {})
            
            # Duration
            if fmt.get('duration'):
                result['duration'] = parse_duration(fmt['duration'])
            
            for stream in streams:
                codec_type = stream.get('codec_type', '').lower()
                tags = stream.get('tags', {})
                
                if codec_type == 'video':
                    result['width'] = stream.get('width') or stream.get('coded_width')
                    result['height'] = stream.get('height') or stream.get('coded_height')
                    result['codec'] = stream.get('codec_name', '').lower()
                    result['bit_depth'] = stream.get('bits_per_raw_sample') or stream.get('bits_per_coded_sample')
                    
                    # HDR detection
                    color_transfer = stream.get('color_transfer', '').lower()
                    color_space = stream.get('color_space', '').lower()
                    if any(x in color_transfer for x in ('smpte2084', 'arib-std-b67', 'pq', 'hlg')):
                        result['hdr'] = 'HDR'
                    elif 'bt2020' in color_space:
                        result['hdr'] = 'HDR'
                    
                elif codec_type == 'audio':
                    lang = tags.get('language', 'unknown')
                    title = tags.get('title', '')
                    result['audio_tracks'].append({
                        'index': len(result['audio_tracks']),
                        'language': get_language_name(lang),
                        'codec': stream.get('codec_name', 'unknown'),
                        'channels': stream.get('channels', 2),
                        'title': title or get_language_name(lang),
                    })
                    
                elif codec_type == 'subtitle':
                    lang = tags.get('language', 'unknown')
                    result['subtitle_tracks'].append({
                        'index': len(result['subtitle_tracks']),
                        'language': get_language_name(lang),
                        'codec': stream.get('codec_name', 'unknown'),
                    })
                    result['has_subtitles'] = True
        
        # Parse mediainfo data (supplement)
        if mi_data:
            tracks = mi_data.get('media', {}).get('track', [])
            for track in tracks:
                track_type = track.get('@type', '').lower()
                
                if track_type == 'video':
                    if not result['height']:
                        for field in ('Height', 'Sampled_Height', 'Encoded_Height'):
                            raw = str(track.get(field, '') or '').split()[0]
                            if raw.isdigit():
                                result['height'] = int(raw)
                                break
                    
                    if not result['codec']:
                        codec = track.get('Format', '').lower()
                        if codec:
                            result['codec'] = codec
                            
                elif track_type == 'audio':
                    lang = track.get('Language', 'unknown')
                    # Avoid duplicates
                    existing_langs = [t['language'] for t in result['audio_tracks']]
                    lang_name = get_language_name(lang)
                    if lang_name not in existing_langs:
                        result['audio_tracks'].append({
                            'index': len(result['audio_tracks']),
                            'language': lang_name,
                            'codec': track.get('Format', 'unknown'),
                            'channels': int(track.get('Channels', 2)) if track.get('Channels', '').isdigit() else 2,
                            'title': lang_name,
                        })
        
        return result
        
    except Exception as e:
        logger.error(f"Error probing file {path}: {e}")
        return {}


async def stream_and_probe(media, message_id: int, max_size_mb: int = 50) -> Dict[str, Any]:
    """
    Stream media from Telegram and probe progressively.
    Stops once we have enough data to determine media info.
    """
    temp_file = None
    try:
        file_size = getattr(media, 'file_size', 0) or 0
        max_bytes = max_size_mb * 1024 * 1024
        probe_size = min(file_size, max_bytes) if file_size else max_bytes
        
        result = None
        
        for step_name, step_size in STREAM_STEPS:
            if step_size > probe_size:
                break
                
            temp_file = f"probe_{step_name}_{message_id}_{uuid.uuid4().hex[:8]}.bin"
            
            try:
                # Stream chunk
                written = 0
                async with aiopen(temp_file, 'wb') as f:
                    async for chunk in media.stream():
                        if not chunk:
                            break
                        remaining = step_size - written
                        if remaining <= 0:
                            break
                        await f.write(chunk[:remaining])
                        written += len(chunk[:remaining])
                        if written >= step_size:
                            break
                
                # Probe this chunk
                probe_result = await probe_file(temp_file)
                
                # Check if we have enough info
                if probe_result.get('height') or probe_result.get('audio_tracks'):
                    result = probe_result
                    logger.info(f"Got media info from {step_name} chunk")
                    break
                    
            except Exception as e:
                logger.debug(f"{step_name} probe failed: {e}")
            finally:
                if temp_file and os.path.exists(temp_file):
                    await aioremove(temp_file)
                    temp_file = None
        
        # If progressive probing failed, try full download (for small files only)
        if not result and file_size and file_size <= 100 * 1024 * 1024:  # 100MB max
            temp_file = f"full_{message_id}_{uuid.uuid4().hex[:8]}.bin"
            try:
                await media.download(temp_file)
                result = await probe_file(temp_file)
            finally:
                if temp_file and os.path.exists(temp_file):
                    await aioremove(temp_file)
        
        return result or {}
        
    except Exception as e:
        logger.error(f"Error in stream_and_probe: {e}")
        return {}
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                await aioremove(temp_file)
            except:
                pass


def build_media_caption(
    title: str,
    probe_result: Dict[str, Any],
    quality: str = None,
    languages: List[str] = None
) -> str:
    """Build enhanced caption with media info."""
    duration = probe_result.get('duration', 0)
    height = probe_result.get('height')
    codec = probe_result.get('codec')
    bit_depth = probe_result.get('bit_depth')
    hdr = probe_result.get('hdr')
    audio_tracks = probe_result.get('audio_tracks', [])
    has_subs = probe_result.get('has_subtitles', False)
    
    # Quality
    video_quality = quality or get_quality_from_height(height) or "Unknown"
    
    # Video format
    video_format = get_video_format(codec or '', hdr or '', bit_depth or '') or "Unknown"
    
    # Audio languages
    audio_langs = [t['language'] for t in audio_tracks] if audio_tracks else languages or ['Original Audio']
    audio_str = ', '.join(audio_langs[:3])  # Max 3 languages
    if len(audio_langs) > 3:
        audio_str += f" +{len(audio_langs)-3}"
    
    # Subtitle
    subtitle_str = "ESUB" if has_subs else "No Esubs"
    
    # Duration
    duration_str = format_duration(duration)
    
    # Build caption
    caption = f"""🎬 <b>{title}</b>

🎞️ <b>Quality:</b> {video_quality} {video_format}
⏳ <b>Duration:</b> {duration_str}
🔊 <b>Audio:</b> {audio_str}
💬 <b>Subtitles:</b> {subtitle_str}

📢 <b>ALL NEW SERIES & MOVIES 🔎</b>"""
    
    return caption
