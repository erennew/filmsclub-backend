"""
Update Channel Module
Handles sending formatted notifications to update channels when new content is added.
"""

import asyncio
from typing import Dict, Any, Optional, List
from pyrogram import Client
from pyrogram.errors import FloodWait, ChannelInvalid, ChatWriteForbidden, UserNotParticipant
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from Backend.logger import LOGGER
from Backend.config import Config


def create_update_keyboard(metadata: Dict[str, Any], media_type: str) -> InlineKeyboardMarkup:
    """Create inline keyboard for update messages with download and site buttons."""
    buttons = []
    
    # Get necessary info
    tmdb_id = metadata.get('tmdb_id')
    db_index = metadata.get('db_index', 1)
    season_number = metadata.get('season_number', 1)
    episode_number = metadata.get('episode_number', 1)
    
    if tmdb_id:
        # First row: Download buttons for different qualities
        download_buttons = []
        qualities = ['720p', '1080p', '2160p']
        
        for quality in qualities:
            if media_type == 'movie':
                tg_download_url = f"https://t.me/{Config.TG_USERNAME}?start=file_{tmdb_id}_{db_index}_0_0_{quality}"
            else:
                tg_download_url = f"https://t.me/{Config.TG_USERNAME}?start=file_{tmdb_id}_{db_index}_{season_number}_{episode_number}_{quality}"
            
            download_buttons.append(InlineKeyboardButton(f"📥 {quality}", url=tg_download_url))
        
        if download_buttons:
            buttons.append(download_buttons[:3])
        
        # Second row: Visit Site button
        if media_type == 'movie':
            site_url = f"{Config.WEBSITE_URL}/mov/{tmdb_id}"
        else:
            site_url = f"{Config.WEBSITE_URL}/ser/{tmdb_id}"
        
        buttons.append([
            InlineKeyboardButton("🌐 Visit Site", url=site_url)
        ])
    else:
        # Fallback if no tmdb_id available
        buttons.append([
            InlineKeyboardButton("🌐 Visit Site", url=Config.WEBSITE_URL)
        ])
    
    return InlineKeyboardMarkup(buttons)


def get_language_name(language_code: str) -> str:
    """Convert language code to readable name."""
    language_names = {
        'en': 'English',
        'es': 'Spanish', 
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'ja': 'Japanese',
        'ko': 'Korean',
        'zh': 'Chinese',
        'ru': 'Russian',
        'pt': 'Portuguese',
        'hi': 'Hindi',
        'ar': 'Arabic',
        'nl': 'Dutch',
        'pl': 'Polish',
        'sv': 'Swedish',
        'no': 'Norwegian',
        'da': 'Danish',
        'tr': 'Turkish',
        'th': 'Thai',
        'vi': 'Vietnamese',
        'id': 'Indonesian',
        'ms': 'Malay',
        'fi': 'Finnish',
        'he': 'Hebrew',
        'cs': 'Czech',
        'hu': 'Hungarian',
        'ro': 'Romanian',
        'bg': 'Bulgarian',
        'hr': 'Croatian',
        'sk': 'Slovak',
        'sl': 'Slovenian',
        'et': 'Estonian',
        'lv': 'Latvian',
        'lt': 'Lithuanian',
        'uk': 'Ukrainian',
        'be': 'Belarusian',
        'ka': 'Georgian',
        'am': 'Armenian',
        'az': 'Azerbaijani'
    }
    
    return language_names.get(language_code.lower(), language_code.title())


