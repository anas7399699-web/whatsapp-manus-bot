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


# ==================== دوال استخراج الفواتير ====================

def extract_product_image_from_page(page, product_name):
    """استخراج صورة المنتج من الصفحة"""
    try:
        text_instances = page.search_for(product_name[:20])
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
                    
                    if distance < 300 and distance < best_distance:
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
    """استخراج الطلبات من فواتير PDF"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        all_orders = []
        current_order = None
        current_page_text = ""
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text()
            
            # البحث عن رقم الطلب
            order_match = re.search(r'رقم الطلب\s*[::\-]\s*(\d+)', page_text)
            if not order_match:
                order_match = re.search(r'#(\d{9,})', page_text)
            
            if order_match:
                new_order_number = order_match.group(1)
                
                if current_order:
                    first_page = current_order["pages"][0] - 1
                    if 0 <= first_page < len(doc):
                        img_page = doc.load_page(first_page)
                        product_image = extract_product_image_from_page(img_page, current_order["product_name"])
                        current_order["image"] = product_image
                    all_orders.append(current_order)
                
                current_order = {
                    "order_number": new_order_number,
                    "product_name": "منتج غير محدد",
                    "quantity": "1",
                    "options": {},
                    "price": "غير محدد",
                    "image": None,
                    "pages": [page_num + 1]
                }
                current_page_text = page_text
            else:
                if current_order:
                    current_page_text += "\n" + page_text
                    current_order["pages"].append(page_num + 1)
            
            if current_order:
                product_match = re.search(r'المنتج\s*([^\n]+?)(?=\s*SAR|\s*الكمية|\n)', current_page_text)
                if not product_match:
                    product_match = re.search(r'([^\n]+?)\s*SAR\s*\d+', current_page_text)
                if product_match:
                    current_order["product_name"] = product_match.group(1).strip()
                
                qty_match = re.search(r'الكمية\s*[::\-]\s*(\d+)', current_page_text)
                if qty_match:
                    current_order["quantity"] = qty_match.group(1)
                
                price_match = re.search(r'SAR\s*(\d+)', current_page_text)
                if price_match:
                    current_order["price"] = price_match.group(1)
                
                options = {}
                color_match = re.search(r'اللون\s*[::\-]\s*([^\n]+)', current_page_text)
                if color_match:
                    options['اللون'] = color_match.group(1).strip()
                
                add_name_match = re.search(r'هل تريد إضافة الاسم\s*[::\-]\s*([^\n]+)', current_page_text)
                if add_name_match:
                    options['إضافة الاسم'] = add_name_match.group(1).strip()
                
                name_match = re.search(r'الاسم\s*[::\-]\s*([^\n]+)', current_page_text)
                if name_match:
                    options['الاسم'] = name_match.group(1).strip()
                
                size_match = re.search(r'المقاس\s*[::\-]\s*([^\n]+)', current_page_text)
                if size_match:
                    options['المقاس'] = size_match.group(1).strip()
                
                extra_options = re.findall(r'([^:]+)\s*[::\-]\s*([^\n]+)', current_page_text)
                for key, value in extra_options:
                    key_clean = key.strip()
                    if key_clean not in ['اللون', 'إضافة الاسم', 'الاسم', 'المقاس', 'المنتج', 'الكمية']:
                        options[key_clean] = value.strip()
                
                current_order["options"] = options
        
        if current_order:
            first_page = current_order["pages"][0] - 1
            if 0 <= first_page < len(doc):
                img_page = doc.load_page(first_page)
                product_image = extract_product_image_from_page(img_page, current_order["product_name"])
                current_order["image"] = product_image
            all_orders.append(current_order)
        
        doc.close()
        return all_orders
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return []


def send_invoice_order(sender_id, order):
    """إرسال طلب مستخرج من فاتورة"""
    caption = f"*رقم الطلب:* {order['order_number']}\n"
    caption += f"*المنتج:* {order['product_name']}\n"
    caption += f"*الكمية:* {order['quantity']}\n"
    for key, value in order['options'].items():
        if value:
            caption += f"*{key}:* {value}\n"
    caption += f"*السعر:* SAR {order['price']}"
    
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
    return "Bot is running - Invoice Tester", 200


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
            
            # معالجة ملفات PDF
            if 'pdf' in mime_type or filename.endswith('.pdf'):
                send_whatsapp_message(sender_id, "📄 جاري تحليل الفاتورة...")
                orders = extract_orders_from_invoice_pdf(media_content)
                if orders:
                    send_whatsapp_message(sender_id, f"✅ تم العثور على {len(orders)} طلب(ات)")
                    for order in orders:
                        send_invoice_order(sender_id, order)
                        time.sleep(2)
                else:
                    send_whatsapp_message(sender_id, "❌ لم يتم العثور على طلبات")
            
            else:
                send_whatsapp_message(sender_id, "⚠️ أرسل ملف PDF فقط")
                
        elif msg.get('type') == 'text':
            send_whatsapp_message(sender_id, "📄 أرسل ملف PDF يحتوي على فواتير الطلبات")
            
    except Exception as e:
        print(f"Error: {str(e)}")
        
    return jsonify({"status": "ok"}), 200


@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    if request.method == 'GET':
        return "Webhook is active", 200
    return jsonify({"status": "received"}), 200


# ==================== تشغيل التطبيق ====================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
