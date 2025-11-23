import os
import json
import logging
import functions_framework
import requests
import vertexai
from googleapiclient.discovery import build
from vertexai.generative_models import GenerativeModel
from vertexai.preview.vision_models import ImageGenerationModel

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- HELPER: CLEAN TOKEN ---
def clean_token(raw_token):
    if not raw_token: return None
    return raw_token.replace('\n', '').replace('\t', '').replace(' ', '').strip()

# --- HELPER: GET USER URN (OIDC METHOD) ---
def get_user_urn(token):
    try:
        # We use userinfo which is the modern OpenID standard
        res = requests.get(
            "https://api.linkedin.com/v2/userinfo", 
            headers={"Authorization": f"Bearer {token}"}
        )
        if res.status_code != 200:
            logger.error(f"ID Fetch Failed: {res.text}")
            return None
        
        # The 'sub' field is the immutable User ID
        data = res.json()
        return f"urn:li:person:{data['sub']}"
    except Exception as e:
        logger.error(f"URN Error: {e}")
        return None

@functions_framework.http
def run_agent(request):
    try:
        logger.info("--- AGENT STARTING (PROTOCOL 2.0) ---")

        # ======================================================
        #       ### CONFIGURATION SECTION ###
        # ======================================================
        
        # 1. GOOGLE KEYS (Pre-Filled)
        api_key = "AIzaSyBKkz6Qj63buFzZ75-V930xE-fqO_gHdNQ"
        cse_id  = "95014ba35b61f4b38"
        
        # 2. YOUR DETAILS (PASTE HERE)
        project_id = "linkedin-agent-pro"
        raw_token  = "AQU9oLwJ5fn8JiUUGDzHYn7X5ZMTiOO6gOJlDFU10_yJ1M349RmQ1xIF1RZFYw2L9EbktP8SOUb7xpOZ7s-iMwfO9DfRjtxWwVDGIJwXJKSFTqyLAduxXHnLsAUeUPUyWIE-sOIznJxxuDcM_RC4EkAoKgPXNBZE10hFcbA6hqv1W8brLUNIjVIk76VpWYCiYwjLNy6Wfo8S8MsgiC7SFyFKY02EVJOpM-bs1h1sCPJmNn83YPHwc_ipoBlHmDwCt7gnxXkVfEiBNMQpjzwG0NhSv11m97pI-HVB8wNpohf1zHQR7Ssag1RdelGQ8fNol1hZ1HWIdsLIhzlALKwaTlfZuV7xcw"


        # ======================================================

        # 1. SETUP HEADERS (THE FIX)
        token = clean_token(raw_token)
        # LinkedIn requires this specific header for API v2 calls
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0" 
        }

        # 2. VERIFY IDENTITY
        logger.info("Verifying Identity...")
        urn = get_user_urn(token)
        if not urn:
            return {"error": "Invalid LinkedIn Token. Could not fetch User ID. Check if 'w_member_social' scope is enabled."}, 401
        logger.info(f"Posting as: {urn}")

        # 3. INIT AI & SEARCH
        vertexai.init(project=project_id, location="us-central1")
        
        logger.info("Searching Google...")
        try:
            service = build("customsearch", "v1", developerKey=api_key)
            res = service.cse().list(q="Artificial Intelligence latest Technology News about LLM models, new release, AGentic AI, Agents, Machine learning, Deep Learning", cx=cse_id, dateRestrict='d1', num=3).execute()
            news_text = "\n".join([f"- {i['title']}" for i in res.get('items', [])]) if 'items' in res else "General AI updates"
        except:
            news_text = "General AI software engineering trends"

        # 4. GENERATE CONTENT
        logger.info("Thinking...")
        model = GenerativeModel("gemini-2.5-pro")
        prompt = f"""
        ROLE: AI Tech Influencer. Latest NEWS: {news_text}
        TASK: Write LinkedIn post (>=290 words, with relatable title, stick to concept, more humanable, cover Tech part,at end "From byocX team) and Image Prompt (stick to content, Abstract 3D, more tech focus, use apporiate images).
        OUTPUT JSON: {{"post_text": "...", "image_prompt": "..."}}
        """
        response = model.generate_content(prompt)
        content = json.loads(response.text.replace("```json", "").replace("```", "").strip())

        # 5. GENERATE IMAGE
        img_path = "/tmp/image.png"
        img_model = ImageGenerationModel.from_pretrained("imagegeneration@006")
        try:
            img_model.generate_images(prompt=content['image_prompt'], number_of_images=1)[0].save(location=img_path)
        except:
            img_model.generate_images(prompt="Blue abstract tech background", number_of_images=1)[0].save(location=img_path)

        # 6. UPLOAD & POST
        # A. Register Upload
        reg_res = requests.post(
            "https://api.linkedin.com/v2/assets?action=registerUpload", 
            headers=headers, 
            json={
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                    "owner": urn,
                    "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}]
                }
            }
        )

        # Fallback Logic
        if reg_res.status_code != 200:
            logger.warning(f"Image Upload Failed: {reg_res.text}. Posting Text Only.")
            post_body = {
                "author": urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": content['post_text']},
                        "shareMediaCategory": "NONE"
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
            }
            final_res = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=post_body)
            
            if final_res.status_code != 201:
                return {"error": f"FINAL POST FAILED: {final_res.text}"}, 403
            return {"status": "success_text_only", "resp": final_res.json()}, 200

        # Upload Image Binary
        data = reg_res.json()
        upload_url = data['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
        asset = data['value']['asset']
        
        # Note: PUT requests for binary upload do NOT use the Restli Header, just Auth
        with open(img_path, 'rb') as f:
            requests.put(upload_url, headers={"Authorization": f"Bearer {token}"}, data=f)

        # Publish Image Post
        post_body = {
            "author": urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content['post_text']},
                    "shareMediaCategory": "IMAGE",
                    "media": [{"status": "READY", "media": asset, "title": {"text": "AI Update"}}]
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
        }
        
        final_res = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=post_body)
        return {"status": "success", "resp": final_res.json()}, 200

    except Exception as e:
        return {"error": str(e)}, 500
