import os
import re
import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator, Optional, Tuple, Any, Dict, List

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

# Multi-GB Automatic Encryption & Chunking Thresholds
# Files <= 1.99 GB are uploaded as a single unified document without splitting.
# Split logic activates only for files >= 2.0 GB.
PIXEL_VAULT_CHUNK_THRESHOLD = 1990 * 1024 * 1024  # 1.99 GB threshold
PIXEL_VAULT_CHUNK_SIZE = 1900 * 1024 * 1024       # 1.90 GB per chunk for multi-part files


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
        self.clients = []
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
        self.has_aes_ni = False
        self.cryptg_version = None
        try:
            import cryptg
            self.has_aes_ni = True
            self.cryptg_version = getattr(cryptg, "__version__", "detected")
        except ImportError:
            self.has_aes_ni = False
        except Exception:
            self.has_aes_ni = False

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
            from config import BOT_TOKENS

            session_file = Path(__file__).resolve().parent / SESSION_NAME
            
            # Initialize Bot Fleet (Connection Pool)
            self.clients = []
            from telethon.sessions import MemorySession
            
            for i, token in enumerate(BOT_TOKENS):
                bot = TelegramClient(MemorySession(), API_ID, API_HASH)
                bot.flood_sleep_threshold = 60
                await bot.start(bot_token=token)
                # Force Telethon to fetch dialogs to cache the private channel access_hash
                try:
                    await bot.get_dialogs(limit=20)
                except Exception as e:
                    logger.warning(f"Failed to fetch dialogs for bot {i}: {e}")
                self.clients.append(bot)

            self.client = self.clients[0] if self.clients else None
            if not self.client:
                raise ValueError("No bot tokens provided.")
                
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

            if self.has_aes_ni:
                logger.info(f"⚡ AES-NI Hardware Acceleration (cryptg v{self.cryptg_version}) is ACTIVE for MTProto transfers.")
            else:
                logger.info("ℹ️ cryptg extension not detected; using standard cryptographic fallback for MTProto.")

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
                    is_todo_ch = (
                        TODO_CHANNEL_ID != 0 and (
                            chat_id == TODO_CHANNEL_ID or
                            abs(chat_id) == abs(TODO_CHANNEL_ID)
                        )
                    )

                    if is_software and not self.software_channel_entity:
                        try:
                            self.software_channel_entity = await event.get_chat()
                            logger.info(f"Dynamically adopted software channel entity from incoming update: {getattr(self.software_channel_entity, 'title', chat_id)}")
                        except Exception:
                            pass

                    if is_todo_ch and not self.todo_channel_entity:
                        try:
                            self.todo_channel_entity = await event.get_chat()
                            logger.info(f"Dynamically adopted todo/notes channel entity from incoming update: {getattr(self.todo_channel_entity, 'title', chat_id)}")
                        except Exception:
                            pass

                    if is_todo_ch and msg.text:
                        from database import get_existing_note_message_ids, get_existing_todo_message_ids, add_note, add_todo
                        raw_text = msg.text.strip()
                        date_str = msg.date.isoformat() if hasattr(msg, 'date') and msg.date else None
                        target_cid = TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID

                        if raw_text.startswith("⏳ **[TODO]**") or raw_text.startswith("⏳ [TODO]"):
                            existing_todo_ids = await get_existing_todo_message_ids()
                            if msg.id not in existing_todo_ids:
                                clean_title = raw_text.replace("⏳ **[TODO]**", "").replace("⏳ [TODO]", "").strip()
                                await add_todo(
                                    title=clean_title,
                                    telegram_message_id=msg.id,
                                    telegram_channel_id=target_cid,
                                    is_demo=False,
                                    created_at=date_str,
                                    completed=False
                                )
                                logger.info(f"Real-time todo received from Telegram: {clean_title}")
                        elif raw_text.startswith("✅ **[COMPLETED]**") or raw_text.startswith("✅ [COMPLETED]"):
                            existing_todo_ids = await get_existing_todo_message_ids()
                            if msg.id not in existing_todo_ids:
                                clean_title = raw_text.replace("✅ **[COMPLETED]**", "").replace("✅ [COMPLETED]", "").replace("~", "").strip()
                                await add_todo(
                                    title=clean_title,
                                    telegram_message_id=msg.id,
                                    telegram_channel_id=target_cid,
                                    is_demo=False,
                                    created_at=date_str,
                                    completed=True
                                )
                                logger.info(f"Real-time completed todo received from Telegram: {clean_title}")
                        else:
                            existing_note_ids = await get_existing_note_message_ids()
                            if msg.id not in existing_note_ids:
                                from database import append_note_chunk

                                # Check if message is a continuation chunk (via [cont:<id>] tag or direct reply)
                                cont_match = re.search(r'\[cont:(\d+)\]', raw_text)
                                parent_id = int(cont_match.group(1)) if cont_match else getattr(msg, 'reply_to_msg_id', None)

                                # Strip prefix including (Part X/Y) and [cont:Z]
                                clean_content = re.sub(r'^📝\s*(\*\*\[NOTE\]\*\*|\[NOTE\])(\s*\(Part\s*\d+/\d+\))?(\s*\[cont:\d+\])?\s*\n*', '', raw_text).strip()

                                if parent_id:
                                    merged = await append_note_chunk(parent_id, msg.id, clean_content)
                                    if merged:
                                        logger.info(f"Merged continuation note chunk {msg.id} into parent {parent_id}")
                                        return

                                await add_note(
                                    content=clean_content,
                                    telegram_message_id=msg.id,
                                    telegram_channel_id=target_cid,
                                    is_demo=False,
                                    created_at=date_str
                                )
                                logger.info(f"Real-time note received from Telegram: {clean_content[:50]}")
                        return

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

                        raw_caption = getattr(msg, "text", "") or getattr(msg, "message", "") or ""
                        if "[PIXEL_VAULT_CHUNK]" in raw_caption or filename.startswith("pv_chunk_"):
                            return  # Internal encrypted chunk: hidden from UI

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
                    elif is_storage and msg.media and hasattr(msg.media, "photo") and msg.media.photo:
                        photo = msg.media.photo
                        photo_size = 0
                        if hasattr(photo, "sizes") and photo.sizes:
                            for s in photo.sizes:
                                if hasattr(s, "size"):
                                    photo_size = max(photo_size, s.size)
                        filename = f"photo_{msg.id}.jpg"
                        await add_file(
                            filename=filename,
                            size=photo_size,
                            mime_type="image/jpeg",
                            telegram_message_id=msg.id,
                            telegram_channel_id=CHANNEL_ID,
                            telegram_file_id=str(photo.id),
                            is_demo=False,
                            category="photo"
                        )
                        logger.info(f"Real-time photo received from Telegram: {filename}")
                except Exception as e:
                    logger.error(f"Error handling incoming Telegram message: {e}")

            @self.client.on(events.MessageDeleted)
            async def on_messages_deleted(event):
                try:
                    deleted_ids = getattr(event, 'deleted_ids', None) or []
                    if deleted_ids:
                        await self.handle_deleted_messages(deleted_ids)
                except Exception as e:
                    logger.error(f"Error handling MessageDeleted event: {e}")

        except Exception as e:
            logger.error(f"Failed to connect to Telegram MTProto: {e}. Falling back to DEMO MODE.")
            self.is_connected = False
            self.is_demo = True

    async def handle_deleted_messages(self, deleted_ids: List[int]) -> int:
        """
        Processes message deletions from Telegram:
        1. If a note or any of its multi-part chunks was deleted on Telegram, delete the note from DB
           and cascade-delete all remaining chunks from Telegram so no orphan parts remain.
        2. If a todo was deleted on Telegram, delete the todo from DB.
        3. If a file was deleted on Telegram, delete the file from DB.
        """
        from database import find_note_by_message_id, delete_note, find_todo_by_message_id, delete_todo, find_file_by_message_id, delete_file
        todo_entity = await self.get_todo_entity()
        affected = 0

        for mid in deleted_ids:
            try:
                # 1. Check note
                note = await find_note_by_message_id(mid)
                if note:
                    logger.info(f"Telegram deletion detected for message {mid} belonging to note {note['id']}")
                    await delete_note(note["id"])
                    affected += 1

                    # Cascade delete remaining parts from Telegram
                    all_parts = [note["telegram_message_id"]] + note.get("extra_message_ids", [])
                    remaining_parts = [p for p in all_parts if p and p not in deleted_ids]
                    if remaining_parts and todo_entity and self.client and not note.get("is_demo"):
                        try:
                            await self.client.delete_messages(todo_entity, message_ids=remaining_parts)
                            logger.info(f"Cascade deleted remaining note parts {remaining_parts} from Telegram")
                        except Exception as cascade_err:
                            logger.error(f"Failed to cascade delete remaining note parts {remaining_parts}: {cascade_err}")

                # 2. Check todo
                todo = await find_todo_by_message_id(mid)
                if todo:
                    logger.info(f"Telegram deletion detected for todo message {mid} ({todo['title']})")
                    await delete_todo(todo["id"])
                    affected += 1

                # 3. Check file
                file_rec = await find_file_by_message_id(mid)
                if file_rec:
                    logger.info(f"Telegram deletion detected for file {file_rec['filename']} (message {mid})")
                    await delete_file(file_rec["id"])
                    affected += 1
            except Exception as item_err:
                logger.error(f"Error handling Telegram deletion for message {mid}: {item_err}")

        return affected

    async def reconcile_active_notes_and_todos(self) -> int:
        """
        Active polling reconciliation to catch manual Telegram deletions that Telegram
        does not push to bots via real-time update events.
        """
        if self.is_demo or not self.client or not self.is_connected:
            return 0

        todo_entity = await self.get_todo_entity()
        if not todo_entity:
            return 0

        from database import get_notes, delete_note, get_todos, delete_todo
        purged = 0

        try:
            active_notes = await get_notes()
            for note in active_notes:
                if note.get("is_demo"):
                    continue
                all_parts = [note["telegram_message_id"]] + note.get("extra_message_ids", [])
                valid_parts = [p for p in all_parts if p]
                if not valid_parts:
                    continue

                check_msgs = await self.client.get_messages(todo_entity, ids=valid_parts)
                deleted_parts = []
                existing_parts = []
                for p_id, m in zip(valid_parts, check_msgs):
                    if not m or getattr(m, 'empty', False):
                        deleted_parts.append(p_id)
                    else:
                        existing_parts.append(p_id)

                if deleted_parts:
                    logger.info(f"Reconciliation detected deleted part(s) {deleted_parts} for note {note['id']}")
                    await delete_note(note["id"])
                    purged += 1
                    if existing_parts:
                        try:
                            await self.client.delete_messages(todo_entity, message_ids=existing_parts)
                            logger.info(f"Purged remaining note parts {existing_parts} from Telegram")
                        except Exception as del_err:
                            logger.error(f"Error purging remaining parts {existing_parts}: {del_err}")

            active_todos = await get_todos()
            for todo in active_todos:
                if todo.get("is_demo") or not todo.get("telegram_message_id"):
                    continue
                td_msg = await self.client.get_messages(todo_entity, ids=todo["telegram_message_id"])
                if not td_msg or getattr(td_msg, 'empty', False):
                    logger.info(f"Reconciliation detected deleted todo message {todo['telegram_message_id']}")
                    await delete_todo(todo["id"])
                    purged += 1

        except Exception as e:
            logger.error(f"Error during active notes/todos deletion reconciliation: {e}")

        return purged

    async def reconcile_active_files(self) -> int:
        """
        Active polling reconciliation to catch manual Telegram deletions of files
        that Telegram does not push to bots via real-time update events.
        """
        if self.is_demo or not self.client or not self.is_connected:
            return 0

        from database import get_files, delete_file
        purged = 0

        try:
            active_files = await get_files()
            channel_files = []
            software_files = []
            for f in active_files:
                if f.get("is_demo") or not f.get("telegram_message_id"):
                    continue
                cid = f.get("telegram_channel_id")
                if cid == SOFTWARE_CHANNEL_ID and self.software_channel_entity:
                    software_files.append(f)
                elif self.channel_entity:
                    channel_files.append(f)

            for entity, file_list in [(self.channel_entity, channel_files), (self.software_channel_entity, software_files)]:
                if not entity or not file_list:
                    continue
                for i in range(0, len(file_list), 50):
                    chunk = file_list[i:i+50]
                    ids = [x["telegram_message_id"] for x in chunk]
                    try:
                        msgs = await self.client.get_messages(entity, ids=ids)
                        msg_map = {m.id: m for m in msgs if m}
                        for f in chunk:
                            mid = f["telegram_message_id"]
                            msg = msg_map.get(mid)
                            if not msg or getattr(msg, 'empty', False):
                                logger.info(f"Reconciliation detected deleted Telegram file message {mid} for '{f['filename']}'")
                                await delete_file(f["id"])
                                purged += 1
                    except Exception as batch_err:
                        logger.debug(f"Failed to batch check messages for reconciliation: {batch_err}")
        except Exception as e:
            logger.error(f"Error during active files deletion reconciliation: {e}")

        return purged

    async def sync_channel_messages(self, max_ids: int = 500) -> int:
        """
        Scan storage channel and software channel for existing files and import them into SQLite catalog.
        Also cleans up any deleted items in Telegram.
        """
        if self.is_demo or not self.client:
            return 0

        from database import get_existing_file_message_ids, add_file, detect_category

        # Reconcile deleted files first
        try:
            await self.reconcile_active_files()
        except Exception as e:
            logger.debug(f"Pre-sync file reconciliation error: {e}")

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
                    if not msg or getattr(msg, 'empty', False):
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
                        raw_caption = getattr(msg, "text", "") or getattr(msg, "message", "") or ""
                        if "[PIXEL_VAULT_CHUNK]" in raw_caption or filename.startswith("pv_chunk_"):
                            continue  # Internal encrypted chunk: hidden from UI

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
                    elif msg.media and hasattr(msg.media, "photo") and msg.media.photo:
                        photo = msg.media.photo
                        photo_size = 0
                        if hasattr(photo, "sizes") and photo.sizes:
                            for s in photo.sizes:
                                if hasattr(s, "size"):
                                    photo_size = max(photo_size, s.size)
                        filename = f"photo_{msg.id}.jpg"
                        await add_file(
                            filename=filename,
                            size=photo_size,
                            mime_type="image/jpeg",
                            telegram_message_id=msg.id,
                            telegram_channel_id=CHANNEL_ID,
                            telegram_file_id=str(photo.id),
                            is_demo=False,
                            category="photo"
                        )
                        existing_ids.add(msg.id)
                        imported += 1

                if not any_found and batch_start > 200:
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

                        raw_caption = getattr(msg, "text", "") or getattr(msg, "message", "") or ""
                        if "[PIXEL_VAULT_CHUNK]" in raw_caption or filename.startswith("pv_chunk_"):
                            continue  # Internal encrypted chunk: hidden from UI

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

        # 3. Sync Todo & Notes channel (Channel 2)
        todo_entity = await self.get_todo_entity()
        target_todo_id = TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID
        if todo_entity:
            from database import get_existing_note_message_ids, get_existing_todo_message_ids, add_note, add_todo
            existing_note_ids = await get_existing_note_message_ids()
            existing_todo_ids = await get_existing_todo_message_ids()

            for batch_start in range(1, max_ids + 1, 100):
                batch_ids = list(range(batch_start, batch_start + 100))
                try:
                    msgs = await self.client.get_messages(todo_entity, ids=batch_ids)
                except Exception as e:
                    logger.error(f"Error fetching todo/notes batch {batch_start}: {e}")
                    break

                any_found = False
                for msg in msgs:
                    if not msg or not msg.text:
                        continue
                    any_found = True
                    if msg.id in existing_note_ids or msg.id in existing_todo_ids:
                        continue

                    raw_text = msg.text.strip()
                    date_str = msg.date.isoformat() if hasattr(msg, 'date') and msg.date else None

                    if raw_text.startswith("⏳ **[TODO]**") or raw_text.startswith("⏳ [TODO]"):
                        clean_title = raw_text.replace("⏳ **[TODO]**", "").replace("⏳ [TODO]", "").strip()
                        await add_todo(
                            title=clean_title,
                            telegram_message_id=msg.id,
                            telegram_channel_id=target_todo_id,
                            is_demo=False,
                            created_at=date_str,
                            completed=False
                        )
                        existing_todo_ids.add(msg.id)
                        imported += 1
                    elif raw_text.startswith("✅ **[COMPLETED]**") or raw_text.startswith("✅ [COMPLETED]"):
                        clean_title = (
                            raw_text.replace("✅ **[COMPLETED]**", "")
                            .replace("✅ [COMPLETED]", "")
                            .replace("~", "")
                            .strip()
                        )
                        await add_todo(
                            title=clean_title,
                            telegram_message_id=msg.id,
                            telegram_channel_id=target_todo_id,
                            is_demo=False,
                            created_at=date_str,
                            completed=True
                        )
                        existing_todo_ids.add(msg.id)
                        imported += 1
                    else:
                        from database import append_note_chunk

                        cont_match = re.search(r'\[cont:(\d+)\]', raw_text)
                        parent_id = int(cont_match.group(1)) if cont_match else getattr(msg, 'reply_to_msg_id', None)

                        clean_content = re.sub(r'^📝\s*(\*\*\[NOTE\]\*\*|\[NOTE\])(\s*\(Part\s*\d+/\d+\))?(\s*\[cont:\d+\])?\s*\n*', '', raw_text).strip()

                        if parent_id:
                            merged = await append_note_chunk(parent_id, msg.id, clean_content)
                            if merged:
                                existing_note_ids.add(msg.id)
                                continue

                        await add_note(
                            content=clean_content,
                            telegram_message_id=msg.id,
                            telegram_channel_id=target_todo_id,
                            is_demo=False,
                            created_at=date_str
                        )
                        existing_note_ids.add(msg.id)
                        imported += 1

                if not any_found and batch_start > 150:
                    break

        # 4. Reconcile deleted files, notes, and todos from Telegram
        try:
            purged_files = await self.reconcile_active_files()
            if purged_files:
                logger.info(f"Channel sync reconciled and purged {purged_files} deleted files.")
            purged = await self.reconcile_active_notes_and_todos()
            if purged:
                logger.info(f"Channel sync reconciled and purged {purged} deleted notes/todos.")
        except Exception as recon_err:
            logger.error(f"Error during channel sync deletion reconciliation: {recon_err}")

        logger.info(f"Channel sync completed: {imported} new items imported.")
        return imported

    # ==================== FILES, PHOTOS, VIDEOS ====================

    async def fast_upload_file(
        self,
        file_path: Path,
        filename: str,
        progress_callback=None,
        max_workers: int = 3,
        client_override: Optional[Any] = None
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
        
        bot_to_use = client_override if client_override else self.client

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

                    # Retry up to 8 times with exponential backoff on transient network blips
                    success = False
                    for attempt in range(8):
                        try:
                            request = functions.upload.SaveBigFilePartRequest(
                                file_id=file_id,
                                file_part=part_index,
                                file_total_parts=part_count,
                                bytes=chunk
                            )
                            result = await bot_to_use(request)
                            if result:
                                success = True
                                break
                            logger.warning(f"Worker {worker_id}: Part {part_index} returned False, retrying (attempt {attempt + 1}/8)...")
                        except Exception as exc:
                            from telethon.errors import FloodWaitError
                            if isinstance(exc, FloodWaitError):
                                wait_time = exc.seconds + 1
                                logger.warning(f"Worker {worker_id}: FloodWait on part {part_index}, sleeping {wait_time}s...")
                                await asyncio.sleep(wait_time)
                                continue

                            logger.warning(f"Worker {worker_id}: Part {part_index} attempt {attempt + 1}/8 failed: {exc}")
                            if attempt == 7:
                                raise
                            backoff = min(0.5 * (1.5 ** attempt), 6.0)
                            await asyncio.sleep(backoff)

                    if not success:
                        raise RuntimeError(f"Failed to upload part {part_index} after 8 attempts")

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
        file_path: Optional[Path] = None,
        filename: str = "",
        category: str = "file",
        progress_callback=None,
        upload_id: Optional[str] = None,
        file_obj: Optional[Any] = None,
        file_stream: Optional[Any] = None,
        file_size: Optional[int] = None
    ) -> Tuple[int, int, Optional[str], List[int], Dict[str, Any]]:
        """Uploads a file directly into the Telegram storage channel as a message with document attachment."""
        # Defensive boundary check: if file_path is provided, must resolve within approved directories
        if file_path is not None:
            file_path = Path(file_path).resolve()
            upload_dir_r = UPLOAD_DIR.resolve()
            demo_dir_r = DEMO_STORAGE_DIR.resolve()
            if not (file_path.is_relative_to(upload_dir_r) or file_path.is_relative_to(demo_dir_r)):
                raise ValueError(f"Path traversal detected in upload_file: '{file_path}'")
            if file_size is None:
                file_size = os.path.getsize(file_path)
        elif file_obj is not None:
            if file_size is None:
                try:
                    file_obj.seek(0, os.SEEK_END)
                    file_size = file_obj.tell()
                    file_obj.seek(0)
                except Exception:
                    file_size = 0
        elif file_size is None:
            file_size = 0

        target_channel_id = SOFTWARE_CHANNEL_ID if (category == "software" and SOFTWARE_CHANNEL_ID != 0) else CHANNEL_ID

        # Multi-GB file automatic PNG chunking & encryption pipeline (Pipelined Producer-Consumer)
        if file_size > PIXEL_VAULT_CHUNK_THRESHOLD:
            logger.info(f"File {filename} ({file_size:,} bytes) exceeds 1.99 GB threshold. Starting pipelined AES-256 Pixel Vault chunking & upload...")
            import secrets
            import time
            from pixel_vault import pack_stream_to_png_chunks
            from telethon.tl.types import DocumentAttributeFilename
            key = os.urandom(32)
            nonce = os.urandom(16)

            # Resolve target entity for Telegram if connected
            target_entity = self.channel_entity
            if not (self.is_demo or not self.client):
                if category == "software" and SOFTWARE_CHANNEL_ID != 0:
                    soft_entity = await self.get_software_entity()
                    if soft_entity:
                        target_entity = soft_entity
                        target_channel_id = SOFTWARE_CHANNEL_ID
                    else:
                        target_entity = self.channel_entity
                        target_channel_id = CHANNEL_ID

            estimated_total_parts = max(1, (file_size + PIXEL_VAULT_CHUNK_SIZE - 1) // PIXEL_VAULT_CHUNK_SIZE)
            chunk_queue = asyncio.Queue(maxsize=len(self.clients) * 2 if self.clients else 2)
            chunk_files_to_clean = set()
            chunk_msg_ids_dict = {}
            chunk_filenames_dict = {}
            first_tg_file_id = None
            producer_error = None

            # Progress tracking variables
            completed_bytes = [0]
            active_progress = {i: 0 for i in range(len(self.clients) if self.clients else 1)}

            async def producer():
                nonlocal producer_error
                try:
                    from pixel_vault import _AesCtrStreamCipher, write_streaming_png_chunk_file
                    cipher = _AesCtrStreamCipher(key, nonce, for_encryption=True)
                    part_idx = 1

                    if file_obj is not None:
                        src_f = file_obj
                        close_src = False
                        src_f.seek(0)
                    elif file_path is not None:
                        src_f = open(file_path, "rb")
                        close_src = True
                    else:
                        raise ValueError("No file source (file_path or file_obj) provided for upload.")

                    try:
                        remaining = file_size
                        while remaining > 0:
                            t_enc_start = time.time()
                            chunk_payload_len = min(remaining, PIXEL_VAULT_CHUNK_SIZE)
                            chunk_filename = f"pv_chunk_part{part_idx}_{secrets.token_hex(4)}.png"
                            chunk_path = UPLOAD_DIR / chunk_filename

                            def _stream_encrypted_blocks():
                                bytes_read = 0
                                while bytes_read < chunk_payload_len:
                                    take = min(64 * 1024, chunk_payload_len - bytes_read)
                                    raw = src_f.read(take)
                                    if not raw:
                                        break
                                    bytes_read += len(raw)
                                    yield cipher.update(raw)

                            # Stream in 64 KB blocks directly to disk with < 15 MB RAM usage
                            await asyncio.to_thread(
                                write_streaming_png_chunk_file,
                                _stream_encrypted_blocks(),
                                chunk_payload_len,
                                chunk_path
                            )
                            enc_dur = time.time() - t_enc_start
                            chunk_files_to_clean.add(chunk_path)
                            if upload_id:
                                try:
                                    from telemetry_recorder import recorder
                                    recorder.record_encryption_chunk(upload_id, part_idx, chunk_payload_len, enc_dur)
                                except Exception:
                                    pass
                            await chunk_queue.put((chunk_path, chunk_filename, part_idx))
                            remaining -= chunk_payload_len
                            part_idx += 1

                        trailing = cipher.finalize()
                    finally:
                        if close_src:
                            src_f.close()
                except Exception as e:
                    producer_error = e
                    logger.error(f"Producer failed in Pixel Vault pipeline: {e}")
                finally:
                    for _ in (self.clients if self.clients else [1]):
                        await chunk_queue.put(None)

            async def consumer(bot_idx: int, bot_client: Any):
                nonlocal first_tg_file_id
                while True:
                    item = await chunk_queue.get()
                    if item is None:
                        chunk_queue.task_done()
                        break
                    c_path, c_name, p_idx = item
                    chunk_filenames_dict[p_idx] = c_name
                    chunk_size = os.path.getsize(c_path)
                    chunk_caption = f"🔐 **[PIXEL_VAULT_CHUNK]** `{filename}` (Part {p_idx}/{estimated_total_parts})"

                    def make_chunk_cb():
                        def _cb(chunk_current, chunk_total, status="uploading_to_telegram", **kwargs):
                            active_progress[bot_idx] = chunk_current
                            if progress_callback:
                                cumulative = min(completed_bytes[0] + sum(active_progress.values()), file_size)
                                try:
                                    progress_callback(cumulative, file_size, status=status)
                                except TypeError:
                                    progress_callback(cumulative, file_size)
                        return _cb

                    chunk_cb = make_chunk_cb() if progress_callback else None

                    t_up_start = time.time()
                    sent_mid = 0
                    try:
                        if self.is_demo or not bot_client:
                            self._demo_counter += 1
                            demo_msg_id = self._demo_counter
                            demo_dest = (DEMO_STORAGE_DIR / f"{demo_msg_id}_{c_name}").resolve()
                            import shutil
                            shutil.copyfile(c_path, demo_dest)
                            chunk_msg_ids_dict[p_idx] = demo_msg_id
                            sent_mid = demo_msg_id
                            if not first_tg_file_id:
                                first_tg_file_id = "demo_file_id"
                        else:
                            uploaded_c = await self.fast_upload_file(
                                file_path=c_path,
                                filename=c_name,
                                progress_callback=chunk_cb,
                                max_workers=6,
                                client_override=bot_client
                            )
                            if chunk_cb:
                                try:
                                    chunk_cb(chunk_size, chunk_size, status="committing_telegram_file")
                                except TypeError:
                                    chunk_cb(chunk_size, chunk_size)
                            
                            bot_target_entity = await bot_client.get_input_entity(target_channel_id)
                            c_msg = await bot_client.send_file(
                                entity=bot_target_entity,
                                file=uploaded_c,
                                caption=chunk_caption,
                                force_document=True,
                                attributes=[DocumentAttributeFilename(file_name=c_name)]
                            )
                            chunk_msg_ids_dict[p_idx] = c_msg.id
                            sent_mid = c_msg.id
                            if not first_tg_file_id and c_msg.media and hasattr(c_msg.media, "document"):
                                first_tg_file_id = str(c_msg.media.document.id)

                        up_dur = time.time() - t_up_start
                        if upload_id:
                            try:
                                from telemetry_recorder import recorder
                                recorder.record_telegram_upload(upload_id, p_idx, chunk_size, up_dur, sent_mid)
                            except Exception:
                                pass
                    except Exception as ce:
                        logger.error(f"Consumer {bot_idx} failed on Pixel Vault chunk {c_name}: {ce}")
                        raise
                    finally:
                        completed_bytes[0] += chunk_size
                        active_progress[bot_idx] = 0
                        if not self.is_demo:
                            try:
                                c_path.unlink()
                            except Exception:
                                pass
                        chunk_queue.task_done()

            producer_task = asyncio.create_task(producer())
            
            consumers = []
            if self.clients:
                for i, c in enumerate(self.clients):
                    consumers.append(asyncio.create_task(consumer(i, c)))
            else:
                consumers.append(asyncio.create_task(consumer(0, None)))

            try:
                await asyncio.gather(producer_task, *consumers)
                if producer_error:
                    raise producer_error
            finally:
                for cp in list(chunk_files_to_clean):
                    if cp.exists():
                        try:
                            cp.unlink()
                        except Exception:
                            pass

            # Assemble ordered chunk IDs
            chunk_msg_ids = [chunk_msg_ids_dict[i] for i in sorted(chunk_msg_ids_dict.keys())]
            chunk_filenames = [chunk_filenames_dict[i] for i in sorted(chunk_filenames_dict.keys())]
            
            encryption_meta = {
                "type": "pixel_vault",
                "key": key.hex(),
                "nonce": nonce.hex(),
                "chunks": chunk_msg_ids,
                "chunk_filenames": chunk_filenames,
                "original_filename": filename,
                "original_size": file_size,
                "chunk_count": len(chunk_msg_ids)
            }

            primary_msg_id = chunk_msg_ids[0] if chunk_msg_ids else 0
            return primary_msg_id, target_channel_id, first_tg_file_id, chunk_msg_ids, encryption_meta

        # Single-file handling (< PIXEL_VAULT_CHUNK_THRESHOLD)
        temp_single_file_created = False
        import shutil
        if file_path is None and file_obj is not None:
            clean_name = Path(filename).name
            import secrets
            file_path = UPLOAD_DIR / f"temp_single_{secrets.token_hex(8)}_{clean_name}"
            with open(file_path, "wb") as f_out:
                file_obj.seek(0)
                shutil.copyfileobj(file_obj, f_out, length=1024 * 1024)
            temp_single_file_created = True

        try:
            if self.is_demo or not self.client:
                self._demo_counter += 1
                demo_msg_id = self._demo_counter
                clean_name = Path(filename).name
                demo_dest = (DEMO_STORAGE_DIR / f"{demo_msg_id}_{clean_name}").resolve()
                if not demo_dest.is_relative_to(DEMO_STORAGE_DIR.resolve()):
                    raise ValueError("Path traversal attempt detected in filename.")
                total_size = file_size
                copied = 0
                with open(file_path, "rb") as src, open(demo_dest, "wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                        copied += len(chunk)
                        if progress_callback:
                            progress_callback(copied, total_size)
                return demo_msg_id, target_channel_id or -1009999999999, f"demo_file_{demo_msg_id}", [], {}

            from telethon.tl.types import DocumentAttributeFilename

            icon = "💻" if category == "software" else ("🖼️" if category == "photo" else ("🎬" if category == "video" else "📁"))
            caption = f"{icon} **{category.title()}:** `{filename}`\n💾 **Size:** {file_size:,} bytes"

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

            is_big = file_size > 10 * 1024 * 1024

            # For regular files, use parallel MTProto multi-worker engine for maximum throughput
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

            if progress_callback:
                try:
                    progress_callback(file_size, file_size, status="committing_telegram_file")
                except TypeError:
                    progress_callback(file_size, file_size)

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

            return message.id, target_channel_id, tg_file_id, [], {}
        finally:
            if temp_single_file_created and file_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass

    async def download_pixel_vault_stream(
        self,
        file_record: Dict[str, Any],
        download_id: Optional[str] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Download, unpack, and decrypt Pixel Vault PNG chunks on the fly.
        Bit-for-bit reconstructs and streams the original multi-GB file with low RAM (< 15 MB)
        using pipelined prefetching to overlap Telegram MTProto chunk downloading with client streaming.
        """
        import json
        import secrets
        import time
        from pixel_vault import _AesCtrStreamCipher, stream_unpack_png_file

        enc_meta_raw = file_record.get("encryption_meta")
        if not enc_meta_raw:
            raise ValueError("File is not marked with Pixel Vault encryption metadata.")

        enc_meta = json.loads(enc_meta_raw) if isinstance(enc_meta_raw, str) else enc_meta_raw
        key = bytes.fromhex(enc_meta["key"])
        nonce = bytes.fromhex(enc_meta["nonce"])
        chunk_msg_ids = enc_meta.get("chunks", [])
        chunk_filenames = enc_meta.get("chunk_filenames", [])
        channel_id = file_record.get("telegram_channel_id")
        is_demo = file_record.get("is_demo", False)

        cipher = _AesCtrStreamCipher(key, nonce, for_encryption=False)

        # Producer-Consumer queue for lookahead chunk prefetching
        # maxsize=2 means while Chunk 1 is streaming, Bot 2 and Bot 3 can pipeline Chunks 2 & 3 to disk
        prefetch_queue = asyncio.Queue(maxsize=2)
        prefetch_error = None
        prefetch_files_to_clean = set()

        async def prefetch_producer():
            nonlocal prefetch_error
            try:
                for i, chunk_mid in enumerate(chunk_msg_ids):
                    c_fname = chunk_filenames[i] if i < len(chunk_filenames) else f"chunk_{chunk_mid}.png"
                    t_chunk_start = time.time()

                    # For demo mode, if file is already on disk, we can use it directly
                    if is_demo or self.is_demo or not self.client:
                        clean_name = Path(c_fname).name
                        demo_path = (DEMO_STORAGE_DIR / f"{chunk_mid}_{clean_name}").resolve()
                        if not demo_path.exists():
                            prefix_matches = list(DEMO_STORAGE_DIR.glob(f"{chunk_mid}_*"))
                            demo_path = prefix_matches[0] if prefix_matches else demo_path
                        await prefetch_queue.put((demo_path, i, False))
                    else:
                        # Download chunk to transient prefetch file on local disk
                        prefetch_path = UPLOAD_DIR / f"pv_dl_part{i}_{secrets.token_hex(4)}.png"
                        prefetch_files_to_clean.add(prefetch_path)
                        
                        # Round-Robin bot selection for downloading
                        bot_to_use = self.clients[i % len(self.clients)] if self.clients else self.client
                        
                        with open(prefetch_path, "wb") as f_out:
                            async for data in self.download_file_stream(
                                telegram_message_id=chunk_mid,
                                filename=c_fname,
                                telegram_channel_id=channel_id,
                                is_demo=False,
                                client_override=bot_to_use
                            ):
                                f_out.write(data)

                        chunk_dur = time.time() - t_chunk_start
                        chunk_size = prefetch_path.stat().st_size
                        if download_id:
                            try:
                                from telemetry_recorder import recorder
                                recorder.record_download_chunk(download_id, i + 1, chunk_size, chunk_dur)
                            except Exception:
                                pass

                        await prefetch_queue.put((prefetch_path, i, True))
            except Exception as e:
                prefetch_error = e
                logger.error(f"Prefetch producer failed: {e}")
            finally:
                await prefetch_queue.put(None)

        producer_task = asyncio.create_task(prefetch_producer())
        first_byte_recorded = False
        t_download_start = time.time()

        try:
            while True:
                item = await prefetch_queue.get()
                if item is None:
                    prefetch_queue.task_done()
                    break

                chunk_path, part_idx, needs_unlink = item
                try:
                    # Stream unpack and decrypt in 64 KB blocks with < 1 MB memory
                    for raw_ciphertext_block in stream_unpack_png_file(chunk_path):
                        plaintext_block = cipher.update(raw_ciphertext_block)
                        if plaintext_block:
                            if not first_byte_recorded and download_id:
                                first_byte_recorded = True
                                try:
                                    from telemetry_recorder import recorder
                                    recorder.record_download_ttfb(download_id, time.time() - t_download_start)
                                except Exception:
                                    pass
                            yield plaintext_block
                finally:
                    if needs_unlink:
                        prefetch_files_to_clean.discard(chunk_path)
                        if chunk_path.exists():
                            try:
                                chunk_path.unlink()
                            except Exception:
                                pass
                    prefetch_queue.task_done()

            trailing = cipher.finalize()
            if trailing:
                yield trailing

            await producer_task
            if prefetch_error:
                raise prefetch_error
        finally:
            if not producer_task.done():
                producer_task.cancel()
            for cp in list(prefetch_files_to_clean):
                if cp.exists():
                    try:
                        cp.unlink()
                    except Exception:
                        pass

    async def fast_download_stream(
        self,
        media: Any,
        file_size: int,
        max_workers: int = 6,
        part_size: int = 512 * 1024,
        client_override: Optional[Any] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        High-performance parallel MTProto streaming download engine using Telegram's upload.getFile
        specification. Employs concurrent coroutine workers with a bounded sliding window buffer
        to deliver chunks in strict sequential order without high memory usage.
        """
        from telethon import functions, utils

        info = utils._get_file_info(media)
        location = info.location
        dc_id = info.dc_id
        actual_size = file_size or getattr(info, "size", 0) or 0
        bot_client = client_override if client_override else self.client
        if actual_size <= 0:
            async for chunk in bot_client.iter_download(media, chunk_size=part_size):
                yield chunk
            return

        part_count = (actual_size + part_size - 1) // part_size
        actual_workers = min(max_workers, part_count)

        # Resolve sender for the target DC if different
        sender = bot_client._sender
        if dc_id and bot_client.session.dc_id != dc_id:
            try:
                sender = await bot_client._borrow_exported_sender(dc_id)
            except Exception as e:
                logger.warning(f"Could not borrow sender for DC {dc_id}: {e}. Using default sender.")
                sender = bot_client._sender

        # Bounded queue limits in-flight prefetching to prevent RAM buildup (at most 16 chunks ahead)
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
                for attempt in range(15):
                    try:
                        if sender and hasattr(sender, "is_connected") and sender.is_connected():
                            res = await bot_client._call(sender, req)
                        else:
                            res = await bot_client(req)
                        chunk = res.bytes
                        break
                    except Exception as exc:
                        logger.warning(f"Download worker {worker_id}: Part {idx} attempt {attempt+1} failed: {exc}")
                        # On socket drops / WinError 121 / timeout, fall back to client(req) directly
                        try:
                            res = await bot_client(req)
                            chunk = res.bytes
                            break
                        except Exception as fb_exc:
                            logger.warning(f"Download worker {worker_id}: Fallback attempt {attempt+1} failed: {fb_exc}")
                        if attempt == 14:
                            worker_error = exc
                            async with condition:
                                condition.notify_all()
                            return
                        wait_sec = min(0.5 * (1.5 ** attempt), 10.0)
                        await asyncio.sleep(wait_sec)

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
        is_demo: bool = False,
        client_override: Optional[Any] = None
    ) -> AsyncGenerator[bytes, None]:
        """Stream chunks directly from Telegram message."""
        if is_demo or self.is_demo or not self.client:
            clean_name = Path(filename).name
            demo_path = (DEMO_STORAGE_DIR / f"{telegram_message_id}_{clean_name}").resolve()
            if not demo_path.is_relative_to(DEMO_STORAGE_DIR.resolve()):
                raise ValueError("Path traversal attempt detected in filename.")
            if not demo_path.exists():
                prefix_matches = list(DEMO_STORAGE_DIR.glob(f"{telegram_message_id}_*"))
                if prefix_matches:
                    demo_path = prefix_matches[0]
                else:
                    raise FileNotFoundError(f"File {filename} not found in demo storage.")
            with open(demo_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
                    await asyncio.sleep(0)
            return

        bot_client = client_override if client_override else self.client
        
        target_channel = telegram_channel_id if telegram_channel_id else CHANNEL_ID
        try:
            # The downloading bot MUST resolve the entity using its own session cache 
            # so the access_hash matches its own authorization, preventing "Invalid channel object".
            target_entity = await bot_client.get_input_entity(target_channel)
        except Exception:
            # Fallback to the integer ID and hope the client's internal cache handles it
            target_entity = target_channel

        # IMPORTANT: The message MUST be fetched by the specific bot downloading it, 
        # otherwise Telegram will throw "FileReferenceExpiredError" because the file_reference
        # is cryptographically bound to the bot session that requested the message!
        message = await bot_client.get_messages(target_entity, ids=telegram_message_id)
        if not message or not message.media:
            raise FileNotFoundError(f"Telegram message {telegram_message_id} media not found.")

        doc_size = 0
        if hasattr(message.media, "document") and message.media.document:
            doc_size = getattr(message.media.document, "size", 0)

        is_big = doc_size > 10 * 1024 * 1024

        # For files > 10 MB, stream via parallel 3-worker MTProto engine to prevent IP connection limits
        if is_big:
            logger.info(f"Parallel MTProto download: {filename} ({doc_size:,} bytes) using 3 concurrent workers")
            async for chunk in self.fast_download_stream(message.media, file_size=doc_size, max_workers=3, client_override=bot_client):
                yield chunk
        else:
            async for chunk in bot_client.iter_download(message.media, chunk_size=1024 * 1024):
                yield chunk

    # ==================== TODOS & NOTES (CHANNEL 2) ====================

    async def send_todo_message(self, title: str) -> Tuple[int, int]:
        """Send a new todo task to the Todo channel."""
        target_channel_id = TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID
        if self.is_demo or not self.client or not self.is_connected:
            self._demo_counter += 1
            return self._demo_counter, target_channel_id

        entity = await self.get_todo_entity() or self.channel_entity
        if not entity:
            self._demo_counter += 1
            return self._demo_counter, target_channel_id

        try:
            msg = await self.client.send_message(
                entity=entity,
                message=f"⏳ **[TODO]** {title}"
            )
            return msg.id, target_channel_id
        except Exception:
            try:
                msg = await self.client.send_message(
                    entity=entity,
                    message=f"⏳ [TODO] {title}",
                    parse_mode=None
                )
                return msg.id, target_channel_id
            except Exception as e:
                logger.error(f"Failed to send todo message to Telegram: {e}")
                raise RuntimeError(f"Telegram delivery failed: {e}")

    async def update_todo_message(self, telegram_message_id: int, title: str, completed: bool) -> None:
        """Edit todo message in Telegram channel to reflect checkmark."""
        if self.is_demo or not self.client or not self.is_connected:
            return
        entity = await self.get_todo_entity() or self.channel_entity
        if not entity:
            return
        status_text = "✅ [COMPLETED]" if completed else "⏳ [TODO]"
        text_plain = f"{status_text} ~{title}~" if completed else f"{status_text} {title}"
        try:
            status_md = "✅ **[COMPLETED]**" if completed else "⏳ **[TODO]**"
            text_md = f"{status_md} ~{title}~" if completed else f"{status_md} {title}"
            await self.client.edit_message(
                entity=entity,
                message=telegram_message_id,
                text=text_md
            )
        except Exception:
            try:
                await self.client.edit_message(
                    entity=entity,
                    message=telegram_message_id,
                    text=text_plain,
                    parse_mode=None
                )
            except Exception as e:
                logger.error(f"Failed to edit todo message {telegram_message_id}: {e}")

    async def send_note_message(self, content: str) -> Tuple[int, int, List[int]]:
        """Send a quick text note to the Todo/Notes channel with auto-chunking (>3800 chars) and parse-mode fallback."""
        target_channel_id = TODO_CHANNEL_ID if TODO_CHANNEL_ID != 0 else CHANNEL_ID
        if self.is_demo or not self.client or not self.is_connected:
            self._demo_counter += 1
            return self._demo_counter, target_channel_id, []

        entity = await self.get_todo_entity() or self.channel_entity
        if not entity:
            logger.warning("No channel entity available for note. Saving locally.")
            self._demo_counter += 1
            return self._demo_counter, target_channel_id, []

        # Telegram hard-limits message text to 4096 characters. Use safe 3800-character chunks.
        chunk_size = 3800
        content_chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
        if not content_chunks:
            content_chunks = [content]

        num_chunks = len(content_chunks)

        if num_chunks == 1:
            sent_msg = None
            try:
                sent_msg = await self.client.send_message(
                    entity=entity,
                    message=f"📝 **[NOTE]**\n\n{content}"
                )
            except Exception as md_err:
                logger.debug(f"Markdown note send failed ({md_err}), falling back to plain text send.")

            if not sent_msg:
                try:
                    sent_msg = await self.client.send_message(
                        entity=entity,
                        message=f"📝 [NOTE]\n\n{content}",
                        parse_mode=None
                    )
                except Exception as e:
                    logger.error(f"Failed to send note to Telegram: {e}")
                    raise RuntimeError(f"Telegram delivery failed: {e}")

            return sent_msg.id, target_channel_id, []

        # Multi-part chunked message (>3800 chars)
        first_chunk_text = f"📝 [NOTE] (Part 1/{num_chunks})\n\n{content_chunks[0]}"
        try:
            first_msg = await self.client.send_message(
                entity=entity,
                message=first_chunk_text,
                parse_mode=None
            )
        except Exception as e:
            logger.error(f"Failed to send initial note chunk 1/{num_chunks} to Telegram: {e}")
            raise RuntimeError(f"Telegram delivery failed: {e}")

        extra_ids: List[int] = []
        for idx in range(1, num_chunks):
            chunk_text = f"📝 [NOTE] (Part {idx+1}/{num_chunks}) [cont:{first_msg.id}]\n\n{content_chunks[idx]}"
            try:
                cont_msg = await self.client.send_message(
                    entity=entity,
                    message=chunk_text,
                    reply_to=first_msg.id,
                    parse_mode=None
                )
                extra_ids.append(cont_msg.id)
            except Exception as e:
                logger.error(f"Failed to send note chunk {idx+1}/{num_chunks} to Telegram: {e}")

        return first_msg.id, target_channel_id, extra_ids

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
