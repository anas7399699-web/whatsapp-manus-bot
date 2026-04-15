import os
import requests
import time
from flask import Flask, request, jsonify
from process_orders import process_excel_orders_to_list
import tempfile

app = Flask(__name__)

# إعدادات WhatsApp Cloud API من بيئة التشغيل
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

def send_whatsapp_message(to, text):
    """إرسال رسالة نصية عبر WhatsApp API"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        return response.json()
    except Exception as e:
        print(f"Error sending message: {str(e)}")
        return None

def download_whatsapp_media(media_id):
    """تحميل الملف من WhatsApp API"""
    url = f"https://graph.facebook.com/v18.0/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    try:
        res = requests.get(url, headers=headers)
        media_url = res.json().get('url')
        
        if media_url:
            media_res = requests.get(media_url, headers=headers)
            return media_res.content
    except Exception as e:
        print(f"Error downloading media: {str(e)}")
    return None

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        return 'Forbidden', 403

    data = request.json
    if data.get('object') == 'whatsapp_business_account':
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                messages = value.get('messages', [])
                for msg in messages:
                    sender_id = msg.get('from')
                    
                    if msg.get('type') == 'document':
                        doc = msg.get('document')
                        mime_type = doc.get('mime_type', '')
                        filename = doc.get('filename', 'file.xlsx')
                        
                        if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
                            send_whatsapp_message(sender_id, "📥 تم استلام الملف. جاري معالجة الطلبات... ⏳")
                            
                            media_content = download_whatsapp_media(doc.get('id'))
                            if media_content:
                                with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_in:
                                    tmp_in.write(media_content)
                                    input_path = tmp_in.name
                                
                                # استخراج قاموس الرسائل
                                result = process_excel_orders_to_list(input_path)
                                
                                if result:
                                    riyadh_msgs = result.get("riyadh", [])
                                    others_msgs = result.get("others", [])
                                    total = len(riyadh_msgs) + len(others_msgs)
                                    
                                    if total > 0:
                                        # 1. إرسال طلبات الرياض أولاً
                                        if riyadh_msgs:
                                            send_whatsapp_message(sender_id, "📍 سأبدأ الآن بإرسال طلبات مدينة الرياض:")
                                            for i, r_msg in enumerate(riyadh_msgs, 1):
                                                send_whatsapp_message(sender_id, f"📦 طلب {i}/{len(riyadh_msgs)}:\n\n{r_msg}")
                                                time.sleep(0.5)
                                        
                                        # 2. إرسال طلبات باقي المناطق
                                        if others_msgs:
                                            send_whatsapp_message(sender_id, "📍 سأبدأ الآن بإرسال طلبات باقي المناطق:")
                                            for i, o_msg in enumerate(others_msgs, 1):
                                                send_whatsapp_message(sender_id, f"📦 طلب {i}/{len(others_msgs)}:\n\n{o_msg}")
                                                time.sleep(0.5)
                                        
                                        send_whatsapp_message(sender_id, "🏁 تم الانتهاء من إرسال جميع الطلبات بنجاح.")
                                    else:
                                        send_whatsapp_message(sender_id, "❌ لم أجد أي طلبات 'قيد التنفيذ' في هذا الملف.")
                                
                                if os.path.exists(input_path):
                                    os.remove(input_path)
                            else:
                                send_whatsapp_message(sender_id, "❌ فشل تحميل الملف.")
                        else:
                            send_whatsapp_message(sender_id, "⚠️ يرجى إرسال ملف بصيغة Excel (.xlsx) فقط.")
                    
                    elif msg.get('type') == 'text':
                        send_whatsapp_message(sender_id, "مرحباً! أرسل لي ملف Excel وسأقوم بفرز طلبات الرياض أولاً ثم باقي المناطق وإرسالها لك فرادى.")

    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
