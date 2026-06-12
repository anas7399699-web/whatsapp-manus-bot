import os
import requests
import time
import threading
import tempfile
import re
import fitz
from PIL import Image
from flask import Flask, request, jsonify
from process_orders import process_excel_orders_to_list
import pandas as pd

app = Flask(__name__)

# الإعدادات من بيئة Render
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ذاكرة مؤقتة
processed_messages = set()
processed_salla_orders = set()
user_temp_data = {}
salla_lock = threading.Lock()
MY_WHATSAPP_NUMBER = "967739969981"


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
    data = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"id": media_id, "caption": caption}}
    requests.post(url, headers=headers, json=data)


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


def send_orders_as_messages(sender_id, orders, region_name):
    """إرسال الطلبات كرسائل منفصلة (نفس النظام القديم)"""
    if not orders:
        send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    send_whatsapp_message(sender_id, f"📍 *{region_name}:*")
    time.sleep(2)
    
    for index, order in enumerate(orders):
        send_whatsapp_message(sender_id, order)
        time.sleep(2)
        if (index + 1) % 10 == 0:
            send_whatsapp_message(sender_id, f"⏳ تم إرسال {index + 1} من {len(orders)}...")
            time.sleep(6)
    
    send_whatsapp_message(sender_id, f"✅ تم إرسال {len(orders)} طلب لـ {region_name}")


def send_orders_as_excel(sender_id, orders, region_name):
    """إرسال الطلبات كملف Excel واحد - كل سطر في خانة منفصلة"""
    if not orders:
        send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    try:
        orders_data = []
        for order_msg in orders:
            order_dict = {
                'العنوان': '',
                'رقم الطلبية': '',
                'رقم المستلم': '',
                'اسم المستلم': ''
            }
            
            lines = order_msg.split('\n')
            for line in lines:
                if 'العنوان /' in line:
                    order_dict['العنوان'] = line.split('العنوان /')[1].strip()
                elif 'رقم الطلبية /' in line:
                    order_dict['رقم الطلبية'] = line.split('رقم الطلبية /')[1].strip()
                elif 'رقم الطلبية/' in line:
                    order_dict['رقم الطلبية'] = line.split('رقم الطلبية/')[1].strip()
                elif 'رقم المستلم /' in line:
                    order_dict['رقم المستلم'] = line.split('رقم المستلم /')[1].strip()
                elif 'اسم المستلم /' in line:
                    order_dict['اسم المستلم'] = line.split('اسم المستلم /')[1].strip()
                elif 'اسم المستلم/' in line:
                    order_dict['اسم المستلم'] = line.split('اسم المستلم/')[1].strip()
            
            orders_data.append(order_dict)
        
        df = pd.DataFrame(orders_data)
        df = df[['العنوان', 'رقم الطلبية', 'رقم المستلم', 'اسم المستلم']]
        
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
                    "caption": f"📊 طلبات {region_name}\n📦 إجمالي الطلبات: {len(orders)}",
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


def handle_document_async(sender_id, doc):
    mime_type = doc.get('mime_type', '')
    filename = doc.get('filename', '').lower()
    media_id = doc.get('id')
    
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers).json()
    media_url = res.get('url')
    if not media_url:
        return
    
    media_content = requests.get(media_url, headers=headers).content

    if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
        send_whatsapp_message(sender_id, "📥 جاري تحليل ملف الإكسل... ⏳")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(media_content)
            path = tmp.name
        
        try:
            result = process_excel_orders_to_list(path)
            
            if result:
                riyadh_orders = result.get("riyadh", [])
                other_orders = result.get("others", [])
                
                user_temp_data[sender_id] = {
                    "riyadh": riyadh_orders,
                    "others": other_orders
                }
                
                options = f"📊 *نتائج التحليل:*\n"
                options += f"📍 الرياض: {len(riyadh_orders)} طلب\n"
                options += f"🏠 باقي المناطق: {len(other_orders)} طلب\n\n"
                options += "*اختر طريقة الاستلام:*\n\n"
                options += "1️⃣ أرسل 'رياض رسائل' - لاستلام طلبات الرياض كرسائل منفصلة\n"
                options += "2️⃣ أرسل 'رياض اكسل' - لاستلام طلبات الرياض كملف Excel\n"
                options += "3️⃣ أرسل 'باقي رسائل' - لاستلام طلبات باقي المناطق كرسائل منفصلة\n"
                options += "4️⃣ أرسل 'باقي اكسل' - لاستلام طلبات باقي المناطق كملف Excel\n"
                options += "5️⃣ أرسل 'الكل اكسل' - لاستلام جميع الطلبات في ملف Excel واحد"
                
                send_whatsapp_message(sender_id, options)
                
            else:
                send_whatsapp_message(sender_id, "❌ لم يتم العثور على بيانات في ملف الإكسل")
                
        except Exception as e:
            print(f"Excel error: {str(e)}")
            send_whatsapp_message(sender_id, f"❌ حدث خطأ: {str(e)[:100]}")
        finally:
            if os.path.exists(path):
                os.remove(path)

    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)


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
            print(f"Salla Error: {str(e)}")


@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running", 200


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge'), 200
        return 'Forbidden', 403

    data = request.json
    try:
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

        if msg.get('type') == 'document':
            threading.Thread(target=handle_document_async, args=(sender_id, msg['document'])).start()
            
        elif msg.get('type') == 'text':
            text_body = msg.get('text', {}).get('body', '').lower()
            
            if sender_id in user_temp_data:
                data_store = user_temp_data[sender_id]
                riyadh_orders = data_store["riyadh"]
                other_orders = data_store["others"]
                del user_temp_data[sender_id]
                
                if "رياض رسائل" in text_body:
                    send_orders_as_messages(sender_id, riyadh_orders, "الرياض")
                elif "رياض اكسل" in text_body or "رياض excel" in text_body:
                    send_orders_as_excel(sender_id, riyadh_orders, "الرياض")
                elif "باقي رسائل" in text_body:
                    send_orders_as_messages(sender_id, other_orders, "باقي المناطق")
                elif "باقي اكسل" in text_body or "باقي excel" in text_body:
                    send_orders_as_excel(sender_id, other_orders, "باقي المناطق")
                elif "الكل اكسل" in text_body or "الكل excel" in text_body:
                    all_orders = riyadh_orders + other_orders
                    send_orders_as_excel(sender_id, all_orders, "جميع الطلبات")
                else:
                    send_whatsapp_message(sender_id, "❌ خيار غير صحيح. أرسل: رياض رسائل، رياض اكسل، باقي رسائل، باقي اكسل، أو الكل اكسل")
            else:
                send_whatsapp_message(sender_id, "أهلاً! أرسل ملف Excel لفرز الطلبات، أو PDF لاستخراج البوالص.")
            
    except Exception as e:
        print(f"Webhook error: {str(e)}")
        
    return jsonify({"status": "ok"}), 200


@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    if request.method == 'GET':
        return "Webhook is active", 200

    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({"status": "no_data"}), 400

        try:
            event = data.get('event')
            shipment_data = data.get('data', {})
            if 'shipment' in str(event):
                threading.Thread(target=process_salla_webhook_async, args=(shipment_data,)).start()
        except Exception as e:
            print(f"Salla Error: {str(e)}")
            
        return jsonify({"status": "received"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