def get_country_flag(language: str) -> str:
    """Get country flag emoji based on language."""
    language_flags = {
        'english': '🇺🇸',
        'en': '🇺🇸',
        'spanish': '🇪🇸',
        'es': '🇪🇸',
        'french': '🇫🇷',
        'fr': '🇫🇷',
        'german': '🇩🇪',
        'de': '🇩🇪',
        'italian': '🇮🇹',
        'it': '🇮🇹',
        'japanese': '🇯🇵',
        'ja': '🇯🇵',
        'korean': '🇰🇷',
        'ko': '🇰🇷',
        'chinese': '🇨🇳',
        'zh': '🇨🇳',
        'russian': '🇷🇺',
        'ru': '🇷🇺',
        'portuguese': '🇵🇹',
        'pt': '🇵🇹',
        'hindi': '🇮🇳',
        'hi': '🇮🇳',
        'arabic': '🇸🇦',
        'ar': '🇸🇦',
        'dutch': '🇳🇱',
        'nl': '🇳🇱',
        'polish': '🇵🇱',
        'pl': '🇵🇱',
        'swedish': '🇸🇪',
        'sv': '🇸🇪',
        'norwegian': '🇳🇴',
        'no': '🇳🇴',
        'danish': '🇩🇰',
        'da': '🇩🇰',
        'turkish': '🇹🇷',
        'tr': '🇹🇷',
        'thai': '🇹🇭',
        'th': '🇹🇭',
        'vietnamese': '🇻🇳',
        'vi': '🇻🇳',
        'indonesian': '🇮🇩',
        'id': '🇮🇩',
        'malay': '🇲🇾',
        'ms': '🇲🇾',
    }
    
    return language_flags.get(language.lower(), '🌐')


def format_genres(genres: List[str]) -> str:
    """Format genres list into a readable string."""
    if not genres:
        return "Unknown"
    
    displayed_genres = genres[:3]
    return ", ".join(displayed_genres)


def truncate_plot(plot: str, max_length: int = 500) -> str:
    """Truncate plot to specified length with ellipsis if needed."""
    if not plot or plot == "No plot available.":
        return "No plot available."
    
    if len(plot) <= max_length:
        return plot
    
    truncated = plot[:max_length]
    last_period = truncated.rfind('.')
    last_exclamation = truncated.rfind('!')
    last_question = truncated.rfind('?')
    
    last_sentence_end = max(last_period, last_exclamation, last_question)
    
    if last_sentence_end > max_length * 0.7:
        return plot[:last_sentence_end + 1]
    else:
        last_space = truncated.rfind(' ')
        if last_space > max_length * 0.8:
            return plot[:last_space] + "..."
        else:
            return truncated + "..."


def format_plot_blockquote(plot: str) -> str:
    """Format plot using blockquote for better visual separation."""
    if not plot or plot == "No plot available.":
        return "No plot available."
    
    truncated_plot = truncate_plot(plot, max_length=600)
    return f"<blockquote>{truncated_plot}</blockquote>"


def format_movie_update(metadata: Dict[str, Any]) -> str:
    """Format movie update message."""
    title = metadata.get('title', 'Unknown Movie')
    year = metadata.get('year', 'N/A')
    languages = metadata.get('languages', ['en'])
    primary_language = languages[0] if languages else 'en'
    rating = metadata.get('rate', metadata.get('rating', 'N/A'))
    genres = metadata.get('genres', [])
    plot = metadata.get('description', metadata.get('plot', 'No plot available.'))
    quality = metadata.get('quality', 'N/A')
    
    flag = get_country_flag(primary_language)
    
    if rating and rating != 'N/A' and rating != 0:
        rating_str = f"{rating}/10"
    else:
        rating_str = 'N/A'
    
    genres_str = format_genres(genres)
    language_name = get_language_name(primary_language)
    language_info = f"{flag} {language_name}"
    quality_str = quality if quality and quality != 'N/A' else 'N/A'
    formatted_plot = format_plot_blockquote(plot)
    
    message = f"""<blockquote><b>🌟 NEW MOVIE ADDED!</b></blockquote>
<b>┃</b>
<b>┣ 🎬 {title} ({year})</b>
<b>┃</b>
<b>┣ 📺 Type: Movie</b>
<b>┣ 🗣️ Language: {language_info}</b>
<b>┣ ⭐ Rating: {rating_str}</b>
<b>┣ 🎭 Genres: {genres_str}</b>
<b>┣ 🎥 Quality: {quality_str}</b>
<b>┃</b>
<b>┗ 📖 Plot:</b> {formatted_plot}

📢 <b>ALL NEW SERIES & MOVIES 🔎</b>"""
    
    return message


