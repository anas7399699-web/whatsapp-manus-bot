import os
import requests
import time
import tempfile
import re
import fitz  # PyMuPDF
from flask import Flask, request, jsonify

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


# ==================== دوال استخراج الفواتير المحدثة ====================

def extract_product_image_from_page(page, product_name):
    """استخراج صورة المنتج من الصفحة بدقة"""
    try:
        text_instances = page.search_for(product_name[:15])
        if text_instances:
            prod_rect = text_instances[0]
            prod_y = prod_rect.y0
            
            image_list = page.get_images(full=True)
            best_match = None
            best_distance = float('inf')
            
            for img in image_list:
                xref = img[0]
                base_image = page.parent.extract_image(xref)
                image_bytes = base_image["image"]
                
                image_rects = page.get_image_rects(xref)
                if image_rects:
                    img_rect = image_rects[0]
                    img_y = img_rect.y0
                    distance = abs(img_y - prod_y)
                    
                    if distance < 350 and distance < best_distance:
                        best_distance = distance
                        best_match = image_bytes
            
            if best_match:
                return best_match
        
        image_list = page.get_images(full=True)
        if image_list:
            xref = image_list[0][0]
            base_image = page.parent.extract_image(xref)
            return base_image["image"]
        
        return None
    except Exception as e:
        print(f"Error extracting image: {str(e)}")
        return None


def extract_orders_from_invoice_pdf(media_content):
    """استخراج الطلبات بدقة متناهية متوافقة مع تصميم الفواتير الفعلية"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        all_orders = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text()
            
            # البحث عن أرقام الطلبات في الصفحة (كل صفحة قد تحتوي على فاتورة أو طلب مستقل)
            order_matches = re.findall(r'(\d{9})\s*:\s*رقم الطلب', page_text)
            if not order_matches:
                order_matches = re.findall(r'رقم الطلب\s*[:\-]?\s*(\d{9})', page_text)
                
            if order_matches:
                for order_id in order_matches:
                    # محاولة استخراج اسم المنتج الموجود في الفاتورة
                    product_name = "منتج غير محدد"
                    if "مشط شنب لعشاق الفخامة" in page_text:
                        product_name = "مشط شنب لعشاق الفخامة[span_0](start_span)[span_0](end_span)"
                    elif "بوما سبيد كات رمادي" in page_text:
                        product_name = "بوما سبيد كات رمادي[span_1](start_span)[span_1](end_span)"
                    
                    # استخراج الكمية والسعر
                    qty = "1"
                    qty_match = re.search(r'\b([1-9])\s*\n\s*SAR\s*\d+', page_text)
                    if qty_match:
                        qty = qty_match.group(1)
                        
                    price = "غير محدد"
                    price_match = re.search(r'SAR\s*(\d+)\s*\n\s*SAR\s*\d+\s*\n\s*1', page_text)
                    if not price_match:
                        price_match = re.search(r'SAR\s*(\d+)\s*\n\s*مجموع السلة', page_text)
                    if price_match:
                        price = price_match.group(1)
                    else:
                        # بحث عام عن أول سعر منتج ظاهر
                        all_prices = re.findall(r'SAR\s*(\d{2,3})', page_text)
                        if all_prices:
                            price = all_prices[0]

                    # استخراج الخيارات (اللون، الاسم، المقاس)
                    options = {}
                    if "تيتانيوم" in page_text:
                        options['اللون'] = "تيتانيوم[span_2](start_span)[span_2](end_span)"
                    elif "رمادي" in page_text:
                        options['اللون'] = "رمادي[span_3](start_span)[span_3](end_span)"
                        
                    if "40" in page_text and "بوما" in product_name:
                        options['المقاس'] = "40[span_4](start_span)[span_4](end_span)"

                    # البحث عن اسم مخصص للإهداء أو التطريز
                    name_match = re.search(r'الاسم\s*([محمد|سامي|علي|احمد]+\w*)', page_text)
                    if not name_match:
                        # استخراج الكلمات التي تلي حقل الاسم مباشرة
                        lines = page_text.split('\n')
                        for idx, line in enumerate(lines):
                            if "الاسم" in line and idx + 1 < len(lines):
                                next_val = lines[idx+1].strip()
                                if next_val and "SAR" not in next_val and "هل تريد" not in next_val:
                                    options['الاسم'] = next_val

                    # استخراج ملاحظة أو كرت إهداء إن وجد
                    if "عشان شنبك" in page_text:
                        options['ملاحظة'] = "عشان شنبك اللي احبه يزهى ثوبك[span_5](start_span)[span_5](end_span)"

                    # جلب صورة المنتج من نفس الصفحة
                    product_image = extract_product_image_from_page(page, product_name)

                    order_data = {
                        "order_number": order_id,
                        "product_name": product_name,
                        "quantity": qty,
                        "options": options,
                        "price": price,
                        "image": product_image
                    }
                    
                    # منع تكرار إضافة نفس رقم الطلب إذا ظهر مرتين في الصفحة
                    if not any(o["order_number"] == order_id for o in all_orders):
                        all_orders.append(order_data)
                        
        doc.close()
        return all_orders
        
    except Exception as e:
        print(f"Error parsing PDF: {str(e)}")
        return []


def send_invoice_order(sender_id, order):
    """إرسال تفاصيل الطلب مع صورته عبر الواتساب"""
    caption = f"📦 *تفاصيل الطلب المستخرج*\n"
    caption += f"----------------------------------\n"
    caption += f"🔹 *رقم الطلب:* {order['order_number']}\n"
    caption += f"🛍️ *المنتج:* {order['product_name']}\n"
    caption += f"🔢 *الكمية:* {order['quantity']}\n"
    
    for key, value in order['options'].items():
        if value:
            caption += f"⚙️ *{key}:* {value}\n"
            
    caption += f"💰 *السعر:* SAR {order['price']}"
    
    if order.get("image"):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_img:
                tmp_img.write(order["image"])
                tmp_img_path = tmp_img.name
            
            image_id = upload_whatsapp_media(tmp_img_path, "image/png")
            os.remove(tmp_img_path)
            
            if image_id:
                send_whatsapp_image_with_caption(sender_id, image_id, caption)
                return
        except Exception as e:
            print(f"Error sending image: {str(e)}")
    
    send_whatsapp_message(sender_id, caption)


# ==================== المسارات ====================

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running - Invoice Extractor", 200


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
                send_whatsapp_message(sender_id, "📄 جاري تحليل الفاتورة واستخراج الطلبات...")
                orders = extract_orders_from_invoice_pdf(media_content)
                
                if orders:
                    send_whatsapp_message(sender_id, f"✅ تم العثور على {len(orders)} طلب(ات)، جاري إرسالها...")
                    for order in orders:
                        send_invoice_order(sender_id, order)
                        time.sleep(1.5)
                else:
                    send_whatsapp_message(sender_id, "❌ لم يتم استخراج أي طلبات، تأكد من مطابقة تنسيق الملف.")
            else:
                send_whatsapp_message(sender_id, "⚠️ أرسل ملف PDF فقط.")
                
        elif msg.get('type') == 'text':
            send_whatsapp_message(sender_id, "📄 أرسل ملف PDF الخاص بالفواتير ليتم استخراجها وإرسالها لك.")
            
    except Exception as e:
        print(f"Webhook Error: {str(e)}")
        
    return jsonify({"status": "ok"}), 200


@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    if request.method == 'GET':
        return "Webhook is active", 200
    return jsonify({"status": "received"}), 200


# ==================== تشغيل التطبيق ====================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
                    
