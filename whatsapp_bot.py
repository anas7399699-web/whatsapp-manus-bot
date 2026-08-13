# التحديث الأخير: 2026-08-13 - إضافة أعمدة إضافية مع الأولوية لبيانات المستلم
import os
import requests
import time
import threading
import tempfile
import re
import fitz  # PyMuPDF لقراءة PDF
from PIL import Image
from flask import Flask, request, jsonify
from process_orders import process_excel_orders_to_list
import pandas as pd  # لإنشاء ملفات Excel

app = Flask(__name__)

# ==================== إعدادات Render ====================
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ==================== الذواكر المؤقتة ====================
processed_messages = set()
processed_salla_orders = set()
user_temp_data = {}
user_temp_expiry = {}

# ==================== إعدادات إضافية ====================
salla_lock = threading.Lock()
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


# ==================== معالجة ملفات PDF ====================

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


# ==================== معالجة Excel (الرسائل المنفصلة) ====================

def send_orders_as_messages(sender_id, orders, region_name):
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


# ==================== استخراج البيانات من رسائل الطلب ====================

def extract_data_from_messages(orders):
    orders_data = []
    for order_msg in orders:
        order_dict = {
            'العنوان': '',
            'المدينة': '',
            'رقم الطلبية': '',
            'رقم المستلم': '',
            'اسم المستلم': ''
        }
        lines = order_msg.split('\n')
        for line in lines:
            if 'العنوان /' in line:
                full_address = line.split('العنوان /')[1].strip()
                if ' - ' in full_address:
                    parts = full_address.split(' - ', 1)
                    city = parts[0].strip()
                    clean_address = parts[1].strip() if len(parts) > 1 else full_address
                else:
                    city = ''
                    clean_address = full_address
                order_dict['العنوان'] = clean_address
                order_dict['المدينة'] = city
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
    return pd.DataFrame(orders_data)


# ==================== معالجة Excel (ملف Excel مع أعمدة إضافية) ====================

