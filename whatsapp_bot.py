# التحديث الأخير: 2026-08-19 - حذف ويب هوك سلة مع الإبقاء على PDF و Excel
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
import pandas as pd
import logging
from datetime import datetime

# ==================== إعدادات التسجيل (Logging) ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ==================== إعدادات Render ====================
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ==================== الذواكر المؤقتة ====================
processed_messages = set()
user_temp_data = {}
user_temp_expiry = {}

# ==================== إعدادات إضافية ====================
MAX_RETRIES = 3
RETRY_DELAY = 5

# ==================== دوال واتساب الأساسية ====================

def safe_send_message(to, text, retries=MAX_RETRIES):
    """إرسال رسالة مع إعادة المحاولة في حالة الفشل"""
    for attempt in range(retries):
        try:
            send_whatsapp_message(to, text)
            return True
        except Exception as e:
            logger.error(f"خطأ في إرسال الرسالة (محاولة {attempt+1}/{retries}): {str(e)}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY)
    return False

def send_whatsapp_message(to, text):
    """إرسال رسالة نصية عبر واتساب"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code != 200:
            logger.error(f"فشل إرسال الرسالة: {response.status_code} - {response.text}")
        return response
    except Exception as e:
        logger.error(f"خطأ في إرسال الرسالة: {str(e)}")
        raise

def upload_whatsapp_media(file_path, mime_type):
    """رفع ملف إلى واتساب"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {'file': (os.path.basename(file_path), open(file_path, 'rb'), mime_type)}
    data = {'messaging_product': 'whatsapp'}
    try:
        response = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        if response.status_code == 200:
            return response.json().get('id')
        else:
            logger.error(f"فشل رفع الملف: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"خطأ في رفع الملف: {str(e)}")
        return None

def send_whatsapp_image_with_caption(to, media_id, caption):
    """إرسال صورة مع تعليق عبر واتساب"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, "caption": caption}
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        return response
    except Exception as e:
        logger.error(f"خطأ في إرسال الصورة: {str(e)}")
        raise

# ==================== دالة تنظيف رقم الجوال ====================
def clean_phone_number(phone):
    """
    تنظيف رقم الجوال فقط من المسافات والعلامات غير الضرورية
    مع الاحتفاظ بعلامة + كما هي في الملف الأصلي
    """
    if not phone or phone == 'غير محدد':
        return phone
    
    # تحويل إلى نص وإزالة المسافات الزائدة
    phone = str(phone).strip()
    phone = ' '.join(phone.split())
    
    # الاحتفاظ بعلامة + إذا كانت موجودة، أو إضافتها إذا كان الرقم يبدأ بـ 966
    if phone and not phone.startswith('+'):
        # إذا كان الرقم يبدأ بـ 966، نضيف +
        if phone.startswith('966'):
            phone = '+' + phone
        # إذا كان الرقم يبدأ بـ 0، نزيل الصفر ونضيف +966
        elif phone.startswith('0') and len(phone) >= 10:
            phone = '+966' + phone[1:]
        # إذا كان الرقم يتكون من 9 أرقام ويبدو كرقم سعودي
        elif phone.isdigit() and len(phone) == 9:
            phone = '+966' + phone
        # إذا كان الرقم يتكون من 10 أرقام ويبدأ بـ 5
        elif phone.isdigit() and len(phone) == 10 and phone.startswith('5'):
            phone = '+966' + phone
    
    return phone

# ==================== معالجة ملفات PDF ====================

def handle_pdf_logic(sender_id, media_content):
    """استخراج بوالص الشحن من ملف PDF وتحويلها إلى صور"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        total_pages = len(doc)
        safe_send_message(sender_id, f"📄 جاري استخراج {total_pages} بوالص شحن... ⏳")
        
        success_count = 0
        for page_num in range(total_pages):
            try:
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
                        success_count += 1
                    os.remove(tmp_img.name)
                
                if page_num < total_pages - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"خطأ في معالجة الصفحة {page_num+1}: {str(e)}")
                safe_send_message(sender_id, f"⚠️ خطأ في الصفحة {page_num+1}: {str(e)[:50]}")
        
        safe_send_message(sender_id, f"✅ تم إرسال {success_count} من {total_pages} بوالص بنجاح.")
    except Exception as e:
        logger.error(f"خطأ عام في PDF: {str(e)}")
        safe_send_message(sender_id, "❌ حدث خطأ في معالجة ملف البوالص.")

# ==================== معالجة Excel (الرسائل المنفصلة) ====================

def send_orders_as_messages(sender_id, orders, region_name):
    """إرسال الطلبات كرسائل منفصلة"""
    if not orders:
        safe_send_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    safe_send_message(sender_id, f"📍 *{region_name}:*")
    time.sleep(2)
    
    for index, order in enumerate(orders):
        try:
            safe_send_message(sender_id, order)
            time.sleep(2)
            if (index + 1) % 10 == 0:
                safe_send_message(sender_id, f"⏳ تم إرسال {index + 1} من {len(orders)}...")
                time.sleep(6)
        except Exception as e:
            logger.error(f"خطأ في إرسال الطلب {index+1}: {str(e)}")
    
    safe_send_message(sender_id, f"✅ تم إرسال {len(orders)} طلب لـ {region_name}")

# ==================== معالجة Excel (ملف Excel) ====================

def send_orders_as_excel(sender_id, orders_data, region_name):
    """إرسال الطلبات كملف Excel"""
    if not orders_data:
        safe_send_message(sender_id, f"⚠️ لا توجد طلبات في {region_name}")
        return
    
    try:
        df = pd.DataFrame(orders_data)
        
        columns_order = [
            'عنوان العميل',
            'المدينة',
            'رقم الطلب',
            'رقم الجوال',
            'اسم العميل',
            'الرمز البريدي',
            'رقم الشارع',
            'معرف الحي',
            'العنوان الوطني المختصر',
            'رقم المبنى',
            'الرقم الإضافي'
        ]
        
        for col in columns_order:
            if col not in df.columns:
                df[col] = ''
        
        df = df[columns_order]
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            output_path = tmp.name
            df.to_excel(output_path, index=False, sheet_name=region_name)
        
        media_id = upload_whatsapp_media(
            output_path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        if media_id:
            url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
            data = {
                "messaging_product": "whatsapp",
                "to": sender_id,
                "type": "document",
                "document": {
                    "id": media_id,
                    "caption": f"📊 طلبات {region_name}\n📦 إجمالي الطلبات: {len(orders_data)}",
                    "filename": f"{region_name}_{len(orders_data)}_طلب.xlsx"
                }
            }
            response = requests.post(url, headers=headers, json=data, timeout=30)
            if response.status_code == 200:
                safe_send_message(sender_id, f"✅ تم إرسال ملف Excel لـ {region_name}\nعدد الطلبات: {len(orders_data)}")
            else:
                logger.error(f"فشل إرسال ملف Excel: {response.text}")
                safe_send_message(sender_id, f"❌ فشل في إرسال ملف {region_name}")
        else:
            safe_send_message(sender_id, f"❌ فشل في رفع ملف {region_name}")
        
        os.remove(output_path)
        
    except Exception as e:
        logger.error(f"خطأ في معالجة Excel: {str(e)}")
        safe_send_message(sender_id, f"❌ خطأ: {str(e)[:100]}")

# ==================== معالجة الملفات الرئيسية ====================

def handle_document_async(sender_id, doc):
    """معالجة الملفات المرسلة عبر واتساب (Excel أو PDF)"""
    mime_type = doc.get('mime_type', '')
    filename = doc.get('filename', '').lower()
    media_id = doc.get('id')
    
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    
    try:
        res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers, timeout=30)
        if res.status_code != 200:
            logger.error(f"فشل الحصول على معلومات الملف: {res.text}")
            safe_send_message(sender_id, "❌ فشل في تحميل الملف")
            return
        media_url = res.json().get('url')
        if not media_url:
            logger.error("لم يتم العثور على URL للملف")
            safe_send_message(sender_id, "❌ فشل في الحصول على رابط الملف")
            return
    except Exception as e:
        logger.error(f"خطأ في تحميل الملف: {str(e)}")
        safe_send_message(sender_id, "❌ حدث خطأ في تحميل الملف")
        return
    
    try:
        media_content = requests.get(media_url, headers=headers, timeout=60).content
    except Exception as e:
        logger.error(f"خطأ في تحميل محتوى الملف: {str(e)}")
        safe_send_message(sender_id, "❌ حدث خطأ في تحميل محتوى الملف")
        return

    # معالجة ملفات Excel
    if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
        safe_send_message(sender_id, "📥 جاري تحليل ملف الإكسل... ⏳")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(media_content)
            path = tmp.name
        
        try:
            df_original = pd.read_excel(path)
            df_original = df_original.fillna('')
            
            logger.info(f"📋 أسماء الأعمدة في الملف: {df_original.columns.tolist()}")
            
            riyadh_orders = []
            other_orders = []
            
            # ===== البحث عن أعمدة المستلم =====
            recipient_name_col = None
            recipient_phone_col = None
            
            # البحث عن اسم المستلم
            name_candidates = ['إسم المستلم الثاني', 'اسم المستلم', 'اسم المستلم الثاني']
            for col in name_candidates:
                if col in df_original.columns:
                    recipient_name_col = col
                    logger.info(f"✅ تم العثور على عمود اسم المستلم: '{recipient_name_col}'")
                    break
            
            if not recipient_name_col:
                for col in df_original.columns:
                    col_lower = col.lower()
                    if 'مستلم' in col and ('اسم' in col or 'إسم' in col):
                        recipient_name_col = col
                        logger.info(f"✅ تم العثور على عمود اسم المستلم البديل: '{recipient_name_col}'")
                        break
            
            # البحث عن جوال المستلم
            phone_candidates = ['جوال المستلم', 'رقم المستلم']
            for col in phone_candidates:
                if col in df_original.columns:
                    recipient_phone_col = col
                    logger.info(f"✅ تم العثور على عمود جوال المستلم: '{recipient_phone_col}'")
                    break
            
            if not recipient_phone_col:
                for col in df_original.columns:
                    col_lower = col.lower()
                    if 'مستلم' in col and ('جوال' in col or 'رقم' in col or 'phone' in col_lower or 'mobile' in col_lower):
                        recipient_phone_col = col
                        logger.info(f"✅ تم العثور على عمود جوال المستلم البديل: '{recipient_phone_col}'")
                        break
            
            # ===== البحث عن أعمدة العميل (دائماً كحل احتياطي) =====
            customer_name_col = None
            customer_phone_col = None
            
            # البحث عن اسم العميل
            for col in df_original.columns:
                if 'اسم العميل' in col or 'اسم عميل' in col:
                    customer_name_col = col
                    logger.info(f"✅ تم العثور على عمود اسم العميل: '{customer_name_col}'")
                    break
            
            if not customer_name_col:
                for col in df_original.columns:
                    col_lower = col.lower()
                    if 'اسم' in col or 'name' in col_lower:
                        if 'مستلم' not in col:
                            customer_name_col = col
                            logger.info(f"✅ تم العثور على عمود اسم بديل: '{customer_name_col}'")
                            break
            
            # البحث عن رقم الجوال
            for col in df_original.columns:
                if 'رقم الجوال' in col or 'جوال العميل' in col:
                    customer_phone_col = col
                    logger.info(f"✅ تم العثور على عمود رقم الجوال: '{customer_phone_col}'")
                    break
            
            if not customer_phone_col:
                for col in df_original.columns:
                    col_lower = col.lower()
                    if 'جوال' in col or 'رقم' in col or 'phone' in col_lower or 'mobile' in col_lower:
                        if 'مستلم' not in col:
                            customer_phone_col = col
                            logger.info(f"✅ تم العثور على عمود رقم بديل: '{customer_phone_col}'")
                            break
            
            # ===== تحديد عمود المدينة =====
            city_column = None
            possible_city_names = ['المدينة', 'city', 'City', 'مدينة']
            for col in possible_city_names:
                if col in df_original.columns:
                    city_column = col
                    break
            
            if city_column is None:
                logger.warning("لم يتم العثور على عمود المدينة، استخدام 'المدينة' كافتراضي")
                city_column = 'المدينة'
                if city_column not in df_original.columns:
                    df_original[city_column] = ''
            
            # دالة للتحقق من القيمة الفارغة
            def is_empty(value):
                if value is None:
                    return True
                if pd.isna(value):
                    return True
                str_val = str(value).strip()
                if str_val == '' or str_val.lower() in ['nan', 'none', 'null', 'na']:
                    return True
                return False

            # ===== معالجة كل صف =====
            for index, row in df_original.iterrows():
                city = str(row.get(city_column, '')).strip()
                address = str(row.get('عنوان العميل', '')).strip()
                
                # محاولة استخراج المدينة من العنوان إذا كانت فارغة
                if not city and address:
                    if 'الرياض' in address or 'Riyadh' in address:
                        city = 'الرياض'
                    elif ' - ' in address:
                        city = address.split(' - ')[0].strip()
                    elif '،' in address:
                        city = address.split('،')[0].strip()
                
                # ===== الأولوية: بيانات المستلم، ثم العميل =====
                recipient_name = ''
                recipient_phone = ''

                # 1. محاولة الحصول على اسم المستلم (إذا كان العمود موجوداً)
                if recipient_name_col:
                    if not is_empty(row.get(recipient_name_col)):
                        recipient_name = str(row.get(recipient_name_col)).strip()
                        logger.info(f"✅ استخدام اسم المستلم: '{recipient_name}'")

                # 2. إذا لم نجد اسم المستلم، نستخدم اسم العميل
                if not recipient_name and customer_name_col:
                    if not is_empty(row.get(customer_name_col)):
                        recipient_name = str(row.get(customer_name_col)).strip()
                        logger.info(f"✅ استخدام اسم العميل (حل احتياطي): '{recipient_name}'")

                # 3. إذا لم نجد أي اسم، نضع قيمة افتراضية
                if not recipient_name:
                    recipient_name = 'غير محدد'

                # 4. محاولة الحصول على جوال المستلم (إذا كان العمود موجوداً)
                if recipient_phone_col:
                    if not is_empty(row.get(recipient_phone_col)):
                        recipient_phone = str(row.get(recipient_phone_col)).strip()
                        logger.info(f"✅ استخدام جوال المستلم: '{recipient_phone}'")

                # 5. إذا لم نجد جوال المستلم، نستخدم جوال العميل
                if not recipient_phone and customer_phone_col:
                    if not is_empty(row.get(customer_phone_col)):
                        recipient_phone = str(row.get(customer_phone_col)).strip()
                        logger.info(f"✅ استخدام جوال العميل (حل احتياطي): '{recipient_phone}'")

                # 6. إذا لم نجد أي رقم، نضع قيمة افتراضية
                if not recipient_phone:
                    recipient_phone = 'غير محدد'
                else:
                    recipient_phone = clean_phone_number(recipient_phone)
                
                # إنشاء قاموس الطلب
                order_dict = {
                    'عنوان العميل': address,
                    'المدينة': city,
                    'رقم الطلب': str(row.get('رقم الطلب', '')),
                    'رقم الجوال': recipient_phone,
                    'اسم العميل': recipient_name,
                    'الرمز البريدي': str(row.get('الرمز البريدي', '')),
                    'رقم الشارع': str(row.get('رقم الشارع', '')),
                    'معرف الحي': str(row.get('معرف الحي', '')),
                    'العنوان الوطني المختصر': str(row.get('العنوان الوطني المختصر', '')),
                    'رقم المبنى': str(row.get('رقم المبنى', '')),
                    'الرقم الإضافي': str(row.get('الرقم الإضافي', ''))
                }
                
                # تصنيف الطلب حسب المدينة
                city_lower = city.lower()
                if 'الرياض' in city or 'riyadh' in city_lower:
                    riyadh_orders.append(order_dict)
                else:
                    other_orders.append(order_dict)
            
            # تخزين النتائج مع صلاحية 30 دقيقة
            user_temp_data[sender_id] = {
                "riyadh": riyadh_orders,
                "others": other_orders
            }
            user_temp_expiry[sender_id] = time.time() + 1800
            
            # عرض الخيارات للمستخدم
            options = f"📊 *نتائج التحليل:*\n"
            options += "*اختر طريقة الاستلام:*\n\n"
            options += f"📍 الرياض: {len(riyadh_orders)} طلب\n"
            options += f"🏠 باقي المناطق: {len(other_orders)} طلب\n\n"
            options += "*اختر طريقة الاستلام:*\n\n"
            options += "1️⃣ أرسل 'رياض رسائل' - لاستلام طلبات الرياض كرسائل منفصلة\n"
            options += "2️⃣ أرسل 'رياض اكسل' - لاستلام طلبات الرياض كملف Excel\n"
            options += "3️⃣ أرسل 'باقي رسائل' - لاستلام طلبات باقي المناطق كرسائل منفصلة\n"  # ← جديد
            options += "4️⃣ أرسل 'باقي اكسل' - لاستلام طلبات باقي المناطق كملف Excel\n"
            options += "5️⃣ أرسل 'الكل اكسل' - لاستلام جميع الطلبات في ملف Excel واحد\n"
            options += "6️⃣ أرسل 'مسح' - لحذف البيانات المؤقتة"
            
            safe_send_message(sender_id, options)
            
        except Exception as e:
            logger.error(f"خطأ في معالجة Excel: {str(e)}")
            safe_send_message(sender_id, f"❌ حدث خطأ: {str(e)[:100]}")
        finally:
            if os.path.exists(path):
                os.remove(path)

    # معالجة ملفات PDF
    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)
    else:
        safe_send_message(sender_id, "❌ نوع الملف غير مدعوم. أرسل ملف Excel أو PDF")

