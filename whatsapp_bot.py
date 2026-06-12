import os
import requests
import time
import threading
import tempfile
import re
import io
import fitz  # PyMuPDF
from PIL import Image
from flask import Flask, request, jsonify
from process_orders import process_excel_orders_to_list
from datetime import datetime

app = Flask(__name__)

# الإعدادات من بيئة Render
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ذاكرة مؤقتة لمنع التكرار في الجلسة الواحدة
processed_messages = set()
processed_salla_orders = set()

# قاموس مؤقت لتخزين نتائج التحليل لكل مستخدم
user_temp_data = {}

# قفل برمجى لضمان إرسال رسائل سلة واحدة تلو الأخرى بأمان
salla_lock = threading.Lock()

# رقم الواتساب الفعلي الخاص بك
MY_WHATSAPP_NUMBER = "967739969981"


# ==================== دوال واتساب الأساسية ====================

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    requests.post(url, headers=headers, json=data)

def upload_whatsapp_media(file_path, mime_type):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {'file': (os.path.basename(file_path), open(file_path, 'rb'), mime_type)}
    data = {'messaging_product': 'whatsapp'}
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        return response.json().get('id')
    except:
        return None

def send_whatsapp_image_with_caption(to, media_id, caption):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, "caption": caption}
    }
    requests.post(url, headers=headers, json=data)


# ==================== دوال معالجة PDF ====================

def handle_pdf_logic(sender_id, media_content):
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        send_whatsapp_message(sender_id, f"📄 جاري استخراج {len(doc)} بوالص شحن... ⏳")
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            order_match = re.search(r'\b(2\d{8})\b', text)
            order_number = order_match.group(1) if order_match else "غير محدد"
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_img:
                pix.save(tmp_img.name)
                image_id = upload_whatsapp_media(tmp_img.name, "image/png")
                if image_id:
                    send_whatsapp_image_with_caption(sender_id, image_id, f"📦 رقم الطلب: {order_number}")
                os.remove(tmp_img.name)
        
        send_whatsapp_message(sender_id, "✅ تم إرسال جميع البوالص بنجاح.")
    except Exception as e:
        print(f"PDF Error: {str(e)}")
        send_whatsapp_message(sender_id, "❌ حدث خطأ في معالجة ملف البوالص.")


# ==================== دوال معالجة Excel الجديدة ====================

def send_orders_as_messages(sender_id, orders, region_name):
    """إرسال الطلبات كرسائل منفصلة"""
    if not orders:
        send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    send_whatsapp_message(sender_id, f"📨 جاري إرسال طلبات {region_name}...")
    time.sleep(2)
    
    for index, order in enumerate(orders):
        send_whatsapp_message(sender_id, order)
        time.sleep(2)
        if (index + 1) % 10 == 0:
            send_whatsapp_message(sender_id, f"⏳ تم إرسال {index + 1} من {len(orders)}...")
            time.sleep(6)
    
    send_whatsapp_message(sender_id, f"✅ تم إرسال {len(orders)} طلب لـ {region_name}")