def format_series_update(metadata: Dict[str, Any]) -> str:
    """Format TV series update message."""
    title = metadata.get('title', 'Unknown Series')
    year = metadata.get('year', 'N/A')
    languages = metadata.get('languages', ['en'])
    primary_language = languages[0] if languages else 'en'
    rating = metadata.get('rate', metadata.get('rating', 'N/A'))
    genres = metadata.get('genres', [])
    plot = metadata.get('description', metadata.get('plot', 'No plot available.'))
    quality = metadata.get('quality', 'N/A')
    
    flag = get_country_flag(primary_language)
    
    if rating and rating != 'N/A' and rating != 0:
        rating_str = f"{rating}/10"
    else:
        rating_str = 'N/A'
    
    genres_str = format_genres(genres)
    language_name = get_language_name(primary_language)
    language_info = f"{flag} {language_name}"
    quality_str = quality if quality and quality != 'N/A' else 'N/A'
    formatted_plot = format_plot_blockquote(plot)
    
    message = f"""<blockquote><b>🌟 NEW SERIES ADDED!</b></blockquote>
<b>┃</b>
<b>┣ 🎬 {title} ({year})</b>
<b>┃</b>
<b>┣ 📺 Type: TV Series</b>
<b>┣ 🗣️ Language: {language_info}</b>
<b>┣ ⭐ Rating: {rating_str}</b>
<b>┣ 🎭 Genres: {genres_str}</b>
<b>┣ 🎥 Quality: {quality_str}</b>
<b>┃</b>
<b>┗ 📖 Plot:</b> {formatted_plot}

📢 <b>ALL NEW SERIES & MOVIES 🔎</b>"""
    
    return message


def format_episode_update(metadata: Dict[str, Any]) -> str:
    """Format episode update message."""
    title = metadata.get('title', 'Unknown Series')
    year = metadata.get('year', 'N/A')
    languages = metadata.get('languages', ['en'])
    primary_language = languages[0] if languages else 'en'
    rating = metadata.get('rate', metadata.get('rating', 'N/A'))
    genres = metadata.get('genres', [])
    plot = metadata.get('description', metadata.get('plot', 'No plot available.'))
    season_number = metadata.get('season_number', 1)
    episode_number = metadata.get('episode_number', 1)
    quality = metadata.get('quality', 'N/A')
    
    flag = get_country_flag(primary_language)
    
    if rating and rating != 'N/A' and rating != 0:
        rating_str = f"{rating}/10"
    else:
        rating_str = 'N/A'
    
    genres_str = format_genres(genres)
    language_name = get_language_name(primary_language)
    language_info = f"{flag} {language_name}"
    season_ep = f"S{season_number:02d} E{episode_number}"
    quality_str = quality if quality and quality != 'N/A' else 'N/A'
    formatted_plot = format_plot_blockquote(plot)
    
    message = f"""<blockquote><b>🦄 NEW EPISODE ADDED!</b></blockquote>
<b>📊 {season_ep} 🔥</b>
<b>┃</b>
<b>┣ 🎬 {title} ({year})</b>
<b>┃</b>
<b>┣ 📺 Type: TV Series</b>
<b>┣ 🗣️ Language: {language_info}</b>
<b>┣ ⭐ Rating: {rating_str}</b>
<b>┣ 🎭 Genres: {genres_str}</b>
<b>┣ 🎥 Quality: {quality_str}</b>
<b>┃</b>
<b>┗ 📖 Plot:</b> {formatted_plot}

📢 <b>ALL NEW SERIES & MOVIES 🔎</b>"""
    
    return message