# ==================== دالة منع نوم Render ====================

def keep_alive():
    """منع خدمة Render من الدخول في وضع السبات"""
    RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not RENDER_URL:
        logger.warning("RENDER_EXTERNAL_URL غير مضبوط")
        return
    while True:
        try:
            time.sleep(600)
            response = requests.get(f"{RENDER_URL}/", timeout=10)
            logger.info(f"Keep-alive ping: {response.status_code}")
        except Exception as e:
            logger.error(f"خطأ في keep-alive: {str(e)}")

# ==================== المسارات ====================

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running", 200

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_sessions": len(user_temp_data)
    }), 200

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """مسار استقبال رسائل واتساب"""
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
            logger.info(f"تجاهل رسالة قديمة من {sender_id}")
            return jsonify({"status": "ignored_old_message"}), 200

        if msg_id in processed_messages:
            logger.info(f"تجاهل رسالة مكررة من {sender_id}")
            return jsonify({"status": "duplicate"}), 200

        processed_messages.add(msg_id)
        if len(processed_messages) > 1000:
            processed_messages.clear()

        if msg.get('type') == 'document':
            logger.info(f"استلام ملف من {sender_id}")
            threading.Thread(target=handle_document_async, args=(sender_id, msg['document'])).start()

        elif msg.get('type') == 'text':
    text_body = msg.get('text', {}).get('body', '').strip()
    text_lower = text_body.lower()

    if sender_id in user_temp_data:
        if sender_id in user_temp_expiry and time.time() > user_temp_expiry[sender_id]:
            del user_temp_data[sender_id]
            del user_temp_expiry[sender_id]
            safe_send_message(sender_id, "⏰ انتهت صلاحية بيانات الطلبات. أرسل ملف Excel مرة أخرى.")
        else:
            data_store = user_temp_data[sender_id]
            riyadh_orders = data_store["riyadh"]
            other_orders = data_store["others"]

            user_temp_expiry[sender_id] = time.time() + 1800

            if "رياض رسائل" in text_lower or "رياض رسائل" in text_body:
                riyadh_texts = []
                for order in riyadh_orders:
                    text = (
                        f"**العنوان /** {order.get('عنوان العميل', '')}\n"
                        f"**رقم الطلبية /** {order.get('رقم الطلب', '')}\n"
                        f"**رقم المستلم /** {order.get('رقم الجوال', '')}\n"
                        f"**اسم المستلم /** {order.get('اسم العميل', '')}"
                    )
                    riyadh_texts.append(text)
                threading.Thread(target=send_orders_as_messages, args=(sender_id, riyadh_texts, "الرياض")).start()

            elif "رياض اكسل" in text_lower or "رياض اكسل" in text_body or "رياض excel" in text_lower:
                threading.Thread(target=send_orders_as_excel, args=(sender_id, riyadh_orders, "الرياض")).start()

            elif "باقي رسائل" in text_lower or "باقي رسائل" in text_body:
                other_texts = []
                for order in other_orders:
                    text = (
                        f"**العنوان /** {order.get('عنوان العميل', '')}\n"
                        f"**رقم الطلبية /** {order.get('رقم الطلب', '')}\n"
                        f"**رقم المستلم /** {order.get('رقم الجوال', '')}\n"
                        f"**اسم المستلم /** {order.get('اسم العميل', '')}"
                    )
                    other_texts.append(text)
                threading.Thread(target=send_orders_as_messages, args=(sender_id, other_texts, "باقي المناطق")).start()

            elif "باقي اكسل" in text_lower or "باقي اكسل" in text_body or "باقي excel" in text_lower:
                threading.Thread(target=send_orders_as_excel, args=(sender_id, other_orders, "باقي المناطق")).start()

            elif "الكل اكسل" in text_lower or "الكل اكسل" in text_body or "الكل excel" in text_lower:
                all_orders = riyadh_orders + other_orders
                threading.Thread(target=send_orders_as_excel, args=(sender_id, all_orders, "جميع الطلبات")).start()

            elif "مسح" in text_lower or "انهاء" in text_lower or "حذف" in text_lower:
                if sender_id in user_temp_data:
                    del user_temp_data[sender_id]
                if sender_id in user_temp_expiry:
                    del user_temp_expiry[sender_id]
                safe_send_message(sender_id, "✅ تم مسح بيانات الطلبات المؤقتة.")
            else:
                safe_send_message(sender_id, "❌ خيار غير صحيح. الأوامر المتاحة: رياض رسائل، رياض اكسل، باقي رسائل، باقي اكسل، الكل اكسل، مسح")
    else:
        safe_send_message(sender_id, "أهلاً! أرسل ملف Excel لفرز الطلبات، أو PDF لاستخراج البوالص.")

    except KeyError as e:
        logger.warning(f"مفتاح مفقود في البيانات: {str(e)}")
    except Exception as e:
        logger.error(f"خطأ في webhook: {str(e)}")

    return jsonify({"status": "ok"}), 200


# ==================== تشغيل التطبيق ====================

if __name__ == '__main__':
    logger.info("🚀 بدء تشغيل بوت واتساب...")
    threading.Thread(target=keep_alive, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"✅ البوت يعمل على المنفذ {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