def send_orders_as_excel(sender_id, orders, region_name):
    """إرسال الطلبات كملف Excel واحد"""
    if not orders:
        send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    try:
        import pandas as pd
        
        orders_data = []
        for order_msg in orders:
            order_dict = {}
            lines = order_msg.split('\n')
            for line in lines:
                if 'العنوان /' in line:
                    order_dict['العنوان'] = line.split('العنوان /')[1].strip()
                elif 'رقم الطلبية /' in line:
                    order_dict['رقم الطلبية'] = line.split('رقم الطلبية /')[1].strip()
                elif 'رقم المستلم /' in line:
                    order_dict['رقم المستلم'] = line.split('رقم المستلم /')[1].strip()
                elif 'اسم المستلم /' in line:
                    order_dict['اسم المستلم'] = line.split('اسم المستلم /')[1].strip()
            orders_data.append(order_dict)
        
        df = pd.DataFrame(orders_data)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            output_path = tmp.name
            df.to_excel(output_path, index=False, sheet_name=region_name)
        
        media_id = upload_whatsapp_media(output_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        if media_id:
            url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
            data = {
                "messaging_product": "whatsapp",
                "to": sender_id,
                "type": "document",
                "document": {
                    "id": media_id,
                    "caption": f"📊 {region_name}\nإجمالي الطلبات: {len(orders)}",
                    "filename": f"{region_name}_{len(orders)}_طلب.xlsx"
                }
            }
            requests.post(url, headers=headers, json=data)
            send_whatsapp_message(sender_id, f"✅ تم إرسال ملف Excel لـ {region_name}\nعدد الطلبات: {len(orders)}")
        else:
            send_whatsapp_message(sender_id, f"❌ فشل في إرسال ملف {region_name}")
        
        os.remove(output_path)
        
    except ImportError:
        send_whatsapp_message(sender_id, "❌ المكتبات المطلوبة غير موجودة (pandas, openpyxl)")
    except Exception as e:
        send_whatsapp_message(sender_id, f"❌ خطأ: {str(e)[:100]}")

def send_all_orders_as_excel(sender_id, riyadh_orders, other_orders):
    """إرسال جميع الطلبات في ملف Excel واحد"""
    try:
        import pandas as pd
        
        orders_data = []
        
        for order_msg in riyadh_orders:
            order_dict = {'المنطقة': 'الرياض'}
            lines = order_msg.split('\n')
            for line in lines:
                if 'العنوان /' in line:
                    order_dict['العنوان'] = line.split('العنوان /')[1].strip()
                elif 'رقم الطلبية /' in line:
                    order_dict['رقم الطلبية'] = line.split('رقم الطلبية /')[1].strip()
                elif 'رقم المستلم /' in line:
                    order_dict['رقم المستلم'] = line.split('رقم المستلم /')[1].strip()
                elif 'اسم المستلم /' in line:
                    order_dict['اسم المستلم'] = line.split('اسم المستلم /')[1].strip()
            orders_data.append(order_dict)
        
        for order_msg in other_orders:
            order_dict = {'المنطقة': 'باقي المناطق'}
            lines = order_msg.split('\n')
            for line in lines:
                if 'العنوان /' in line:
                    order_dict['العنوان'] = line.split('العنوان /')[1].strip()
                elif 'رقم الطلبية /' in line:
                    order_dict['رقم الطلبية'] = line.split('رقم الطلبية /')[1].strip()
                elif 'رقم المستلم /' in line:
                    order_dict['رقم المستلم'] = line.split('رقم المستلم /')[1].strip()
                elif 'اسم المستلم /' in line:
                    order_dict['اسم المستلم'] = line.split('اسم المستلم /')[1].strip()
            orders_data.append(order_dict)
        
        df = pd.DataFrame(orders_data)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            output_path = tmp.name
            df.to_excel(output_path, index=False, sheet_name='جميع الطلبات')
        
        media_id = upload_whatsapp_media(output_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        if media_id:
            url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
            data = {
                "messaging_product": "whatsapp",
                "to": sender_id,
                "type": "document",
                "document": {
                    "id": media_id,
                    "caption": f"📊 جميع الطلبات\n📍 الرياض: {len(riyadh_orders)} طلب\n🏠 باقي المناطق: {len(other_orders)} طلب\n📦 الإجمالي: {len(riyadh_orders) + len(other_orders)} طلب",
                    "filename": f"جميع_الطلبات_{len(riyadh_orders) + len(other_orders)}.xlsx"
                }
            }
            requests.post(url, headers=headers, json=data)
            send_whatsapp_message(sender_id, "✅ تم إرسال ملف Excel واحد يحتوي على جميع الطلبات")
        else:
            send_whatsapp_message(sender_id, "❌ فشل في إرسال الملف")
        
        os.remove(output_path)
        
    except ImportError:
        send_whatsapp_message(sender_id, "❌ المكتبات المطلوبة غير موجودة (pandas, openpyxl)")
    except Exception as e:
        send_whatsapp_message(sender_id, f"❌ خطأ: {str(e)[:100]}")

def send_interactive_buttons(to_number):
    """إرسال أزرار تفاعلية لاختيار طريقة الاستلام"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "📋 اختر طريقة الاستلام"
            },
            "body": {
                "text": "كيف تريد استلام الطلبات؟"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "riyadh_separate",
                            "title": "📍 الرياض - رسائل منفصلة"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "riyadh_excel",
                            "title": "📍 الرياض - ملف Excel"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "others_separate",
                            "title": "🏠 باقي المناطق - رسائل منفصلة"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "others_excel",
                            "title": "🏠 باقي المناطق - ملف Excel"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "all_excel",
                            "title": "📊 الكل - ملف Excel واحد"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "mixed_riyadh_separate_others_excel",
                            "title": "🔄 الرياض رسائل - الباقي Excel"
                        }
                    }
                ]
            }
        }
    }
    requests.post(url, headers=headers, json=data)


# ==================== دالة معالجة الملفات الرئيسية ====================

def handle_document_async(sender_id, doc):
    mime_type = doc.get('mime_type', '')
    filename = doc.get('filename', '').lower()
    media_id = doc.get('id')
    
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers).json()
    media_url = res.get('url')
    if not media_url: return
    
    media_content = requests.get(media_url, headers=headers).content

    if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
        send_whatsapp_message(sender_id, "📥 جاري تحليل ملف الإكسل... ⏳")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(media_content)
            input_path = tmp.name
        
        try:
            result = process_excel_orders_to_list(input_path)
            
            if result:
                riyadh_orders = result.get("riyadh", [])
                other_orders = result.get("others", [])
                
                # تخزين النتائج مؤقتاً للمستخدم
                user_temp_data[sender_id] = {
                    "riyadh": riyadh_orders,
                    "others": other_orders,
                    "filename": filename
                }
                
                # إرسال ملخص النتائج
                summary = f"📊 *نتائج التحليل:*\n"
                summary += f"📍 طلبات الرياض: {len(riyadh_orders)} طلب\n"
                summary += f"🏠 طلبات باقي المناطق: {len(other_orders)} طلب\n\n"
                summary += "*اختر طريقة الاستلام:*"
                
                send_whatsapp_message(sender_id, summary)
                send_interactive_buttons(sender_id)
                
            else:
                send_whatsapp_message(sender_id, "❌ لم يتم العثور على بيانات في ملف الإكسل")
                
        except Exception as e:
            print(f"Excel processing error: {str(e)}")
            send_whatsapp_message(sender_id, f"❌ حدث خطأ: {str(e)[:100]}")
        finally:
            if os.path.exists(input_path):
                os.remove(input_path)

    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)


# ==================== دالة معالجة سلة (Webhook) ====================

def process_salla_webhook_async(shipment_data):
    with salla_lock:
        try:
            order_id = shipment_data.get('order_id', 'غير متوفر')
            
            if order_id in processed_salla_orders:
                return
            processed_salla_orders.add(order_id)
            if len(processed_salla_orders) > 1000:
                processed_salla_orders.clear()

            city = shipment_data.get('city', '')
            full_address = city if city else "غير محدد"

            recipient_name = shipment_data.get('customer_name', '')
            recipient_mobile = shipment_data.get('customer_phone', '')

            mobile_str = str(recipient_mobile).strip()
            if mobile_str.startswith('5') and len(mobile_str) == 9:
                mobile_str = '966' + mobile_str
            elif mobile_str.startswith('05') and len(mobile_str) == 10:
                mobile_str = '966' + mobile_str[1:]
            elif mobile_str.startswith('+'):
                mobile_str = mobile_str.replace('+', '')

            final_msg = f"العنوان / {full_address}\nرقم الطلبية/ {order_id}\nرقم المستلم / +{mobile_str}\nاسم المستلم/ {recipient_name.strip()}"
            
            send_whatsapp_message(MY_WHATSAPP_NUMBER, final_msg)
            time.sleep(2)

        except Exception as e:
            print(f"Async Salla Shipment Process Error: {str(e)}")


# ==================== مسار واتساب Webhook ====================

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge'), 200
        return 'Forbidden', 403

    data = request.json
    try:
        # التحقق من وجود رسالة
        if 'messages' in data['entry'][0]['changes'][0]['value']:
            msg = data['entry'][0]['changes'][0]['value']['messages'][0]
            msg_id = msg.get('id')
            sender_id = msg.get('from')
            
            msg_timestamp = int(msg.get('timestamp'))
            current_time = int(time.time())
            
            if (current_time - msg_timestamp) > 300:
                return jsonify({"status": "ignored_old_message"}), 200

            if msg_id in processed_messages:
                return jsonify({"status": "duplicate"}), 200
            
            processed_messages.add(msg_id)
            if len(processed_messages) > 1000:
                processed_messages.clear()

            # معالجة المستندات
            if msg.get('type') == 'document':
                threading.Thread(target=handle_document_async, args=(sender_id, msg['document'])).start()
            
            # معالجة النصوص العادية
            elif msg.get('type') == 'text':
                text_body = msg.get('text', {}).get('body', '')
                
                # معالجة ردود الأزرار (الرسائل النصية بديلاً عن الأزرار)
                if sender_id in user_temp_data:
                    data_store = user_temp_data[sender_id]
                    riyadh_orders = data_store["riyadh"]
                    other_orders = data_store["others"]
                    
                    # تنظيف البيانات المؤقتة
                    del user_temp_data[sender_id]
                    
                    text_lower = text_body.lower()
                    
                    if "الرياض" in text_lower:
                        if "رسائل" in text_lower or "منفصله" in text_lower:
                            send_orders_as_messages(sender_id, riyadh_orders, "الرياض")
                        elif "اكسل" in text_lower or "excel" in text_lower:
                            send_orders_as_excel(sender_id, riyadh_orders, "الرياض")
                        else:
                            send_whatsapp_message(sender_id, "❌ خيار غير صحيح، أعد إرسال الملف")
                    
                    elif "باقي" in text_lower or "المناطق" in text_lower:
                        if "رسائل" in text_lower or "منفصله" in text_lower:
                            send_orders_as_messages(sender_id, other_orders, "باقي المناطق")
                        elif "اكسل" in text_lower or "excel" in text_lower:
                            send_orders_as_excel(sender_id, other_orders, "باقي المناطق")
                        else:
                            send_whatsapp_message(sender_id, "❌ خيار غير صحيح، أعد إرسال الملف")
                    
                    elif "الكل" in text_lower or "جميع" in text_lower:
                        send_all_orders_as_excel(sender_id, riyadh_orders, other_orders)
                    
                    else:
                        send_whatsapp_message(sender_id, "أهلاً! أرسل ملف Excel لفرز الطلبات")
                else:
                    send_whatsapp_message(sender_id, "أهلاً! أرسل ملف Excel لفرز طلبات (قيد التنفيذ وجاري التوصيل)")
            
            # معالجة الردود التفاعلية (Interactive Buttons)
            elif msg.get('type') == 'interactive':
                interactive = msg.get('interactive', {})
                button_id = interactive.get('button_reply', {}).get('id', '')
                sender_id = msg.get('from')
                
                if sender_id in user_temp_data:
                    data_store = user_temp_data[sender_id]
                    riyadh_orders = data_store["riyadh"]
                    other_orders = data_store["others"]
                    