def send_orders_as_excel(sender_id, orders, region_name, original_file_path=None):
    """
    إرسال الطلبات كملف Excel مع الاحتفاظ بجميع الأعمدة من الملف الأصلي
    مع الأولوية لبيانات المستلم (إن وجدت) أو بيانات العميل
    """
    if not orders:
        send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    try:
        # تصفية الطلبات حسب المنطقة
        if region_name == "الرياض":
            filtered_orders = [order for order in orders if "الرياض" in order]
        elif region_name == "باقي المناطق":
            filtered_orders = [order for order in orders if "الرياض" not in order]
        else:
            filtered_orders = orders
        
        if not filtered_orders:
            send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
            return
        
        # إذا كان لدينا مسار الملف الأصلي، نقرأه مباشرة
        if original_file_path and os.path.exists(original_file_path):
            df_original = pd.read_excel(original_file_path)
            
            required_columns = [
                'اسم المستلم',
                'رقم المستلم',
                'العنوان',
                'المدينة',
                'الرمز البريدي',
                'رقم الشارع',
                'معرف الحي',
                'العنوان الوطني المختصر',
                'رقم المبنى',
                'الرقم الإضافي',
                'اسم العميل',
                'رقم العميل'
            ]
            
            available_columns = []
            for col in required_columns:
                if col in df_original.columns:
                    available_columns.append(col)
                else:
                    print(f"⚠️ العمود '{col}' غير موجود في الملف الأصلي")
            
            # الأولوية لبيانات المستلم
            if 'اسم المستلم' not in df_original.columns and 'اسم العميل' in df_original.columns:
                df_original['اسم المستلم'] = df_original['اسم العميل']
                if 'اسم المستلم' not in available_columns:
                    available_columns.append('اسم المستلم')
                print("✅ تم إنشاء عمود 'اسم المستلم' من 'اسم العميل'")
            
            if 'رقم المستلم' not in df_original.columns and 'رقم العميل' in df_original.columns:
                df_original['رقم المستلم'] = df_original['رقم العميل']
                if 'رقم المستلم' not in available_columns:
                    available_columns.append('رقم المستلم')
                print("✅ تم إنشاء عمود 'رقم المستلم' من 'رقم العميل'")
            
            if 'اسم المستلم' not in df_original.columns and 'اسم العميل' not in df_original.columns:
                print("⚠️ لا يوجد عمود لاسم المستلم أو العميل، سيتم إنشاء عمود فارغ")
                df_original['اسم المستلم'] = 'غير محدد'
                available_columns.append('اسم المستلم')
            
            if 'رقم المستلم' not in df_original.columns and 'رقم العميل' not in df_original.columns:
                print("⚠️ لا يوجد عمود لرقم المستلم أو العميل، سيتم إنشاء عمود فارغ")
                df_original['رقم المستلم'] = 'غير محدد'
                available_columns.append('رقم المستلم')
            
            # تصفية الصفوف حسب المنطقة
            region_column = None
            possible_region_columns = ['المنطقة', 'المدينة', 'city', 'City', 'region', 'Region']
            for col in possible_region_columns:
                if col in df_original.columns:
                    region_column = col
                    break
            
            if region_column:
                if region_name == "الرياض":
                    df_filtered = df_original[df_original[region_column].str.contains('الرياض', case=False, na=False)]
                else:
                    df_filtered = df_original[~df_original[region_column].str.contains('الرياض', case=False, na=False)]
                
                if len(df_filtered) == 0:
                    print(f"⚠️ لم يتم العثور على طلبات في {region_name}")
                    send_whatsapp_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
                    return
            else:
                print(f"⚠️ لم يتم العثور على عمود المنطقة، نستخدم جميع الصفوف")
                df_filtered = df_original
            
            # إزالة الأعمدة المكررة
            if 'اسم المستلم' in df_filtered.columns and 'اسم العميل' in df_filtered.columns:
                df_filtered = df_filtered.drop(columns=['اسم العميل'])
                if 'اسم العميل' in available_columns:
                    available_columns.remove('اسم العميل')
            
            if 'رقم المستلم' in df_filtered.columns and 'رقم العميل' in df_filtered.columns:
                df_filtered = df_filtered.drop(columns=['رقم العميل'])
                if 'رقم العميل' in available_columns:
                    available_columns.remove('رقم العميل')
            
            final_columns = [col for col in available_columns if col in df_filtered.columns]
            df_filtered = df_filtered[final_columns]
        else:
            # الطريقة القديمة (بدون ملف أصلي)
            orders_data = []
            for order_msg in filtered_orders:
                order_dict = {
                    'العنوان': '',
                    'المدينة': '',
                    'رقم الطلبية': '',
                    'رقم المستلم': '',
                    'اسم المستلم': ''
                }
                lines = order_msg.split('\n')
                for line in lines:
                    if 'العنوان /' in line:
                        full_address = line.split('العنوان /')[1].strip()
                        if ' - ' in full_address:
                            parts = full_address.split(' - ', 1)
                            city = parts[0].strip()
                            clean_address = parts[1].strip() if len(parts) > 1 else full_address
                        else:
                            city = ''
                            clean_address = full_address
                        order_dict['العنوان'] = clean_address
                        order_dict['المدينة'] = city
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
            df_filtered = pd.DataFrame(orders_data)
            df_filtered = df_filtered[['العنوان', 'المدينة', 'رقم الطلبية', 'رقم المستلم', 'اسم المستلم']]
        
        # حفظ الملف
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            output_path = tmp.name
            df_filtered.to_excel(output_path, index=False, sheet_name=region_name)
        
        # رفع الملف إلى واتساب
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
                    "caption": f"📊 طلبات {region_name}\n📦 إجمالي الطلبات: {len(df_filtered)}",
                    "filename": f"{region_name}_{len(df_filtered)}_طلب.xlsx"
                }
            }
            requests.post(url, headers=headers, json=data)
            send_whatsapp_message(sender_id, f"✅ تم إرسال ملف Excel لـ {region_name}\nعدد الطلبات: {len(df_filtered)}")
        else:
            send_whatsapp_message(sender_id, f"❌ فشل في إرسال ملف {region_name}")
        
        os.remove(output_path)
        
    except Exception as e:
        send_whatsapp_message(sender_id, f"❌ خطأ: {str(e)[:100]}")
        print(f"Excel error: {str(e)}")


