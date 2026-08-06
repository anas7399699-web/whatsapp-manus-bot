import os
import requests
import time
import tempfile
import re
import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, request, jsonify
import io

app = Flask(__name__)

# ==================== إعدادات Render ====================
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ==================== دوال واتساب ====================

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


def send_whatsapp_image(to, media_id):
    """إرسال الصورة وحدها بعد أن أصبحت البيانات مكتوبة عليها مباشرة"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id}
    }
    requests.post(url, headers=headers, json=data)


# ==================== دالة دمج النصوص داخل الصورة ====================

def draw_text_on_image(image_bytes, order):
    """رسم بيانات الطلب مباشرة على الصورة"""
    try:
        # فتح الصورة من بايتات البيانات
        base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img_w, img_h = base_image.size

        # إنشاء لوحة بيضاء أوسع بجانب الصورة أو مساحة مخصصة للنصوص
        canvas_width = img_w + 500  # إضافة مساحة جانبية للكتابة
        canvas_height = max(img_h, 800)
        
        new_img = Image.new("RGBA", (canvas_width, canvas_height), "white")
        # لصق صورة المنتج في الجانب الأيمن
        new_img.paste(base_image, (canvas_width - img_w - 20, (canvas_height - img_h) // 2))

        draw = ImageDraw.Draw(new_img)
        
        try:
            font_large = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
            font_medium = ImageFont.truetype("DejaVuSans.ttf", 28)
        except:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()

        # تجهيز النصوص المراد كتابتها بترتيب مثل الصورة
        text_lines = []
        text_lines.append(f"{order['order_number']}")
        text_lines.append("")
        
        for key, value in order['options'].items():
            if value:
                text_lines.append(f"{key}")
                text_lines.append(f"{value}")
                text_lines.append("")
                
        text_lines.append(f"المنتج: {order['product_name']}")
        text_lines.append(f"السعر: SAR {order['price']}")

        # كتابة النصوص على الصورة
        current_y = 50
        current_x = 40
        
        for line in text_lines:
            if line == order['order_number']:
                draw.text((current_x, current_y), line, fill="blue", font=font_large)
                current_y += 55
            else:
                draw.text((current_x, current_y), line, fill="black", font=font_medium)
                current_y += 40

        # تحويل الصورة النهائية إلى بايتات لإرسالها
        output_io = io.BytesIO()
        new_img.convert("RGB").save(output_io, format="JPEG")
        output_io.seek(0)
        return output_io.read()

    except Exception as e:
        print(f"Error drawing on image: {str(e)}")
        return image_bytes


# ==================== دوال استخراج الفواتير ====================

def extract_product_image_from_page(page, product_name, image_index=0):
    """استخراج صورة المنتج بناءً على ترتيبها في الصفحة لخدمة تعدد المنتجات"""
    try:
        image_list = page.get_images(full=True)
        if image_list and image_index < len(image_list):
            xref = image_list[image_index][0]
            base_image = page.parent.extract_image(xref)
            return base_image["image"]
        return None
    except Exception as e:
        print(f"Error extracting image: {str(e)}")
        return None


def extract_orders_from_invoice_pdf(media_content):
    """استخراج كافة المنتجات من الطلبات وتطهير النصوص تماماً"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        all_orders = []
        
        # قائمة المنتجات المعروفة للبحث عنها
        known_products = [
            "مشط شنب لعشاق الفخامة",
            "بوما سبيد كات رمادي",
            "كفر جوال أنيق و مميز بالاسم حسب الطلب",
            "فنجال الزهور مودرن الصيفي"
        ]
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text()
            
            # استخراج رقم الطلب
            order_matches = re.findall(r'(\d{9})\s*:\s*رقم الطلب', page_text)
            if not order_matches:
                order_matches = re.findall(r'رقم الطلب\s*[:\-]?\s*(\d{9})', page_text)
                
            if order_matches:
                order_id = order_matches[0]
                
                # --- التعديل الجوهري: البحث عن كل المنتجات في الصفحة الواحدة ---
                found_products_in_page = []
                for p_name in known_products:
                    for match in re.finditer(re.escape(p_name), page_text):
                        found_products_in_page.append({"name": p_name, "start": match.start()})
                
                # ترتيب المنتجات حسب ظهورها في الصفحة
                found_products_in_page.sort(key=lambda x: x['start'])
                
                for i, prod in enumerate(found_products_in_page):
                    # تحديد سياق النص الخاص بهذا المنتج فقط
                    next_start = found_products_in_page[i+1]['start'] if i+1 < len(found_products_in_page) else len(page_text)
                    prod_context = page_text[prod['start']:next_start]
                    
                    product_name = prod['name']
                    
                    # استخراج الكمية والسعر من سياق المنتج
                    qty = "1"
                    qty_match = re.search(r'\b([1-9])\s*\n\s*SAR\s*\d+', prod_context)
                    if qty_match: qty = qty_match.group(1)
                        
                    price = "غير محدد"
                    price_match = re.search(r'SAR\s*(\d+)', prod_context)
                    if price_match: price = price_match.group(1)

                    # استخراج الخيارات من سياق المنتج
                    options = {}
                    if "تيتانيوم" in prod_context: options['اللون'] = "تيتانيوم"
                    elif "رمادي" in prod_context: options['اللون'] = "رمادي"
                    if "40" in prod_context and "بوما" in product_name: options['المقاس'] = "40"
                    if "هل تريد إضافة الاسم" in prod_context:
                        if "لا" in prod_context: options['هل تريد إضافة الاسم'] = "لا"
                        elif "نعم" in prod_context: options['هل تريد إضافة الاسم'] = "نعم"
                    if "عشان شنبك" in prod_context: options['ملاحظة'] = "عشان شنبك اللي احبه يزهى ثوبك"
                    
                    name_match = re.search(r'الاسم\n([^\n]+)', prod_context)
                    if name_match: options['الاسم'] = name_match.group(1).strip()

                    # استخراج الصورة المرتبطة بالمنتج (بناءً على الترتيب)
                    product_image = extract_product_image_from_page(page, product_name, image_index=i)

                    order_data = {
                        "order_number": order_id,
                        "product_name": product_name,
                        "quantity": qty,
                        "options": options,
                        "price": price,
                        "image": product_image
                    }
                    all_orders.append(order_data)
                        
        doc.close()
        return all_orders
        
    except Exception as e:
        print(f"Error parsing PDF: {str(e)}")
        return []