async def send_update_notification(client: Client, metadata: Dict[str, Any], media_type: str) -> bool:
    """
    Send update notification to the update channel.
    
    Args:
        client: Pyrogram client instance
        metadata: Media metadata dictionary
        media_type: Type of media ('movie', 'series', 'episode')
    
    Returns:
        bool: True if notification was sent successfully, False otherwise
    """
    # Check if update channel is configured
    if not Config.UPDATE_CHANNEL:
        LOGGER.warning("UPDATE_CHANNEL not configured, skipping notification")
        return False
    
    try:
        # Format message based on media type
        if media_type == 'movie':
            message = format_movie_update(metadata)
        elif media_type == 'series':
            message = format_series_update(metadata)
        elif media_type == 'episode':
            message = format_episode_update(metadata)
        else:
            LOGGER.error(f"Unknown media type: {media_type}")
            return False
        
        # Get poster URL from metadata
        poster_url = metadata.get('poster') or metadata.get('poster_url') or metadata.get('backdrop_url')
        
        # Create keyboard
        keyboard = create_update_keyboard(metadata, media_type)
        
        # Try to send with poster first, fallback to text-only
        if poster_url:
            try:
                await client.send_photo(
                    chat_id=Config.UPDATE_CHANNEL,
                    photo=poster_url,
                    caption=message,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
                LOGGER.info(f"Update notification with poster sent successfully for {media_type}: {metadata.get('title', 'Unknown')}")
                return True
            except Exception as poster_error:
                LOGGER.warning(f"Failed to send with poster, falling back to text-only: {poster_error}")
        
        # Send text-only message (fallback or when no poster available)
        await client.send_message(
            chat_id=Config.UPDATE_CHANNEL,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard
        )
        
        LOGGER.info(f"Update notification sent successfully for {media_type}: {metadata.get('title', 'Unknown')}")
        return True
        
    except FloodWait as e:
        LOGGER.warning(f"FloodWait error when sending update notification: {e.value} seconds")
        await asyncio.sleep(e.value)
        try:
            # Retry after waiting
            poster_url = metadata.get('poster') or metadata.get('poster_url') or metadata.get('backdrop_url')
            keyboard = create_update_keyboard(metadata, media_type)
            
            if poster_url:
                try:
                    await client.send_photo(
                        chat_id=Config.UPDATE_CHANNEL,
                        photo=poster_url,
                        caption=message,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard
                    )
                    LOGGER.info(f"Update notification with poster sent successfully after FloodWait for {media_type}: {metadata.get('title', 'Unknown')}")
                    return True
                except Exception:
                    pass
            
            await client.send_message(
                chat_id=Config.UPDATE_CHANNEL,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=keyboard
            )
            LOGGER.info(f"Update notification sent successfully after FloodWait for {media_type}: {metadata.get('title', 'Unknown')}")
            return True
        except Exception as retry_error:
            LOGGER.error(f"Failed to send update notification after FloodWait retry: {retry_error}")
            return False
            
    except ChannelInvalid:
        LOGGER.error(f"Invalid update channel ID: {Config.UPDATE_CHANNEL}")
        return False
        
    except ChatWriteForbidden:
        LOGGER.error(f"Bot doesn't have permission to write in update channel: {Config.UPDATE_CHANNEL}")
        return False
        
    except UserNotParticipant:
        LOGGER.error(f"Bot is not a member of update channel: {Config.UPDATE_CHANNEL}")
        return False
        
    except Exception as e:
        LOGGER.error(f"Unexpected error sending update notification: {e}")
        return False


async def notify_new_content(client: Client, metadata: Dict[str, Any]) -> bool:
    """
    Determine content type and send appropriate notification (automatic trigger).
    
    Args:
        client: Pyrogram client instance
        metadata: Media metadata dictionary
    
    Returns:
        bool: True if notification was sent successfully, False otherwise
    """
    try:
        # Determine media type from metadata
        if metadata.get('episode_number'):
            media_type = 'episode'
        elif metadata.get('media_type') == 'movie':
            media_type = 'movie'
        elif metadata.get('media_type') == 'tv':
            media_type = 'series'
        else:
            if metadata.get('season_number') and not metadata.get('episode_number'):
                media_type = 'series'
            elif metadata.get('season_number') and metadata.get('episode_number'):
                media_type = 'episode'
            else:
                media_type = 'movie'
        
        return await send_update_notification(client, metadata, media_type)
        
    except Exception as e:
        LOGGER.error(f"Error in notify_new_content: {e}")
        return False


async def send_manual_update(client: Client, metadata: Dict[str, Any], media_type: str) -> bool:
    """
    Manually send update notification (manual trigger).
    
    Args:
        client: Pyrogram client instance
        metadata: Media metadata dictionary
        media_type: Type of media ('movie', 'series', 'episode')
    
    Returns:
        bool: True if notification was sent successfully, False otherwise
    """
    return await send_update_notification(client, metadata, media_type)


# For backward compatibility
async def send_to_update_channel(client: Client, metadata: Dict[str, Any]) -> bool:
    """
    Legacy function name for backward compatibility.
    """
    return await notify_new_content(client, metadata)