# ==================== معالجة ملف Excel الرئيسية ====================

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
                    "others": other_orders,
                    "original_file_path": path
                }
                user_temp_expiry[sender_id] = time.time() + 1800
                options = f"📊 *نتائج التحليل:*\n"
                options += f"📍 الرياض: {len(riyadh_orders)} طلب\n"
                options += f"🏠 باقي المناطق: {len(other_orders)} طلب\n\n"
                options += "*اختر طريقة الاستلام:*\n\n"
                options += "1️⃣ أرسل 'رياض رسائل' - لاستلام طلبات الرياض كرسائل منفصلة\n"
                options += "2️⃣ أرسل 'رياض اكسل' - لاستلام طلبات الرياض كملف Excel\n"
                options += "3️⃣ أرسل 'باقي رسائل' - لاستلام طلبات باقي المناطق كرسائل منفصلة\n"
                options += "4️⃣ أرسل 'باقي اكسل' - لاستلام طلبات باقي المناطق كملف Excel\n"
                options += "5️⃣ أرسل 'الكل اكسل' - لاستلام جميع الطلبات في ملف Excel واحد\n"
                options += "6️⃣ أرسل 'مسح' - لحذف البيانات المؤقتة"
                send_whatsapp_message(sender_id, options)
            else:
                send_whatsapp_message(sender_id, "❌ لم يتم العثور على بيانات في ملف الإكسل")
        except Exception as e:
            print(f"Excel error: {str(e)}")
            send_whatsapp_message(sender_id, f"❌ حدث خطأ: {str(e)[:100]}")
        finally:
            pass

    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)


# ==================== دالة معالجة إشعارات سلة ====================

def process_salla_webhook_async(raw_data):
    with salla_lock:
        try:
            order_id = str(
                raw_data.get('id')
                or raw_data.get('order_id')
                or raw_data.get('reference_id')
                or 'غير متوفر'
            )
            order_status = raw_data.get('status', '')
            print(f"[Salla] 🔍 الحالة المستقبلة: '{order_status}'")
            
            allowed_statuses = [
                'جاري التوصيل',
                'جاري التوصيل ',
                'جاريالتوصيل',
                'تم التنفيذ',
                'تم التنفيذ ',
                'تمالتنفيذ',
                'shipped',
                'completed',
                'delivered',
                'out_for_delivery',
                'in_progress',
                'processing'
            ]
            if order_status not in allowed_statuses:
                print(f"[Salla] ⏭️ تم تجاهل تحديث الطلب {order_id} - الحالة: '{order_status}' (غير مسموحة)")
                return
            if order_id in processed_salla_orders:
                print(f"[Salla] تم تجاهل طلب مكرر: {order_id}")
                return
            processed_salla_orders.add(order_id)
            if len(processed_salla_orders) > 1000:
                processed_salla_orders.clear()
            
            recipient_obj = raw_data.get('shipping_address') or raw_data.get('address') or {}
            customer_obj = raw_data.get('customer') or {}
            
            recipient_name = (
                recipient_obj.get('name')
                or customer_obj.get('name')
                or 'غير متوفر'
            ).strip()
            
            recipient_mobile = (
                recipient_obj.get('phone')
                or recipient_obj.get('mobile')
                or customer_obj.get('mobile')
                or customer_obj.get('phone')
                or ''
            )
            
            city = recipient_obj.get('city', '') or customer_obj.get('city', '')
            district = recipient_obj.get('district', '') or customer_obj.get('district', '')
            street = recipient_obj.get('street', '') or customer_obj.get('street', '')
            
            if not city and not district and not street:
                city = customer_obj.get('city', '')
                district = customer_obj.get('district', '')
                street = customer_obj.get('street', '')
            
            address_parts = [part.strip() for part in [city, district, street] if part and part.strip()]
            full_address = ' - '.join(address_parts) if address_parts else 'غير محدد'

            mobile_str = str(recipient_mobile).strip().replace(' ', '').replace('-', '')
            if mobile_str.startswith('+'):
                mobile_str = mobile_str[1:]
            elif mobile_str.startswith('05') and len(mobile_str) == 10:
                mobile_str = '966' + mobile_str[1:]
            elif mobile_str.startswith('5') and len(mobile_str) == 9:
                mobile_str = '966' + mobile_str

            print(f"[Salla] ✅ سيتم إرسال إشعار للطلب {order_id} - الحالة: {order_status}")

                final_msg = (
                f"**العنوان /** {full_address}\n"
                f"**رقم الطلبية /** {order_id}\n"
                f"**رقم المستلم /** +{mobile_str}\n"
                f"**اسم المستلم /** {recipient_name}"
            )

            send_whatsapp_message(MY_WHATSAPP_NUMBER, final_msg)
            time.sleep(2)
        except Exception as e:
            print(f"[Salla] خطأ في المعالجة الداخلية: {str(e)}")