def send_invoice_order(sender_id, order):
    """دمج النصوص داخل الصورة ثم إرسال الصورة وحدها"""
    if order.get("image"):
        try:
            processed_image_bytes = draw_text_on_image(order["image"], order)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_img:
                tmp_img.write(processed_image_bytes)
                tmp_img_path = tmp_img.name
            
            image_id = upload_whatsapp_media(tmp_img_path, "image/jpeg")
            os.remove(tmp_img_path)
            
            if image_id:
                send_whatsapp_image(sender_id, image_id)
                return
        except Exception as e:
            print(f"Error processing image: {str(e)}")
    
    fallback_text = f"{order['order_number']}\n\n"
    for key, value in order['options'].items():
        if value:
            fallback_text += f"{key}\n{value}\n\n"
    fallback_text += f"المنتج: {order['product_name']}\nالسعر: SAR {order['price']}"
    send_whatsapp_message(sender_id, fallback_text)


# ==================== المسارات ====================

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running - Direct Image Text Renderer", 200


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge'), 200
        return 'Forbidden', 403

    data = request.json
    try:
        msg = data['entry'][0]['changes'][0]['value']['messages'][0]
        sender_id = msg.get('from')
        
        if msg.get('type') == 'document':
            doc = msg['document']
            mime_type = doc.get('mime_type', '')
            filename = doc.get('filename', '').lower()
            media_id = doc.get('id')
            
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
            res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers).json()
            media_url = res.get('url')
            if not media_url:
                return jsonify({"status": "error"}), 200
            
            media_content = requests.get(media_url, headers=headers).content
            
            if 'pdf' in mime_type or filename.endswith('.pdf'):
                send_whatsapp_message(sender_id, "📄 جاري تحليل الفاتورة ودمج البيانات على الصورة...")
                orders = extract_orders_from_invoice_pdf(media_content)
                
                if orders:
                    send_whatsapp_message(sender_id, f"✅ تم العثور على {len(orders)} منتج(ات)")
                    for order in orders:
                        send_invoice_order(sender_id, order)
                        time.sleep(1.5)
                else:
                    send_whatsapp_message(sender_id, "❌ لم يتم استخراج أي طلبات.")
            else:
                send_whatsapp_message(sender_id, "⚠️ أرسل ملف PDF فقط.")
                
        elif msg.get('type') == 'text':
            send_whatsapp_message(sender_id, "📄 أرسل ملف PDF الخاص بالفواتير ليتم استخراجها ودمجها على الصور.")
            
    except Exception as e:
        print(f"Webhook Error: {str(e)}")
        
    return jsonify({"status": "ok"}), 200


@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    if request.method == 'GET':
        return "Webhook is active", 200
    return jsonify({"status": "received"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
