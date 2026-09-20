import os
import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator, Optional, Tuple, Any, Dict

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    CHANNEL_ID,
    TODO_CHANNEL_ID,
    SOFTWARE_CHANNEL_ID,
    OTP_CHANNEL_ID,
    OTP_AUTO_DELETE_SECONDS,
    SESSION_NAME,
    DEMO_STORAGE_DIR,
    UPLOAD_DIR,
    OWNER_ID,
    is_telegram_configured,
)

logger = logging.getLogger("telegram_cloud.client")

class TelegramStorageClient:
    """
    Manages triple-channel communication with Telegram MTProto.
    Channel 1: Files, Photos, and Videos vault (up to 2 GB per file).
    Channel 2: To-Do tasks checklist and quick Text Notes.
    Channel 3: Softwares and Application installers.
    Dedicated Channel: OTP codes and Security Alerts (auto-purged after 3 min).
    """

    def __init__(self):
        self.client = None
        self.is_connected = False
        self.is_demo = not is_telegram_configured()
        self.bot_info = None
        self.channel_entity = None
        self.todo_channel_entity = None
        self.software_channel_entity = None
        self.otp_channel_entity = None
        self.owner_entity = None
        self.owner_id = OWNER_ID
        self._demo_counter = 1000
        self.active_otp_messages: Dict[str, Tuple[Any, int]] = {}

    async def get_software_entity(self):
        """Dynamically resolve or return the software channel entity."""
        if self.is_demo or not self.client:
            return None
        if self.software_channel_entity is not None and self.software_channel_entity != self.channel_entity:
            return self.software_channel_entity
        target_soft_id = SOFTWARE_CHANNEL_ID if SOFTWARE_CHANNEL_ID != 0 else CHANNEL_ID
        if target_soft_id == CHANNEL_ID:
            self.software_channel_entity = self.channel_entity
            return self.channel_entity
        try:
            self.software_channel_entity = await self.client.get_entity(target_soft_id)
            logger.info(f"Software Channel resolved: {getattr(self.software_channel_entity, 'title', target_soft_id)}")
            return self.software_channel_entity
        except Exception as e:
            logger.debug(f"Could not resolve SOFTWARE_CHANNEL_ID ({target_soft_id}): {e}. Make sure bot is added as administrator.")
            return None

    async def get_todo_entity(self):
        """Dynamically resolve or return the todo/notes channel entity."""
        if self.is_demo or not self.client:
            return None
        if self.todo_channel_entity is not None and self.todo_channel_entity != self.channel_entity:
            return self.todo_channel_entity
        target_todo_id = TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID
        if target_todo_id == CHANNEL_ID:
            self.todo_channel_entity = self.channel_entity
            return self.channel_entity
        try:
            self.todo_channel_entity = await self.client.get_entity(target_todo_id)
            logger.info(f"Todo/Notes Channel resolved: {getattr(self.todo_channel_entity, 'title', target_todo_id)}")
            return self.todo_channel_entity
        except Exception as e:
            logger.warning(f"Could not resolve TODO_CHANNEL_ID ({target_todo_id}): {e}. Using storage channel.")
            self.todo_channel_entity = self.channel_entity
            return self.channel_entity

    async def initialize(self) -> None:
        """Start the Telegram MTProto client or fall back to Demo mode."""
        if not is_telegram_configured():
            logger.warning(
                "Telegram credentials incomplete. Running in DEMO MODE (Local simulation)."
            )
            self.is_demo = True
            return

        try:
            from telethon import TelegramClient

            session_file = Path(__file__).resolve().parent / SESSION_NAME
            self.client = TelegramClient(str(session_file), API_ID, API_HASH)
            self.client.flood_sleep_threshold = 60
            await self.client.start(bot_token=BOT_TOKEN)
            
            # Retrieve bot info
            self.bot_info = await self.client.get_me()
            logger.info(f"Connected to Telegram as Bot: @{self.bot_info.username} (ID: {self.bot_info.id})")
            
            # Resolve storage channel (Files/Photos/Videos)
            self.channel_entity = await self.client.get_entity(CHANNEL_ID)
            logger.info(f"Storage Vault Channel resolved: {getattr(self.channel_entity, 'title', CHANNEL_ID)}")

            # Resolve todo/notes channel
            target_todo_id = TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID
            if target_todo_id == CHANNEL_ID:
                self.todo_channel_entity = self.channel_entity
            else:
                try:
                    self.todo_channel_entity = await self.client.get_entity(target_todo_id)
                    logger.info(f"Todo Vault Channel resolved: {getattr(self.todo_channel_entity, 'title', target_todo_id)}")
                except Exception as e:
                    logger.warning(f"Could not resolve separate TODO_CHANNEL_ID ({target_todo_id}): {e}. Using main storage channel.")
                    self.todo_channel_entity = self.channel_entity

            # Resolve software channel
            target_soft_id = SOFTWARE_CHANNEL_ID if SOFTWARE_CHANNEL_ID != 0 else CHANNEL_ID
            if target_soft_id == CHANNEL_ID:
                self.software_channel_entity = self.channel_entity
            else:
                try:
                    self.software_channel_entity = await self.client.get_entity(target_soft_id)
                    logger.info(f"Software Channel resolved: {getattr(self.software_channel_entity, 'title', target_soft_id)}")
                except Exception as e:
                    logger.warning(f"Could not resolve separate SOFTWARE_CHANNEL_ID ({target_soft_id}) at startup: {e}. Bot needs to be added as admin to that channel.")
                    self.software_channel_entity = None

            # Resolve dedicated OTP channel entity
            if OTP_CHANNEL_ID != 0:
                try:
                    self.otp_channel_entity = await self.client.get_entity(OTP_CHANNEL_ID)
                    logger.info(f"Dedicated OTP Channel resolved: {getattr(self.otp_channel_entity, 'title', OTP_CHANNEL_ID)}")
                except Exception as e:
                    logger.warning(f"Could not resolve dedicated OTP_CHANNEL_ID ({OTP_CHANNEL_ID}) at startup: {e}. Bot needs to be added as admin to that channel.")
                    self.otp_channel_entity = None

            # Resolve owner entity if configured
            if self.owner_id != 0:
                try:
                    self.owner_entity = await self.client.get_entity(self.owner_id)
                    logger.info(f"Owner Entity resolved: {getattr(self.owner_entity, 'first_name', self.owner_id)} (ID: {self.owner_id})")
                except Exception as e:
                    logger.warning(f"Could not resolve TELEGRAM_OWNER_ID ({self.owner_id}): {e}. Bot will auto-detect when you message it.")

            self.is_connected = True
            self.is_demo = False

            # Register real-time message listener for all incoming bot events
            from telethon import events
            from database import add_file, detect_category

            @self.client.on(events.NewMessage)
            async def on_new_incoming_message(event):
                try:
                    msg = event.message
                    if not msg:
                        return
                    chat_id = event.chat_id

                    # If this is a private direct message to the bot, auto-detect Owner
                    if event.is_private:
                        sender = await event.get_sender()
                        if sender and not getattr(sender, 'bot', False):
                            self.owner_entity = sender
                            self.owner_id = sender.id
                            logger.info(f"Adopted private chat sender as Owner: {getattr(sender, 'first_name', '')} (ID: {sender.id})")
                            if msg.raw_text and msg.raw_text.strip().startswith("/start"):
                                await event.reply("👋 **Welcome to SS Workspace Vault Bot!**\n\nYour Telegram User ID is registered for one-time login codes (OTP) and real-time security alerts.")

                    is_software = (
                        SOFTWARE_CHANNEL_ID != 0 and (
                            chat_id == SOFTWARE_CHANNEL_ID or
                            abs(chat_id) == abs(SOFTWARE_CHANNEL_ID)
                        )
                    )
                    is_storage = (
                        CHANNEL_ID != 0 and (
                            chat_id == CHANNEL_ID or
                            abs(chat_id) == abs(CHANNEL_ID)
                        )
                    )

                    if is_software and not self.software_channel_entity:
                        try:
                            self.software_channel_entity = await event.get_chat()
                            logger.info(f"Dynamically adopted software channel entity from incoming update: {getattr(self.software_channel_entity, 'title', chat_id)}")
                        except Exception:
                            pass

                    if not (is_software or is_storage):
                        return

                    if msg.media and hasattr(msg.media, "document") and msg.media.document:
                        doc = msg.media.document
                        prefix = "software" if is_software else "file"
                        filename = f"{prefix}_{msg.id}"
                        for attr in doc.attributes:
                            if hasattr(attr, "file_name") and attr.file_name:
                                filename = attr.file_name
                                break

                        cat = "software" if is_software else detect_category(filename, doc.mime_type)
                        target_cid = SOFTWARE_CHANNEL_ID if is_software else CHANNEL_ID
                        await add_file(
                            filename=filename,
                            size=doc.size,
                            mime_type=doc.mime_type,
                            telegram_message_id=msg.id,
                            telegram_channel_id=target_cid,
                            telegram_file_id=str(doc.id),
                            is_demo=False,
                            category=cat
                        )
                        logger.info(f"Real-time file received from Telegram ({cat}): {filename}")
                except Exception as e:
                    logger.error(f"Error handling incoming Telegram message: {e}")

        except Exception as e:
            logger.error(f"Failed to connect to Telegram MTProto: {e}. Falling back to DEMO MODE.")
            self.is_connected = False
            self.is_demo = True

    async def sync_channel_messages(self, max_ids: int = 500) -> int:
        """
        Scan storage channel and software channel for existing files and import them into SQLite catalog.
        Overcomes Telegram bot restrictions by batch querying message IDs.
        """
        if self.is_demo or not self.client:
            return 0

        from database import get_existing_file_message_ids, add_file, detect_category

        imported = 0

        # 1. Sync main storage channel
        if self.channel_entity:
            existing_ids = await get_existing_file_message_ids(CHANNEL_ID)
            for batch_start in range(1, max_ids + 1, 100):
                batch_ids = list(range(batch_start, batch_start + 100))
                try:
                    msgs = await self.client.get_messages(self.channel_entity, ids=batch_ids)
                except Exception as e:
                    logger.error(f"Error fetching storage batch {batch_start}: {e}")
                    break

                any_found = False
                for msg in msgs:
                    if not msg:
                        continue
                    any_found = True
                    if msg.id in existing_ids:
                        continue

                    if msg.media and hasattr(msg.media, "document") and msg.media.document:
                        doc = msg.media.document
                        filename = f"file_{msg.id}"
                        for attr in doc.attributes:
                            if hasattr(attr, "file_name") and attr.file_name:
                                filename = attr.file_name
                                break

                        cat = detect_category(filename, doc.mime_type)
                        await add_file(
                            filename=filename,
                            size=doc.size,
                            mime_type=doc.mime_type,
                            telegram_message_id=msg.id,
                            telegram_channel_id=CHANNEL_ID,
                            telegram_file_id=str(doc.id),
                            is_demo=False,
                            category=cat
                        )
                        existing_ids.add(msg.id)
                        imported += 1

                if not any_found and batch_start > 150:
                    break

        # 2. Sync Software channel if distinct
        soft_entity = await self.get_software_entity()
        if soft_entity and soft_entity != self.channel_entity:
            existing_soft_ids = await get_existing_file_message_ids(SOFTWARE_CHANNEL_ID)
            for batch_start in range(1, max_ids + 1, 100):
                batch_ids = list(range(batch_start, batch_start + 100))
                try:
                    msgs = await self.client.get_messages(soft_entity, ids=batch_ids)
                except Exception as e:
                    logger.error(f"Error fetching software batch {batch_start}: {e}")
                    break

                any_found = False
                for msg in msgs:
                    if not msg:
                        continue
                    any_found = True
                    if msg.id in existing_soft_ids:
                        continue

                    if msg.media and hasattr(msg.media, "document") and msg.media.document:
                        doc = msg.media.document
                        filename = f"software_{msg.id}"
                        for attr in doc.attributes:
                            if hasattr(attr, "file_name") and attr.file_name:
                                filename = attr.file_name
                                break

                        await add_file(
                            filename=filename,
                            size=doc.size,
                            mime_type=doc.mime_type,
                            telegram_message_id=msg.id,
                            telegram_channel_id=SOFTWARE_CHANNEL_ID,
                            telegram_file_id=str(doc.id),
                            is_demo=False,
                            category="software"
                        )
                        existing_soft_ids.add(msg.id)
                        imported += 1

                if not any_found and batch_start > 150:
                    break

        logger.info(f"Channel sync completed: {imported} new files imported.")
        return imported

    # ==================== FILES, PHOTOS, VIDEOS ====================

    async def fast_upload_file(
        self,
        file_path: Path,
        filename: str,
        progress_callback=None,
        max_workers: int = 6
    ):
        """
        High-performance parallel MTProto upload engine using Telegram's upload.saveBigFilePart
        specification. Distributes chunks across concurrent coroutine workers to eliminate
        single-threaded round-trip network latency.
        """
        from telethon import functions, types, helpers

        file_path = Path(file_path).resolve()
        # Defensive boundary check: file must reside in an approved directory
        upload_dir_r = UPLOAD_DIR.resolve()
        demo_dir_r = DEMO_STORAGE_DIR.resolve()
        if not (file_path.is_relative_to(upload_dir_r) or file_path.is_relative_to(demo_dir_r)):
            raise ValueError(f"Path traversal detected: file_path '{file_path}' is outside approved directories.")
        file_size = os.path.getsize(file_path)
        part_size = 512 * 1024  # 512 KB chunks (Telegram MTProto maximum)
        part_count = (file_size + part_size - 1) // part_size
        file_id = helpers.generate_random_long(signed=True)

        queue = asyncio.Queue()
        for i in range(part_count):
            queue.put_nowait(i)

        uploaded_parts = 0
        uploaded_lock = asyncio.Lock()

        async def worker(worker_id: int):
            nonlocal uploaded_parts
            with open(file_path, "rb") as f:
                while True:
                    try:
                        part_index = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    offset = part_index * part_size
                    f.seek(offset)
                    read_len = min(part_size, file_size - offset)
                    chunk = f.read(read_len)

                    # Retry up to 3 times on transient network blips
                    success = False
                    for attempt in range(3):
                        try:
                            request = functions.upload.SaveBigFilePartRequest(
                                file_id=file_id,
                                file_part=part_index,
                                file_total_parts=part_count,
                                bytes=chunk
                            )
                            result = await self.client(request)
                            if result:
                                success = True
                                break
                            logger.warning(f"Worker {worker_id}: Part {part_index} returned False, retrying...")
                        except Exception as exc:
                            logger.warning(f"Worker {worker_id}: Part {part_index} attempt {attempt + 1} failed: {exc}")
                            if attempt == 2:
                                raise
                            await asyncio.sleep(0.5 * (attempt + 1))

                    if not success:
                        raise RuntimeError(f"Failed to upload part {part_index} after 3 attempts")

                    queue.task_done()

                    async with uploaded_lock:
                        uploaded_parts += 1
                        if progress_callback:
                            current_bytes = min(uploaded_parts * part_size, file_size)
                            try:
                                if asyncio.iscoroutinefunction(progress_callback):
                                    await progress_callback(current_bytes, file_size)
                                else:
                                    progress_callback(current_bytes, file_size)
                            except Exception as cb_err:
                                logger.debug(f"Progress callback error: {cb_err}")

        actual_workers = min(max_workers, part_count)
        logger.info(f"Starting parallel upload: {filename} ({part_count} parts, {actual_workers} workers)")
        tasks = [asyncio.create_task(worker(i)) for i in range(actual_workers)]
        await asyncio.gather(*tasks)

        logger.info(f"Parallel upload completed: {filename} ({part_count}/{part_count} parts)")
        return types.InputFileBig(id=file_id, parts=part_count, name=filename)

    async def upload_file(
        self,
        file_path: Path,
        filename: str,
        category: str = "file",
        progress_callback=None
    ) -> Tuple[int, int, Optional[str]]:
        """Uploads a file directly into the Telegram storage channel as a message with document attachment."""
        # Defensive boundary check: file_path must resolve within approved directories
        file_path = Path(file_path).resolve()
        upload_dir_r = UPLOAD_DIR.resolve()
        demo_dir_r = DEMO_STORAGE_DIR.resolve()
        if not (file_path.is_relative_to(upload_dir_r) or file_path.is_relative_to(demo_dir_r)):
            raise ValueError(f"Path traversal detected in upload_file: '{file_path}'")
        target_channel_id = SOFTWARE_CHANNEL_ID if (category == "software" and SOFTWARE_CHANNEL_ID != 0) else CHANNEL_ID

        if self.is_demo or not self.client:
            self._demo_counter += 1
            demo_msg_id = self._demo_counter
            clean_name = Path(filename).name
            demo_dest = (DEMO_STORAGE_DIR / f"{demo_msg_id}_{clean_name}").resolve()
            if not demo_dest.is_relative_to(DEMO_STORAGE_DIR.resolve()):
                raise ValueError("Path traversal attempt detected in filename.")
            total_size = os.path.getsize(file_path)
            copied = 0
            with open(file_path, "rb") as src, open(demo_dest, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    copied += len(chunk)
                    if progress_callback:
                        progress_callback(copied, total_size)
            return demo_msg_id, target_channel_id or -1009999999999, f"demo_file_{demo_msg_id}"

        from telethon.tl.types import DocumentAttributeFilename

        icon = "💻" if category == "software" else ("🖼️" if category == "photo" else ("🎬" if category == "video" else "📁"))
        caption = f"{icon} **{category.title()}:** `{filename}`\n💾 **Size:** {os.path.getsize(file_path):,} bytes"

        # Determine target entity and destination channel ID
        target_entity = self.channel_entity
        target_channel_id = CHANNEL_ID
        if category == "software" and SOFTWARE_CHANNEL_ID != 0:
            soft_entity = await self.get_software_entity()
            if soft_entity:
                target_entity = soft_entity
                target_channel_id = SOFTWARE_CHANNEL_ID
            else:
                logger.warning("Software channel entity could not be resolved; routing upload to primary storage channel.")
                target_entity = self.channel_entity
                target_channel_id = CHANNEL_ID

        file_size = os.path.getsize(file_path)
        is_big = file_size > 10 * 1024 * 1024

        # For files > 10 MB, use parallel MTProto multi-worker engine for maximum throughput
        if is_big:
            logger.info(f"Using Parallel MTProto Multi-Worker Engine for {filename} ({file_size:,} bytes)")
            uploaded_file = await self.fast_upload_file(
                file_path=file_path,
                filename=filename,
                progress_callback=progress_callback,
                max_workers=6
            )
        else:
            uploaded_file = await self.client.upload_file(
                file=str(file_path),
                part_size_kb=512,
                file_name=filename,
                progress_callback=progress_callback
            )

        message = await self.client.send_file(
            entity=target_entity,
            file=uploaded_file,
            caption=caption,
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name=filename)]
        )

        tg_file_id = None
        if message.media and hasattr(message.media, "document"):
            tg_file_id = str(message.media.document.id)

        return message.id, target_channel_id, tg_file_id

    async def fast_download_stream(
        self,
        media: Any,
        file_size: int,
        max_workers: int = 6,
        part_size: int = 512 * 1024
    ) -> AsyncGenerator[bytes, None]:
        """
        High-performance parallel MTProto streaming download engine using Telegram's upload.getFile
        specification. Employs 6 concurrent coroutine workers with a bounded sliding window buffer
        to deliver chunks in strict sequential order without high memory usage.
        """
        from telethon import functions, utils

        info = utils._get_file_info(media)
        location = info.location
        dc_id = info.dc_id
        actual_size = file_size or getattr(info, "size", 0) or 0
        if actual_size <= 0:
            async for chunk in self.client.iter_download(media, chunk_size=part_size):
                yield chunk
            return

        part_count = (actual_size + part_size - 1) // part_size
        actual_workers = min(max_workers, part_count)

        # Resolve sender for the target DC if different
        sender = self.client._sender
        if dc_id and self.client.session.dc_id != dc_id:
            try:
                sender = await self.client._borrow_exported_sender(dc_id)
            except Exception as e:
                logger.warning(f"Could not borrow sender for DC {dc_id}: {e}. Using default sender.")
                sender = self.client._sender

        # Bounded queue limits in-flight prefetching to prevent RAM buildup (at most 12 chunks ahead)
        queue = asyncio.Queue(maxsize=actual_workers * 2)
        completed = {}
        condition = asyncio.Condition()
        worker_error = None

        async def feeder():
            for i in range(part_count):
                await queue.put(i)

        async def worker(worker_id: int):
            nonlocal worker_error
            while True:
                try:
                    idx = await queue.get()
                except asyncio.CancelledError:
                    break

                offset = idx * part_size
                req = functions.upload.GetFileRequest(
                    location=location,
                    offset=offset,
                    limit=part_size
                )
                chunk = None
                for attempt in range(3):
                    try:
                        res = await self.client._call(sender, req)
                        chunk = res.bytes
                        break
                    except Exception as exc:
                        logger.warning(f"Download worker {worker_id}: Part {idx} attempt {attempt+1} failed: {exc}")
                        if attempt == 2:
                            worker_error = exc
                            async with condition:
                                condition.notify_all()
                            return
                        await asyncio.sleep(0.5 * (attempt + 1))

                async with condition:
                    completed[idx] = chunk
                    condition.notify_all()
                queue.task_done()

        feeder_task = asyncio.create_task(feeder())
        worker_tasks = [asyncio.create_task(worker(i)) for i in range(actual_workers)]
        all_tasks = [feeder_task] + worker_tasks

        try:
            for current_part in range(part_count):
                async with condition:
                    while current_part not in completed and not worker_error:
                        await condition.wait()
                    if worker_error:
                        raise worker_error
                    chunk = completed.pop(current_part)
                yield chunk
        finally:
            for t in all_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)

    async def download_file_stream(
        self,
        telegram_message_id: int,
        filename: str,
        telegram_channel_id: Optional[int] = None,
        is_demo: bool = False
    ) -> AsyncGenerator[bytes, None]:
        """Stream chunks directly from Telegram message."""
        if is_demo or self.is_demo or not self.client:
            clean_name = Path(filename).name
            demo_path = (DEMO_STORAGE_DIR / f"{telegram_message_id}_{clean_name}").resolve()
            if not demo_path.is_relative_to(DEMO_STORAGE_DIR.resolve()):
                raise ValueError("Path traversal attempt detected in filename.")
            if not demo_path.exists():
                raise FileNotFoundError(f"File {filename} not found in demo storage.")
            with open(demo_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
                    await asyncio.sleep(0)
            return

        target_entity = self.channel_entity
        if telegram_channel_id:
            if telegram_channel_id == SOFTWARE_CHANNEL_ID and SOFTWARE_CHANNEL_ID != 0:
                soft_entity = await self.get_software_entity()
                target_entity = soft_entity or self.channel_entity
            elif telegram_channel_id == TODO_CHANNEL_ID and self.todo_channel_entity:
                target_entity = self.todo_channel_entity
            else:
                try:
                    target_entity = await self.client.get_entity(telegram_channel_id)
                except Exception:
                    target_entity = self.channel_entity

        message = await self.client.get_messages(target_entity, ids=telegram_message_id)
        if not message or not message.media:
            raise FileNotFoundError(f"Telegram message {telegram_message_id} media not found.")

        doc_size = 0
        if hasattr(message.media, "document") and message.media.document:
            doc_size = getattr(message.media.document, "size", 0)

        is_big = doc_size > 10 * 1024 * 1024

        # For files > 10 MB, stream via parallel 6-worker MTProto engine
        if is_big:
            logger.info(f"Parallel MTProto download: {filename} ({doc_size:,} bytes) using 6 concurrent workers")
            async for chunk in self.fast_download_stream(message.media, file_size=doc_size, max_workers=6):
                yield chunk
        else:
            async for chunk in self.client.iter_download(message.media, chunk_size=1024 * 1024):
                yield chunk

    # ==================== TODOS & NOTES (CHANNEL 2) ====================

    async def send_todo_message(self, title: str) -> Tuple[int, int]:
        """Send a new todo task to the Todo channel."""
        if self.is_demo or not self.client or not self.is_connected:
            self._demo_counter += 1
            return self._demo_counter, TODO_CHANNEL_ID or -1009999999998

        entity = await self.get_todo_entity() or self.channel_entity
        if not entity:
            self._demo_counter += 1
            return self._demo_counter, TODO_CHANNEL_ID or -1009999999998

        try:
            msg = await self.client.send_message(
                entity=entity,
                message=f"⏳ **[TODO]** {title}"
            )
            return msg.id, (TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID)
        except Exception as e:
            logger.error(f"Failed to send todo message to Telegram: {e}")
            self._demo_counter += 1
            return self._demo_counter, TODO_CHANNEL_ID or -1009999999998

    async def update_todo_message(self, telegram_message_id: int, title: str, completed: bool) -> None:
        """Edit todo message in Telegram channel to reflect checkmark."""
        if self.is_demo or not self.client or not self.is_connected:
            return
        entity = await self.get_todo_entity() or self.channel_entity
        if not entity:
            return
        try:
            status_text = "✅ **[COMPLETED]**" if completed else "⏳ **[TODO]**"
            await self.client.edit_message(
                entity=entity,
                message=telegram_message_id,
                text=f"{status_text} ~{title}~" if completed else f"{status_text} {title}"
            )
        except Exception as e:
            logger.error(f"Failed to edit todo message {telegram_message_id}: {e}")

    async def send_note_message(self, content: str) -> Tuple[int, int]:
        """Send a quick text note to the Todo/Notes channel."""
        if self.is_demo or not self.client or not self.is_connected:
            self._demo_counter += 1
            return self._demo_counter, TODO_CHANNEL_ID or -1009999999998

        entity = await self.get_todo_entity() or self.channel_entity
        if not entity:
            logger.warning("No channel entity available for note. Saving locally.")
            self._demo_counter += 1
            return self._demo_counter, TODO_CHANNEL_ID or -1009999999998

        try:
            msg = await self.client.send_message(
                entity=entity,
                message=f"📝 **[NOTE]**\n\n{content}"
            )
            return msg.id, (TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID)
        except Exception as e:
            logger.error(f"Failed to send note message to Telegram: {e}")
            self._demo_counter += 1
            return self._demo_counter, TODO_CHANNEL_ID or -1009999999998

    async def delete_message(self, channel_id: int, message_id: int, is_demo: bool = False, filename: Optional[str] = None) -> bool:
        """Deletes any message from Telegram (Storage, Software, or Todo channel)."""
        if is_demo or self.is_demo or not self.client:
            if filename:
                clean_name = Path(filename).name
                demo_path = (DEMO_STORAGE_DIR / f"{message_id}_{clean_name}").resolve()
                if demo_path.is_relative_to(DEMO_STORAGE_DIR.resolve()) and demo_path.exists():
                    try:
                        demo_path.unlink()
                    except Exception:
                        pass
            return True

        if channel_id == SOFTWARE_CHANNEL_ID and SOFTWARE_CHANNEL_ID != 0:
            soft_entity = await self.get_software_entity()
            if not soft_entity:
                logger.error(f"Cannot delete message {message_id}: software channel entity could not be resolved. Failing closed to prevent accidental cross-channel deletion.")
                return False
            entity = soft_entity
        elif channel_id == TODO_CHANNEL_ID and TODO_CHANNEL_ID != 0:
            if not self.todo_channel_entity and not self.channel_entity:
                return False
            entity = self.todo_channel_entity or self.channel_entity
        else:
            if not self.channel_entity:
                return False
            entity = self.channel_entity

        try:
            await self.client.delete_messages(entity, message_ids=[message_id])
            return True
        except Exception as e:
            logger.error(f"Failed to delete message {message_id} from channel {channel_id}: {e}")
            return False

    # ==================== OTP AUTH & SECURITY ALERTS ====================

    async def send_otp_to_owner(self, code: str, ip: str, expiry_seconds: int = 60, context_info: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Send a 6-digit login OTP directly to the dedicated security channel, specifying tab & action."""
        tab_name = (context_info or {}).get("tab") or "Workspace Vault"
        action = (context_info or {}).get("action") or "access"
        target_name = (context_info or {}).get("target_name")

        if self.is_demo or not self.client:
            logger.info(f"[DEMO OTP] Verification code generated for {ip} (Tab: {tab_name})")
            return True, "Demo Mode: Verification session created."

        # If there is already an active OTP message for this IP, purge it from Telegram immediately
        await self.cancel_active_otp(ip)

        # 1. Dedicated OTP Channel
        target_entity = self.otp_channel_entity
        if not target_entity and OTP_CHANNEL_ID != 0:
            try:
                self.otp_channel_entity = await self.client.get_entity(OTP_CHANNEL_ID)
                target_entity = self.otp_channel_entity
            except Exception as e:
                logger.warning(f"Could not dynamically resolve OTP_CHANNEL_ID: {e}")

        # Fallbacks if dedicated channel entity is unresolved
        if not target_entity:
            target_entity = self.owner_entity or self.todo_channel_entity or self.channel_entity

        if not target_entity:
            return False, "Could not reach Telegram destination for OTP."

        # Build contextual details
        details_lines = [
            f"📂 **Target Tab:** `{tab_name}`"
        ]
        if action == "delete_file" or target_name:
            details_lines.append("⚠️ **Action:** 🗑️ Removing Item")
            if target_name:
                details_lines.append(f"📄 **Item Being Removed:** `{target_name}`")
        else:
            details_lines.append("🔑 **Action:** Tab Security Access")

        details_block = "\n".join(details_lines)

        try:
            msg = await self.client.send_message(
                entity=target_entity,
                message=(
                    f"🔐 **[SS WORKSPACE LOGIN OTP]**\n\n"
                    f"🔑 **Code:** `{code}`\n"
                    f"⏳ **Valid for:** {expiry_seconds} seconds\n"
                    f"🌐 **Client IP:** `{ip}`\n"
                    f"{details_block}\n"
                    f"🕒 **Auto-Deletes in:** 3 minutes\n\n"
                    f"🛡️ _SS Workspace Security Shield_"
                )
            )

            # Record active message so it can be purged immediately if cancelled or cross-tab switch occurs
            self.active_otp_messages[ip] = (target_entity, msg.id)

            # Auto-remove the OTP message from Telegram after 3 minutes (180 seconds)
            async def _auto_delete_otp(entity, message_id, delay=OTP_AUTO_DELETE_SECONDS):
                await asyncio.sleep(delay)
                try:
                    await self.client.delete_messages(entity, message_ids=[message_id])
                    logger.info(f"Auto-deleted OTP message {message_id} from security channel after {delay}s")
                except Exception as e:
                    logger.debug(f"Could not auto-delete OTP message {message_id}: {e}")
                finally:
                    if ip in self.active_otp_messages and self.active_otp_messages[ip][1] == message_id:
                        self.active_otp_messages.pop(ip, None)

            asyncio.create_task(_auto_delete_otp(target_entity, msg.id, delay=OTP_AUTO_DELETE_SECONDS))

            return True, "OTP dispatched to security channel (auto-deletes in 3 minutes)."
        except Exception as e:
            logger.error(f"Failed to send OTP to security channel: {e}")
            return False, f"Failed to deliver OTP: {str(e)}"

    async def cancel_active_otp(self, ip: str) -> bool:
        """Immediately deletes the active OTP message from the Telegram channel upon cancellation or tab switch."""
        if self.is_demo or not self.client:
            return False
        if ip in self.active_otp_messages:
            target_entity, msg_id = self.active_otp_messages.pop(ip, (None, None))
            if target_entity and msg_id:
                try:
                    await self.client.delete_messages(target_entity, message_ids=[msg_id])
                    logger.info(f"Cancelled and purged active OTP message {msg_id} from Telegram channel for {ip}")
                    return True
                except Exception as e:
                    logger.debug(f"Could not purge active OTP message {msg_id}: {e}")
        return False

    async def send_security_alert(self, ip: str, failed_count: int, lockout_minutes: int = 10) -> bool:
        """Send an urgent security alert regarding failed OTP attempts and IP lockout (Option 3)."""
        alert_msg = (
            f"🚨 **[SECURITY ALERT — BRUTE FORCE DETECTED]**\n\n"
            f"⚠️ **{failed_count} consecutive failed OTP attempts** detected!\n"
            f"🌐 **Client IP:** `{ip}`\n"
            f"⛔ **Protection Triggered:** IP has been **LOCKED OUT for {lockout_minutes} minutes**.\n\n"
            f"🛡️ _SS Workspace Security Shield_"
        )

        if self.is_demo or not self.client:
            logger.warning(f"[DEMO SECURITY ALERT] {alert_msg}")
            return True

        target_entity = self.otp_channel_entity or self.owner_entity or self.todo_channel_entity or self.channel_entity
        if not target_entity and OTP_CHANNEL_ID != 0:
            try:
                target_entity = await self.client.get_entity(OTP_CHANNEL_ID)
            except Exception:
                pass

        if not target_entity:
            logger.warning("No target entity available for security alert.")
            return False

        try:
            await self.client.send_message(entity=target_entity, message=alert_msg)
            return True
        except Exception as e:
            logger.error(f"Failed to dispatch security alert: {e}")
            return False

    async def close(self) -> None:
        """Close the Telethon client connection."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            self.is_connected = False

storage_client = TelegramStorageClient()