# ==================== دالة منع نوم Render ====================

def keep_alive():
    RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not RENDER_URL:
        return
    while True:
        try:
            time.sleep(600)
            requests.get(f"{RENDER_URL}/", timeout=10)
        except:
            pass


# ==================== المسارات ====================

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
                if sender_id in user_temp_expiry and time.time() > user_temp_expiry[sender_id]:
                    del user_temp_data[sender_id]
                    del user_temp_expiry[sender_id]
                    send_whatsapp_message(sender_id, "⏰ انتهت صلاحية بيانات الطلبات. أرسل ملف Excel مرة أخرى.")
                else:
                    data_store = user_temp_data[sender_id]
                    riyadh_orders = data_store["riyadh"]
                    other_orders = data_store["others"]
                    original_file_path = data_store.get("original_file_path")
                    
                    user_temp_expiry[sender_id] = time.time() + 1800
                    
                    if "رياض رسائل" in text_body:
                        send_orders_as_messages(sender_id, riyadh_orders, "الرياض")
                    elif "رياض اكسل" in text_body or "رياض excel" in text_body:
                        send_orders_as_excel(sender_id, riyadh_orders, "الرياض", original_file_path)
                    elif "باقي رسائل" in text_body:
                        send_orders_as_messages(sender_id, other_orders, "باقي المناطق")
                    elif "باقي اكسل" in text_body or "باقي excel" in text_body:
                        send_orders_as_excel(sender_id, other_orders, "باقي المناطق", original_file_path)
                    elif "الكل اكسل" in text_body or "الكل excel" in text_body:
                        all_orders = riyadh_orders + other_orders
                        send_orders_as_excel(sender_id, all_orders, "جميع الطلبات", original_file_path)
                    elif "مسح" in text_body or "انهاء" in text_body or "حذف" in text_body:
                        if sender_id in user_temp_data:
                            del user_temp_data[sender_id]
                        if sender_id in user_temp_expiry:
                            del user_temp_expiry[sender_id]
                        if original_file_path and os.path.exists(original_file_path):
                            try:
                                os.remove(original_file_path)
                            except:
                                pass
                        send_whatsapp_message(sender_id, "✅ تم مسح بيانات الطلبات المؤقتة.")
                    else:
                        send_whatsapp_message(sender_id, "❌ خيار غير صحيح. الأوامر المتاحة: رياض رسائل، رياض اكسل، باقي رسائل، باقي اكسل، الكل اكسل، مسح")
            else:
                send_whatsapp_message(sender_id, "أهلاً! أرسل ملف Excel لفرز الطلبات، أو PDF لاستخراج البوالص.")
            
    except Exception as e:
        print(f"Webhook error: {str(e)}")
        
    return jsonify({"status": "ok"}), 200


# ==================== مسار سلة مع كود تشخيصي ====================

@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    print("="*50)
    print("🚨 تم الوصول إلى مسار /salla-webhook")
    print(f"🚨 Method: {request.method}")
    print(f"🚨 Headers: {dict(request.headers)}")
    print(f"🚨 Body: {request.get_data(as_text=True)}")
    print("="*50)
    
    if request.method == 'GET':
        print("Salla webhook verification test received via GET.")
        return "Webhook is active", 200

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"status": "no_data"}), 400

        try:
            event = data.get('event', '')
            raw_data = data.get('data', {})
            
            print(f"📢 وصل إشعار جديد من سلة! الحدث: {event}")

            if event in ['order.updated', 'order.status.updated']:
                threading.Thread(
                    target=process_salla_webhook_async,
                    args=(raw_data,)
                ).start()
            else:
                print(f"⚠️ تم تجاهل الحدث (ليس تحديث طلب): {event}")

        except Exception as e:
            print(f"Salla Webhook Route Error: {str(e)}")
            
        return jsonify({"status": "received"}), 200


@app.route('/debug-salla', methods=['POST', 'GET'])
def debug_salla():
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True)
        print(f"🔍 DEBUG - Received raw data: {data}")
        print(f"🔍 DEBUG - Headers: {dict(request.headers)}")
        return jsonify({"status": "debug_received"}), 200
    return "Debug endpoint active - Send POST requests here to test", 200


# ==================== تشغيل التطبيق ====================

if __name__ == '__main__':
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
