import os
import json
import requests
import pandas as pd
from flask import Flask, request, jsonify
from openai import OpenAI
import sys

# إضافة مسار المهارة للوصول إلى سكربت المعالجة
sys.path.append('/home/ubuntu/skills/excel-order-processor/scripts')
from process_orders import process_excel_orders

app = Flask(__name__)

# الإعدادات (استناداً لبيانات المستخدم)
ACCESS_TOKEN = "EAALsdcSfLIYBRA8FqZAwfip2yxAsw9YchDbjf9gJhvGaF7oKAgmvnMeBwLWfMac5wxpOtGCJIWvNCMHcbXSBW2GxzZBnZCUoMEBUwCuOJqVpH3SMhIzVsD4ZCCTRHl52KHXT3KAY6UDJK2bsSBqKxAZCGUGP1x4UXV4UHJZB9MQzviNZAsaZAZAeUuZCgYkx1iVR0jvAZDZD"
PHONE_NUMBER_ID = "1124469584072164"
VERIFY_TOKEN = "ManusBot2026"
ALLOWED_NUMBER = "967739969981"
VERSION = "v17.0"

# إعداد عميل OpenAI
client = OpenAI()

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    # تقسيم الرسالة إذا كانت طويلة جداً (واتساب يدعم حتى 4096 حرف)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": chunk}
        }
        requests.post(url, headers=headers, json=data)

def get_ai_response(user_message):
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "أنت مساعد ذكي يعمل عبر واتساب. رد على المستخدم بلغة عربية واضحة ومفيدة."},
                {"role": "user", "content": user_message}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error calling AI: {e}")
        return "عذراً، حدث خطأ أثناء معالجة طلبك."

def download_whatsapp_media(media_id):
    url = f"https://graph.facebook.com/{VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    
    # الحصول على رابط التحميل
    res = requests.get(url, headers=headers)
    download_url = res.json().get("url")
    
    if download_url:
        # تحميل الملف الفعلي
        media_res = requests.get(download_url, headers=headers)
        file_path = f"/home/ubuntu/downloads/{media_id}.xlsx"
        os.makedirs("/home/ubuntu/downloads", exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(media_res.content)
        return file_path
    return None

@app.route("/", methods=["GET"])
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403

@app.route("/", methods=["POST"])
@app.route("/webhook", methods=["POST"])
def handle_messages():
    try:
        data = request.get_json()
        if not data or data.get("object") != "whatsapp_business_account":
            return "OK", 200

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    for message in value["messages"]:
                        sender_id = message["from"]
                        if sender_id != ALLOWED_NUMBER:
                            continue

                        # 1. معالجة الرسائل النصية
                        if message.get("type") == "text":
                            user_text = message["text"]["body"]
                            ai_reply = get_ai_response(user_text)
                            send_whatsapp_message(sender_id, ai_reply)

                        # 2. معالجة ملفات الإكسيل (Documents)
                        elif message.get("type") == "document":
                            doc = message["document"]
                            filename = doc.get("filename", "")
                            if filename.endswith(".xlsx") or filename.endswith(".xls"):
                                send_whatsapp_message(sender_id, "جاري استلام ملف الإكسيل ومعالجة الطلبات... يرجى الانتظار.")
                                
                                file_path = download_whatsapp_media(doc["id"])
                                if file_path:
                                    output_path = file_path.replace(".xlsx", ".txt")
                                    success = process_excel_orders(file_path, output_path)
                                    
                                    if success:
                                        with open(output_path, "r", encoding="utf-8") as f:
                                            result_text = f.read()
                                        
                                        # تقسيم النتائج بناءً على القسمين (الرياض وباقي المناطق)
                                        parts = result_text.split("\nطلبات باقي المناطق\n")
                                        riyadh_section = parts[0] if len(parts) > 0 else ""
                                        others_section = parts[1] if len(parts) > 1 else ""
                                        
                                        # 1. إرسال تنبيه بداية طلبات الرياض
                                        send_whatsapp_message(sender_id, "📍 بدأت الآن بإرسال طلبات مدينة الرياض:")
                                        
                                        # 2. إرسال طلبات الرياض
                                        riyadh_orders = riyadh_section.split("--------------------")
                                        for order in riyadh_orders:
                                            clean_order = order.replace("طلبات مدينة الرياض", "").replace("="*20, "").strip()
                                            if "رقم الطلبية" in clean_order:
                                                send_whatsapp_message(sender_id, clean_order)
                                        
                                        # 3. إرسال الفاصل وتنبيه بداية باقي المناطق
                                        send_whatsapp_message(sender_id, "----------------------------------------")
                                        send_whatsapp_message(sender_id, "📍 بدأت الآن بإرسال طلبات باقي المناطق:")
                                        
                                        # 4. إرسال طلبات باقي المناطق
                                        others_orders = others_section.split("--------------------")
                                        for order in others_orders:
                                            clean_order = order.replace("طلبات باقي المناطق", "").replace("="*20, "").strip()
                                            if "رقم الطلبية" in clean_order:
                                                send_whatsapp_message(sender_id, clean_order)
                                        
                                        send_whatsapp_message(sender_id, "✅ تم الانتهاء من إرسال كافة الطلبات.")
                                    else:
                                        send_whatsapp_message(sender_id, "عذراً، حدث خطأ أثناء معالجة بيانات الإكسيل. تأكد من تنسيق الملف.")
                                else:
                                    send_whatsapp_message(sender_id, "عذراً، فشل تحميل الملف من خوادم واتساب.")

        return "EVENT_RECEIVED", 200
    except Exception as e:
        print(f"Error: {e}")
        return "Error", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
