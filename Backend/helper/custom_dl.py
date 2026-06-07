import asyncio
from pyrogram import utils, raw
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Session, Auth
from typing import Dict, Union
from Backend.logger import LOGGER
from Backend.helper.exceptions import FIleNotFound
from Backend.helper.pyro import get_file_ids
from Backend.pyrofork import work_loads
from Backend.helper.cache import file_metadata_cache, cache_stats, video_chunk_cache
from pyrogram import Client


class ByteStreamer:
    def __init__(self, client: Client):
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.__cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, chat_id: int, message_id: int) -> FileId:
        """
        Get file properties from cache or fetch from Telegram.
        
        Uses shared LRU cache across all ByteStreamer instances
        for better cache hit rates.
        """
        cache_key = f"{chat_id}:{message_id}"
        
        # Try shared cache first (cross-instance)
        cached = await file_metadata_cache.get(cache_key)
        if cached:
            await cache_stats.record_hit()
            LOGGER.debug(f"File metadata cache hit for {cache_key}")
            return cached
        
        await cache_stats.record_miss()
        
        # Try instance-level cache (legacy, for backward compatibility)
        if message_id in self.__cached_file_ids:
            file_id = self.__cached_file_ids[message_id]
            # Also populate shared cache
            await file_metadata_cache.set(cache_key, file_id)
            return file_id
        
        # Fetch from Telegram
        file_id = await get_file_ids(self.client, int(chat_id), int(message_id))
        if not file_id:
            LOGGER.info('Message with ID %s not found!', message_id)
            raise FIleNotFound
        
        # Cache in both locations
        self.__cached_file_ids[message_id] = file_id
        await file_metadata_cache.set(cache_key, file_id)
        LOGGER.debug(f"Cached file metadata for {cache_key}")
        
        return file_id

    async def yield_file(self, file_id: FileId, index: int, offset: int, first_part_cut: int, last_part_cut: int, part_count: int, chunk_size: int) -> Union[str, None]: # type: ignore
        """Original yield_file method - kept for backward compatibility."""
        client = self.client
        work_loads[index] += 1
        LOGGER.debug(f"Starting to yielding file with client {index}.")
        media_session = await self.generate_media_session(client, file_id)
        current_part = 1
        location = await self.get_location(file_id)
        try:
            r = await media_session.send(raw.functions.upload.GetFile(location=location, offset=offset, limit=chunk_size))
            if isinstance(r, raw.types.upload.File):
                while True:
                    chunk = r.bytes
                    if not chunk:
                        break
                    elif part_count == 1:
                        yield chunk[first_part_cut:last_part_cut]
                    elif current_part == 1:
                        yield chunk[first_part_cut:]
                    elif current_part == part_count:
                        yield chunk[:last_part_cut]
                    else:
                        yield chunk

                    current_part += 1
                    offset += chunk_size

                    if current_part > part_count:
                        break
                    
                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location, offset=offset, limit=chunk_size
                        ),
                    )
        except (TimeoutError, AttributeError):
            pass
        finally:
            LOGGER.debug("Finished yielding file with {current_part} parts.")
            work_loads[index] -= 1

    async def yield_file_with_cache(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
        stream_id: str = None
    ) -> Union[str, None]:
        """
        Yield file with intelligent chunk-level caching.
        
        Uses video_chunk_cache to store:
        - First chunks for instant video start
        - Recently accessed chunks for smooth seeking
        
        Args:
            file_id: Telegram FileId object
            index: Client index for workload tracking
            offset: Starting byte offset
            first_part_cut: Bytes to cut from first chunk
            last_part_cut: Bytes to cut from last chunk  
            part_count: Total number of parts to yield
            chunk_size: Size of each chunk
            stream_id: Optional stream identifier for active stream buffering
        """
        client = self.client
        work_loads[index] += 1
        file_id_str = str(file_id.media_id) if hasattr(file_id, 'media_id') else str(file_id)
        
        LOGGER.debug(f"Starting cached file yield with client {index}, stream_id={stream_id}")
        
        # Create stream buffer if stream_id provided
        if stream_id:
            await video_chunk_cache.create_stream_buffer(stream_id, ttl=300)
        
        media_session = None
        current_part = 1
        current_offset = offset
        
        try:
            # Check hot start cache for first request
            if offset == 0 and current_part == 1:
                hot_start = await video_chunk_cache.get_chunk(file_id_str, 0, chunk_size)
                if hot_start:
                    LOGGER.debug(f"Hot start cache hit for {file_id_str}")
                    # Apply first_part_cut if this is the only part
                    if part_count == 1:
                        yield hot_start[first_part_cut:last_part_cut]
                    else:
                        yield hot_start[first_part_cut:]
                    current_part += 1
                    current_offset += chunk_size
            
            # Get media session (only if we need to fetch from Telegram)
            media_session = await self.generate_media_session(client, file_id)
            location = await self.get_location(file_id)
            
            # Continue with remaining chunks
            while current_part <= part_count:
                # Check active stream buffer
                if stream_id:
                    buffered = await video_chunk_cache.get_from_stream_buffer(
                        stream_id, current_offset
                    )
                    if buffered:
                        LOGGER.debug(f"Stream buffer hit for {file_id_str} at {current_offset}")
                        yield buffered
                        current_part += 1
                        current_offset += chunk_size
                        continue
                
                # Fetch from Telegram
                try:
                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location,
                            offset=current_offset,
                            limit=chunk_size
                        )
                    )
                except Exception as e:
                    LOGGER.warning(f"Telegram API error at offset {current_offset}: {e}")
                    break
                
                if isinstance(r, raw.types.upload.File):
                    chunk = r.bytes
                    if not chunk:
                        break
                    
                    raw_chunk = chunk  # Store raw for caching
                    
                    # Apply cuts for first/last parts
                    if part_count == 1:
                        chunk = chunk[first_part_cut:last_part_cut]
                    elif current_part == 1:
                        chunk = chunk[first_part_cut:]
                    elif current_part == part_count:
                        chunk = chunk[:last_part_cut]
                    
                    # Cache the chunk (in background, don't block)
                    is_start = current_offset < video_chunk_cache.hot_start_size
                    try:
                        await video_chunk_cache.cache_chunk(
                            file_id_str, current_offset, raw_chunk, is_start=is_start
                        )
                    except Exception as e:
                        LOGGER.debug(f"Failed to cache chunk: {e}")
                    
                    # Add to stream buffer
                    if stream_id:
                        try:
                            await video_chunk_cache.add_to_stream_buffer(
                                stream_id, current_offset, raw_chunk
                            )
                        except Exception as e:
                            LOGGER.debug(f"Failed to add to stream buffer: {e}")
                    
                    yield chunk
                    current_part += 1
                    current_offset += chunk_size
                else:
                    break
                    
        except (TimeoutError, AttributeError) as e:
            LOGGER.debug(f"Streaming error: {e}")
        except Exception as e:
            LOGGER.error(f"Unexpected error in yield_file_with_cache: {e}")
        finally:
            LOGGER.debug(f"Finished cached file yield. Parts: {current_part - 1}")
            work_loads[index] -= 1
            if stream_id:
                await video_chunk_cache.close_stream_buffer(stream_id)

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        media_session = client.media_sessions.get(file_id.dc_id, None)
        if media_session is None:
            if file_id.dc_id != await client.storage.dc_id():
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await Auth(client, file_id.dc_id, await client.storage.test_mode()).create(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
                for _ in range(6):
                    exported_auth = await client.invoke(raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id))
                    try:
                        
                        await media_session.send(raw.functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))
                        break
                    except AuthBytesInvalid:
                        LOGGER.debug(f"Invalid authorization bytes for DC {file_id.dc_id}, retrying...")
                    except OSError:
                        LOGGER.debug(f"Connection error, retrying...")
                        await asyncio.sleep(2)
                else:
                    await media_session.stop()
                    LOGGER.debug(f"Failed to establish media session for DC {file_id.dc_id} after multiple retries")
                    return None 
            else:
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await client.storage.auth_key(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
            LOGGER.debug(f"Created media session for DC {file_id.dc_id}")
            client.media_sessions[file_id.dc_id] = media_session
        else:
            LOGGER.debug(f"Using cached media session for DC {file_id.dc_id}")
        return media_session


    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation, raw.types.InputDocumentFileLocation, raw.types.InputPeerPhotoFileLocation]:
        file_type = file_id.file_type
        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash)
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(channel_id=utils.get_channel_id(
                        file_id.chat_id), access_hash=file_id.chat_access_hash)
            location = raw.types.InputPeerPhotoFileLocation(peer=peer,
                                                            volume_id=file_id.volume_id,
                                                            local_id=file_id.local_id,
                                                            big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG)
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(id=file_id.media_id,
                                                        access_hash=file_id.access_hash,
                                                        file_reference=file_id.file_reference,
                                                        thumb_size=file_id.thumbnail_size)
        else:
            location = raw.types.InputDocumentFileLocation(id=file_id.media_id,
                                                           access_hash=file_id.access_hash,
                                                           file_reference=file_id.file_reference,
                                                           thumb_size=file_id.thumbnail_size)
        return location

    async def clean_cache(self) -> None:
        """
        function to clean the cache to reduce memory usage
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.__cached_file_ids.clear()
            LOGGER.debug("Cleaned the cache")
